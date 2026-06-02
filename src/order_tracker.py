"""Bounded overwrite-only order ledger for guarded tiny-live Spot execution."""
import json
import os
import tempfile
import threading
import time
from collections import deque
from functools import wraps

from config import cfg


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'runtime'))
STATE_FILE = 'order_tracker_state.json'
RECENT_FILE = 'order_tracker_recent.json'
TERMINAL_STATUSES = {'FILLED', 'FAILED', 'EMERGENCY_DONE'}
BLOCKING_STATUSES = {'PARTIAL_RISK', 'EMERGENCY_PENDING', 'EMERGENCY_FAILED'}
ACTIVE_STATUSES = {'INIT', 'SUBMITTED', 'WAITING_FILL', 'PARTIAL_FILLED'}
SENSITIVE_KEYS = {'access_key', 'secret_key', 'api_key', 'api_secret', 'signature', 'authorization'}


def _read_json(name: str) -> dict:
    try:
        with open(os.path.join(RUNTIME_DIR, name), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_json(name: str, data) -> None:
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=RUNTIME_DIR, prefix=name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, os.path.join(RUNTIME_DIR, name))
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _sanitize(value):
    if isinstance(value, dict):
        return {
            key: ('[REDACTED]' if key.lower() in SENSITIVE_KEYS else _sanitize(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and any(token in value.lower() for token in ('signature=', 'authorization:', 'api_secret', 'secret_key')):
        return '[REDACTED]'
    return value


def _leg(venue: str, side: str, requested_qty=0, requested_quote=0) -> dict:
    return {
        'venue': venue, 'side': side, 'client_order_ref': '', 'exchange_order_id': '',
        'requested_qty': float(requested_qty or 0), 'requested_quote': float(requested_quote or 0),
        'filled_qty': 0.0, 'avg_price': 0.0, 'fee': 0.0, 'status': 'INIT',
        'submitted_at': 0.0, 'filled_at': 0.0, 'raw_response_sanitized': {},
    }


def _state_locked(save=False):
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            with self._lock:
                result = method(self, *args, **kwargs)
            if save:
                self.save()
            return result
        return wrapped
    return decorate


class OrderTracker:
    def __init__(self):
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self.state = _read_json(STATE_FILE)
        recent = _read_json(RECENT_FILE).get('events', [])
        self._recent = deque(recent, maxlen=cfg.order_tracker_recent_max_items)

    @_state_locked(save=True)
    def start_plan(self, plan: dict) -> dict:
        now = time.time()
        qty = float(plan.get('normalized_qty', plan.get('qty', 0)) or 0)
        left_venue = plan.get('left_venue', 'UPBIT')
        right_venue = plan.get('right_venue', 'BINANCE')
        left_side = plan.get('left_side') or plan.get('upbit_side', '')
        right_side = plan.get('right_side') or plan.get('binance_side', '')
        self.state = {
            'plan_id': plan.get('plan_id', ''), 'symbol': plan.get('symbol', ''),
            'pair_id': plan.get('pair_id', 'UPBIT_BINANCE'),
            'direction_label': plan.get('direction_label', ''), 'status': 'INIT',
            'created_at': now, 'updated_at': now,
            'left_leg': _leg(left_venue, left_side, qty, plan.get('order_krw', 0)),
            'right_leg': _leg(right_venue, right_side, qty, plan.get('order_usdt') or plan.get('order_krw', 0)),
            'net_filled_qty': 0.0, 'exposure_qty': 0.0, 'exposure_side': 'FLAT',
            'exposure_notional_krw': 0.0, 'failed_leg': '', 'filled_leg': '',
            'emergency_required': False, 'emergency_attempted': False, 'emergency_done': False,
            'emergency_strategy': cfg.emergency_strategy, 'emergency_status': 'NOT_REQUIRED',
            'emergency_order_ids': [], 'emergency_result': {},
            'emergency_manual_action': '', 'suggested_manual_action': '',
        }
        self._event('START_PLAN')
        return self.to_dict()

    @_state_locked(save=True)
    def mark_submitted(self, venue: str, response: dict) -> dict:
        leg = self._get_leg(venue)
        response = response or {}
        leg['status'] = 'SUBMITTED'
        leg['submitted_at'] = leg['submitted_at'] or time.time()
        leg['exchange_order_id'] = str(response.get('uuid') or response.get('orderId') or '')
        leg['client_order_ref'] = str(response.get('identifier') or response.get('clientOrderId') or '')
        leg['raw_response_sanitized'] = _sanitize(response)
        self.state['status'] = 'SUBMITTED'
        self._event('SUBMITTED', venue)
        return self.to_dict()

    @_state_locked(save=True)
    def mark_filled(self, venue: str, fill_result: dict) -> dict:
        leg = self._get_leg(venue)
        fill_result = fill_result or {}
        order = fill_result.get('order') or {}
        ratio = float(fill_result.get('fill_ratio', 0) or 0)
        filled_qty = (
            order.get('executed_volume') or order.get('executedQty')
            or float(leg.get('requested_qty', 0) or 0) * ratio
        )
        leg['filled_qty'] = float(filled_qty or 0)
        leg['avg_price'] = float(order.get('avg_price') or order.get('price') or 0)
        leg['fee'] = float(order.get('paid_fee') or order.get('commission') or 0)
        leg['filled_at'] = time.time() if fill_result.get('filled') else 0.0
        leg['status'] = 'FILLED' if fill_result.get('filled') else ('PARTIAL_FILLED' if ratio > 0 else 'WAITING_FILL')
        leg['raw_response_sanitized'] = _sanitize(fill_result)
        self._event(leg['status'], venue)
        self.compute_exposure()
        return self.to_dict()

    @_state_locked(save=True)
    def mark_waiting_fill(self, venue: str) -> dict:
        leg = self._get_leg(venue)
        leg['status'] = 'WAITING_FILL'
        self.state['status'] = 'WAITING_FILL'
        self._event('WAITING_FILL', venue)
        return self.to_dict()

    @_state_locked(save=True)
    def mark_failed(self, venue: str, error) -> dict:
        leg = self._get_leg(venue)
        leg['status'] = 'FAILED'
        leg['raw_response_sanitized'] = {'error': str(error)}
        self._event('FAILED', venue)
        self.compute_exposure()
        return self.to_dict()

    @_state_locked(save=True)
    def mark_timeout(self, venue: str) -> dict:
        leg = self._get_leg(venue)
        if leg.get('status') != 'FILLED':
            leg['status'] = 'PARTIAL_FILLED' if leg.get('filled_qty', 0) else 'FAILED'
        self._event('TIMEOUT', venue)
        self.compute_exposure()
        return self.to_dict()

    @_state_locked()
    def compute_exposure(self) -> dict:
        left, right = self._legs()
        signed = sum(
            (1 if leg.get('side') == 'BUY' else -1) * float(leg.get('filled_qty', 0) or 0)
            for leg in (left, right)
        )
        self.state['net_filled_qty'] = round(min(
            float(left.get('filled_qty', 0) or 0), float(right.get('filled_qty', 0) or 0)), 12)
        self.state['exposure_qty'] = round(abs(signed), 12)
        self.state['exposure_side'] = 'LONG' if signed > 0 else 'SHORT' if signed < 0 else 'FLAT'
        filled_leg = max((left, right), key=lambda leg: float(leg.get('filled_qty', 0) or 0))
        failed_leg = min((left, right), key=lambda leg: float(leg.get('filled_qty', 0) or 0))
        self.state['filled_leg'] = filled_leg.get('venue', '') if self.state['exposure_qty'] else ''
        self.state['failed_leg'] = failed_leg.get('venue', '') if self.state['exposure_qty'] else ''
        self.state['exposure_notional_krw'] = round(
            self.state['exposure_qty'] * float(filled_leg.get('avg_price', 0) or 0), 2
        )
        if self.is_partial_risk():
            self.state['status'] = 'PARTIAL_RISK'
            self.state['emergency_required'] = True
            self.state['emergency_status'] = 'EMERGENCY_REQUIRED'
        elif all(leg.get('status') == 'FILLED' for leg in (left, right)):
            self.state['status'] = 'FILLED'
            self.state['emergency_required'] = False
            self.state['emergency_status'] = 'NOT_REQUIRED'
            self.state['failed_leg'] = ''
            self.state['filled_leg'] = ''
            self.state['exposure_notional_krw'] = 0.0
        self.state['updated_at'] = time.time()
        return self.to_dict()

    @_state_locked()
    def is_partial_risk(self) -> bool:
        legs = list(self._legs())
        filled = [float(leg.get('filled_qty', 0) or 0) for leg in legs]
        statuses = {leg.get('status') for leg in legs}
        return (
            abs(filled[0] - filled[1]) > 1e-12
            or ('FAILED' in statuses and max(filled) > 0)
            or 'PARTIAL_FILLED' in statuses
        )

    @_state_locked(save=True)
    def require_emergency(self) -> dict:
        self.state['emergency_required'] = True
        self.state['status'] = 'EMERGENCY_PENDING'
        self._event('EMERGENCY_PENDING')
        return self.to_dict()

    @_state_locked(save=True)
    def mark_partial_risk(self, suggested_manual_action: str) -> dict:
        self.state['status'] = 'PARTIAL_RISK'
        self.state['emergency_required'] = True
        self.state['suggested_manual_action'] = suggested_manual_action
        self.state['emergency_manual_action'] = suggested_manual_action
        self.state['emergency_status'] = 'EMERGENCY_REQUIRED'
        self._event('PARTIAL_RISK')
        return self.to_dict()

    @_state_locked(save=True)
    def mark_emergency_attempted(self, emergency_plan=None) -> dict:
        today = time.strftime('%Y-%m-%d')
        if self.state.get('emergency_attempt_date') != today:
            self.state['emergency_attempts_today'] = 0
        self.state['status'] = 'EMERGENCY_PENDING'
        self.state['emergency_attempted'] = True
        self.state['emergency_attempts_today'] = int(self.state.get('emergency_attempts_today', 0) or 0) + 1
        self.state['emergency_attempt_date'] = today
        self.state['emergency_status'] = 'EMERGENCY_PENDING'
        self.state['emergency_strategy'] = (emergency_plan or {}).get('strategy', cfg.emergency_strategy)
        self._event('EMERGENCY_ATTEMPTED')
        return self.to_dict()

    @_state_locked(save=True)
    def mark_emergency_result(self, ok: bool, result=None) -> dict:
        result = result or {}
        self.state['status'] = 'EMERGENCY_DONE' if ok else 'EMERGENCY_FAILED'
        self.state['emergency_status'] = self.state['status']
        self.state['emergency_done'] = bool(ok)
        self.state['emergency_required'] = not ok
        self.state['emergency_result'] = _sanitize(result)
        response = result.get('response', {})
        order_id = str(response.get('exchange_order_id') or '')
        if order_id:
            self.state['emergency_order_ids'] = [*self.state.get('emergency_order_ids', []), order_id]
        self._event(self.state['status'], detail=str(result.get('error', '')))
        return self.to_dict()

    @_state_locked(save=True)
    def set_emergency_preview(self, check: dict, manual_action: str) -> dict:
        plan = check.get('plan') or {}
        self.state['emergency_required'] = True
        self.state['emergency_strategy'] = plan.get('strategy', cfg.emergency_strategy)
        self.state['emergency_status'] = 'EMERGENCY_REQUIRED'
        self.state['emergency_manual_action'] = manual_action
        self.state['suggested_manual_action'] = manual_action
        if float(plan.get('order_krw', 0) or 0) > 0:
            self.state['exposure_notional_krw'] = round(float(plan.get('order_krw', 0) or 0), 2)
        self._event('EMERGENCY_PREVIEW')
        return self.to_dict()

    @_state_locked(save=True)
    def manual_clear(self, reason: str) -> dict:
        if not reason.strip():
            raise ValueError('CLEARING_REASON_REQUIRED')
        if self.state.get('status') not in BLOCKING_STATUSES and not self.state.get('emergency_required'):
            raise ValueError('PARTIAL_RISK_NOT_ACTIVE')
        self._event('MANUAL_CLEAR', detail=reason.strip())
        self.state = {
            **self.state, 'status': 'DISARMED', 'updated_at': time.time(),
            'emergency_required': False, 'emergency_done': True,
            'emergency_status': 'MANUALLY_CLEARED',
            'suggested_manual_action': f'Manually cleared after operator review: {reason.strip()}',
        }
        return self.to_dict()

    @_state_locked()
    def to_dict(self) -> dict:
        return _sanitize(dict(self.state))

    def save(self) -> None:
        with self._save_lock:
            with self._lock:
                now = time.time()
                if self.state:
                    self.state['updated_at'] = now
                state = _sanitize(dict(self.state))
                recent = {'updated_at': now, 'events': list(self._recent)}
            _write_json(STATE_FILE, state)
            _write_json(RECENT_FILE, recent)

    @_state_locked()
    def recent(self) -> list[dict]:
        return list(reversed(self._recent))

    def _get_leg(self, venue: str) -> dict:
        if not self.state:
            raise RuntimeError('ORDER_LEDGER_UNSYNCED')
        for leg in self._legs():
            if leg.get('venue', '').upper() == venue.upper():
                return leg
        raise RuntimeError(f'ORDER_LEDGER_UNSYNCED: {venue}')

    def _legs(self) -> tuple[dict, dict]:
        left = self.state.get('left_leg') or self.state.get('upbit_leg', {})
        right = self.state.get('right_leg') or self.state.get('binance_leg', {})
        return left, right

    def _event(self, event: str, venue='', detail='') -> None:
        self._recent.append({
            'time': time.time(), 'plan_id': self.state.get('plan_id', ''),
            'event': event, 'venue': venue, 'detail': detail,
            'status': self.state.get('status', ''),
        })
