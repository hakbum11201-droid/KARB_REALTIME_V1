"""Guarded tiny-live Spot execution. Withdrawals and transfers are intentionally absent."""
import json
import os
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from config import cfg
from exchange_clients import BinanceSpotPrivateClient, UpbitPrivateClient
from bithumb_private import BithumbPrivateClient
from execution_plan import ExecutionPlan, build_execution_plan
from emergency_liquidator import EmergencyLiquidator
from inventory_manager import InventoryManager
from order_tracker import ACTIVE_STATUSES, BLOCKING_STATUSES, OrderTracker
from risk_guard import RiskGuard
from secrets_manager import get_api_permission_policy, get_key_status


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'runtime'))
STATUS_FILE = 'tiny_live_status.json'
PREFLIGHT_FILE = 'tiny_live_last_preflight.json'
ORDER_FILE = 'tiny_live_last_order.json'


def _read_json(name: str) -> dict:
    try:
        with open(os.path.join(RUNTIME_DIR, name), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_json(name: str, data: dict):
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=RUNTIME_DIR, prefix=name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, os.path.join(RUNTIME_DIR, name))
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _unique(values):
    return list(dict.fromkeys(values))


def _num(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _status() -> dict:
    stored = _read_json(STATUS_FILE)
    today = date.today().isoformat()
    if stored.get('trade_date') != today:
        stored['trade_date'] = today
        stored['trade_count'] = 0
        stored['daily_loss_krw'] = 0
    return {
        'armed': False,
        'status': 'DISARMED',
        'partial_risk': False,
        'trade_date': today,
        'trade_count': 0,
        'daily_loss_krw': 0,
        'calibration_session_submit_count': 0,
        'calibration_session_success_count': 0,
        'calibration_session_fail_count': 0,
        'last_error': '',
        'blockers': [],
        'warnings': [],
        'updated_at': time.time(),
        **stored,
    }


def _write_status(**updates) -> dict:
    status = {**_status(), **updates, 'updated_at': time.time()}
    _write_json(STATUS_FILE, status)
    return status


def _public_inventory_summary(quotes: dict) -> dict:
    return InventoryManager().inventory_summary(quotes=quotes, mode=cfg.mode)


def get_inventory_summary(pair_id='UPBIT_BINANCE') -> dict:
    if pair_id == 'UPBIT_BITHUMB':
        snapshot = _read_json('latest_opportunities.json')
        return InventoryManager().upbit_bithumb_inventory_summary(
            snapshot.get('all_opportunities', []), mode=cfg.mode
        )
    return _public_inventory_summary(_read_json('latest_quotes.json'))


def _base_blockers(pair_id='UPBIT_BINANCE') -> list[str]:
    keys = get_key_status()
    blockers = []
    if keys['UPBIT_ACCESS_KEY'] != 'Set' or keys['UPBIT_SECRET_KEY'] != 'Set':
        blockers.append('UPBIT_KEY_MISSING')
    if pair_id == 'UPBIT_BINANCE' and (keys['BINANCE_API_KEY'] != 'Set' or keys['BINANCE_API_SECRET'] != 'Set'):
        blockers.append('BINANCE_KEY_MISSING')
    if pair_id == 'UPBIT_BITHUMB':
        if keys['BITHUMB_ACCESS_KEY'] != 'Set' or keys['BITHUMB_SECRET_KEY'] != 'Set':
            blockers.append('BITHUMB_KEY_MISSING')
        if not cfg.bithumb_private_enabled:
            blockers.append('BITHUMB_PRIVATE_DISABLED')
        if not cfg.upbit_bithumb_live_enabled:
            blockers.append('UPBIT_BITHUMB_LIVE_DISABLED')
        if cfg.upbit_bithumb_order_krw < cfg.bithumb_min_order_krw:
            blockers.extend(['MIN_ORDER_FAIL', 'BITHUMB_MIN_ORDER_KRW'])
        if cfg.upbit_bithumb_order_krw > cfg.upbit_bithumb_max_order_krw:
            blockers.extend(['MIN_ORDER_FAIL', 'UPBIT_BITHUMB_MAX_ORDER_EXCEEDED'])
    if pair_id not in ('UPBIT_BINANCE', 'UPBIT_BITHUMB'):
        blockers.append('PAIR_DISABLED')
    if any(item.endswith('_KEY_MISSING') for item in blockers):
        blockers.append('KEY_MISSING')
    if cfg.mode != 'tiny_live':
        blockers.append('MODE_GUARD')
    if not cfg.enable_live_trading:
        blockers.append('CONFIG_LIVE_DISABLED')
        blockers.append('ENABLE_LIVE_TRADING_FALSE')
    if not cfg.tiny_live_enabled:
        blockers.append('TINY_LIVE_DISABLED')
    if not cfg.live_order_enabled:
        blockers.append('LIVE_ORDER_ENABLED_FALSE')
    if cfg.withdrawals_enabled:
        blockers.append('WITHDRAWALS_MUST_REMAIN_DISABLED')
    if cfg.futures_hedge_enabled:
        blockers.append('FUTURES_HEDGE_MUST_REMAIN_DISABLED')
    if not cfg.manual_rebalance_only:
        blockers.append('MANUAL_REBALANCE_ONLY_REQUIRED')
    if cfg.tiny_live_order_krw < 5000:
        blockers.extend(['MIN_ORDER_FAIL', 'UPBIT_MIN_ORDER_KRW'])
    if cfg.tiny_live_order_krw > cfg.tiny_live_max_order_krw:
        blockers.extend(['MIN_ORDER_FAIL', 'TINY_LIVE_MAX_ORDER_EXCEEDED'])
    status = _status()
    if cfg.block_new_entries_on_partial_risk and status.get('partial_risk'):
        blockers.append('PARTIAL_RISK_ACTIVE')
    if int(status.get('trade_count', 0)) >= cfg.tiny_live_max_trades_per_day:
        blockers.append('MAX_TRADES_LIMIT')
    if float(status.get('daily_loss_krw', 0) or 0) >= cfg.tiny_live_daily_loss_limit_krw:
        blockers.append('DAILY_LOSS_LIMIT')
    if cfg.order_tracker_enabled:
        tracker_state = OrderTracker().to_dict()
        tracker_status = tracker_state.get('status')
        if tracker_status in BLOCKING_STATUSES:
            blockers.append(tracker_status)
        elif tracker_status in ACTIVE_STATUSES:
            blockers.append('ORDER_TRACKER_ACTIVE')
        if tracker_state.get('emergency_required') and not tracker_state.get('emergency_done'):
            blockers.append('PARTIAL_RISK_ACTIVE')
        blockers.extend(RiskGuard.live_order_blockers(tracker_state))
    return _unique(blockers)


def get_tiny_live_readiness(pair_id='UPBIT_BINANCE') -> dict:
    perf = _read_json('performance_summary.json')
    last_session = _read_json('last_session_summary.json')
    quotes = _read_json('latest_quotes.json')
    blockers = _base_blockers(pair_id)
    warnings = ['WITHDRAWALS_DISABLED_BY_POLICY', 'MANUAL_REBALANCE_ONLY']
    if cfg.require_paper_pass_for_tiny_live and last_session.get('judgement') != 'PAPER_EDGE_PASS':
        blockers.append('PAPER_PASS_REQUIRED')
    if int(perf.get('closed_trade_count', 0)) < cfg.min_paper_closed_trades_for_tiny_live:
        blockers.append('NOT_ENOUGH_PAPER_TRADES')
    if float(perf.get('net_pnl_krw', 0)) <= cfg.min_paper_net_pnl_krw_for_tiny_live:
        blockers.append('PAPER_NET_PNL_TOO_LOW')
    if float(perf.get('win_rate', 0)) < cfg.min_paper_win_rate_for_tiny_live * 100:
        blockers.append('PAPER_WIN_RATE_TOO_LOW')
    if float(perf.get('avg_pnl_krw', 0)) <= 0:
        blockers.append('PAPER_AVG_PNL_TOO_LOW')
    inventory = get_inventory_summary(pair_id) if not blockers else {}
    blockers.extend(inventory.get('blockers', []))
    return {
        'ready': not blockers,
        'pair_id': pair_id,
        'blockers': _unique(blockers),
        'warnings': warnings,
        'next_action': 'Review blockers, then arm tiny-live explicitly.' if blockers else 'Preflight may proceed.',
        'key_status': get_key_status(),
        'permission_policy': get_api_permission_policy(),
        'inventory': inventory,
        'status': _status(),
        'limits': {
            'tiny_live_order_krw': cfg.tiny_live_order_krw,
            'tiny_live_max_order_krw': cfg.tiny_live_max_order_krw,
            'upbit_bithumb_order_krw': cfg.upbit_bithumb_order_krw,
            'upbit_bithumb_max_order_krw': cfg.upbit_bithumb_max_order_krw,
        },
    }


def _calibration_cfg() -> dict:
    row = cfg.tiny_live_calibration
    return row if isinstance(row, dict) else {}


def _candidate_rows(pair_id='UPBIT_BITHUMB') -> list[dict]:
    snapshot = _read_json('latest_opportunities.json')
    rows = [
        row for row in snapshot.get('all_opportunities', [])
        if isinstance(row, dict) and row.get('pair_id', 'UPBIT_BINANCE') == pair_id
    ]
    rows.sort(key=lambda row: _num(row.get('best_net_surplus_bp'), -999999), reverse=True)
    return rows


def _candidate_reject_reasons(row: dict, pair_id: str, allowed_pairs: set, allowed_symbols: set, max_order: float) -> list[str]:
    reasons = []
    symbol = str(row.get('symbol', '')).upper()
    if allowed_pairs and pair_id not in allowed_pairs:
        reasons.append('PAIR_NOT_ALLOWED')
    if allowed_symbols and symbol not in allowed_symbols:
        reasons.append('SYMBOL_NOT_ALLOWED')
    if _num(row.get('selected_notional_krw', row.get('order_krw_used')), max_order) > max_order:
        reasons.append('MAX_ORDER_EXCEEDED')
    if _num(row.get('net_expected_profit_krw', row.get('expected_net_profit_krw'))) <= 0:
        reasons.append('EXPECTED_NET_NOT_POSITIVE')
    if row.get('depth_ok') is False:
        reasons.append('DEPTH_INSUFFICIENT')
    if _num(row.get('expected_fill_ratio_buy'), 1.0) < cfg.min_fill_ratio:
        reasons.append('BUY_FILL_RATIO_LOW')
    if _num(row.get('expected_fill_ratio_sell'), 1.0) < cfg.min_fill_ratio:
        reasons.append('SELL_FILL_RATIO_LOW')
    if row.get('leg_freshness_ok') is False:
        reasons.append(row.get('leg_freshness_blocker') or 'LEG_QUOTE_TOO_OLD')
    if any(bool(row.get(name)) for name in ('stale', 'stale_grace', 'has_stale_quote')):
        reasons.append('STALE_QUOTE')
    direction = row.get('best_direction') or row.get('direction', '')
    if pair_id == 'UPBIT_BITHUMB' and direction not in ('UPBIT_BITHUMB_A', 'UPBIT_BITHUMB_B'):
        reasons.append('DIRECTION_UNAVAILABLE')
    if pair_id == 'UPBIT_BINANCE' and direction not in ('A', 'B'):
        reasons.append('DIRECTION_UNAVAILABLE')
    return list(dict.fromkeys(reasons))


def _preflight_selection_stats(rows: list[dict], allowed_symbols: set, eligible: list[dict]) -> dict:
    allowed_rows = [
        row for row in rows
        if not allowed_symbols or str(row.get('symbol', '')).upper() in allowed_symbols
    ]
    positive_rows = [
        row for row in allowed_rows
        if _num(row.get('net_expected_profit_krw', row.get('expected_net_profit_krw'))) > 0
    ]
    fresh_rows = [
        row for row in positive_rows
        if row.get('leg_freshness_ok') is not False
        and not any(bool(row.get(name)) for name in ('stale', 'stale_grace', 'has_stale_quote'))
    ]
    return {
        'scanned_count': len(rows),
        'allowed_filtered_count': len(allowed_rows),
        'positive_net_count': len(positive_rows),
        'fresh_count': len(fresh_rows),
        'eligible_count': len(eligible),
    }


def _debug_candidate(row: dict, reasons: list[str]) -> dict:
    return {
        'symbol': row.get('symbol', ''),
        'pair_id': row.get('pair_id', ''),
        'direction': row.get('best_direction') or row.get('direction', ''),
        'expected_net_profit_krw': row.get('net_expected_profit_krw', row.get('expected_net_profit_krw')),
        'max_leg_quote_age_ms': row.get('max_leg_quote_age_ms'),
        'leg_quote_age_cap_ms': row.get('leg_quote_age_cap_ms'),
        'depth_ok': row.get('depth_ok'),
        'reason': next(iter(reasons), row.get('reason_no_trade', 'UNKNOWN')),
        'reject_reasons': reasons[:5],
    }


def _select_preflight_candidate(pair_id='UPBIT_BITHUMB', candidate=None) -> tuple[dict | None, dict]:
    cal = _calibration_cfg()
    allowed_pairs = set(cal.get('allowed_pairs') or [])
    allowed_symbols = {str(item).upper() for item in (cal.get('allowed_symbols') or [])}
    max_order = _num(cal.get('max_order_krw'), 10000)
    if isinstance(candidate, dict) and candidate.get('symbol'):
        rows = [dict(candidate)]
    else:
        rows = _candidate_rows(pair_id)
    eligible = []
    debug = []
    for row in rows:
        reasons = _candidate_reject_reasons(row, pair_id, allowed_pairs, allowed_symbols, max_order)
        if reasons:
            if len(debug) < 5:
                debug.append(_debug_candidate(row, reasons))
            continue
        plan = build_execution_plan({
            **row,
            'entry_reason': (
                row.get('entry_reason')
                or 'NORMAL_GO' if row.get('reason_no_trade') == 'OK' else 'UNKNOWN'
            ),
        }, 'tiny_live', cfg)
        plan_reasons = []
        if not plan:
            plan_reasons.append('EXECUTION_PLAN_UNAVAILABLE')
        elif not plan.get('plan_ok', False):
            plan_reasons.extend(plan.get('execution_plan_blockers') or [plan.get('blocker') or 'EXECUTION_PLAN_BLOCKED'])
        if plan and not plan.get('depth_ok', False):
            plan_reasons.append('DEPTH_INSUFFICIENT')
        if plan and _num(plan.get('expected_net_profit_krw')) <= 0:
            plan_reasons.append('EXPECTED_NET_NOT_POSITIVE')
        if plan and not plan.get('leg_freshness_ok', True):
            plan_reasons.append(plan.get('leg_freshness_blocker') or 'LEG_QUOTE_TOO_OLD')
        if plan and (
            _num(plan.get('expected_fill_ratio_buy'), 1.0) < cfg.min_fill_ratio
            or _num(plan.get('expected_fill_ratio_sell'), 1.0) < cfg.min_fill_ratio
        ):
            plan_reasons.append('FILL_RATIO_LOW')
        if plan_reasons:
            if len(debug) < 5:
                debug.append(_debug_candidate({**row, **plan}, list(dict.fromkeys(plan_reasons))))
            continue
        eligible.append({**row, '_preflight_plan': plan})
    def plan_value(row, key, default=None):
        value = row.get('_preflight_plan', {}).get(key)
        return row.get(key, default) if value is None else value

    def sort_key(row):
        return (
            _num(plan_value(row, 'expected_net_profit_krw', row.get('net_expected_profit_krw'))),
            -_num(plan_value(row, 'max_leg_quote_age_ms', row.get('max_leg_quote_age_ms')), 999999),
            -_num(plan_value(row, 'entry_decision_wait_ms', row.get('entry_decision_wait_ms')), 999999),
            _num(plan_value(row, 'expected_fill_ratio_buy', row.get('expected_fill_ratio_buy')), 1.0)
            + _num(plan_value(row, 'expected_fill_ratio_sell', row.get('expected_fill_ratio_sell')), 1.0),
        )
    eligible.sort(key=sort_key, reverse=True)
    selection = _preflight_selection_stats(rows, allowed_symbols, eligible)
    selection['debug_candidates'] = debug
    return (dict(eligible[0]) if eligible else None), selection


def _legacy_preflight_candidate(pair_id='UPBIT_BITHUMB') -> dict:
    rows = _candidate_rows(pair_id)
    actionable = [
        row for row in rows
        if row.get('reason_no_trade') == 'OK'
        or row.get('go_no_go') == 'GO'
        or row.get('stale_recheck_status') == 'RECHECK_ACTIONABLE_FAST_PASS'
        or row.get('wide_spread_recheck_status') == 'WIDE_SPREAD_RECHECK_ACTIONABLE'
        or row.get('entry_reason') in ('NORMAL_GO', 'RECHECK_ACTIONABLE', 'WIDE_SPREAD_RECHECK_ACTIONABLE')
    ]
    return dict((actionable or rows or [{}])[0])


def _balance_sources(pair_id: str, should_check: bool):
    if not should_check:
        return {}, {}, []
    keys = get_key_status()
    blockers = []
    if keys['UPBIT_ACCESS_KEY'] != 'Set' or keys['UPBIT_SECRET_KEY'] != 'Set':
        blockers.append('UPBIT_KEY_MISSING')
    if pair_id == 'UPBIT_BITHUMB':
        if keys['BITHUMB_ACCESS_KEY'] != 'Set' or keys['BITHUMB_SECRET_KEY'] != 'Set':
            blockers.append('BITHUMB_KEY_MISSING')
    elif keys['BINANCE_API_KEY'] != 'Set' or keys['BINANCE_API_SECRET'] != 'Set':
        blockers.append('BINANCE_KEY_MISSING')
    if blockers:
        return {}, {}, [*blockers, 'LIVE_API_KEY_MISSING']
    try:
        upbit = UpbitPrivateClient().get_balances()
        right = BithumbPrivateClient().get_balances() if pair_id == 'UPBIT_BITHUMB' else BinanceSpotPrivateClient().get_balances()
    except Exception:
        return {}, {}, ['BALANCE_CHECK_FAILED']
    blockers = list(upbit.get('blockers', [])) + list(right.get('blockers', []))
    if blockers:
        blockers.append('BALANCE_CHECK_FAILED')
    return upbit.get('balances', {}), right.get('balances', {}), blockers


def _required_assets_for_plan(plan: dict, candidate: dict, pair_id: str, direction: str) -> tuple[dict, dict]:
    symbol = plan.get('symbol') or candidate.get('symbol', '')
    qty = _num(plan.get('selected_qty', plan.get('normalized_qty', plan.get('qty'))))
    notional = _num(plan.get('selected_notional_krw', plan.get('order_krw', plan.get('order_krw_used'))))
    buy_fee = _num(plan.get('buy_fee_krw'))
    fx = _num(plan.get('krw_usdt', candidate.get('krw_usdt')), 1.0) or 1.0
    required = {}
    labels = {}
    if pair_id == 'UPBIT_BITHUMB':
        if direction == 'UPBIT_BITHUMB_A':
            required = {
                'BITHUMB.KRW': notional + buy_fee,
                f'UPBIT.{symbol}': qty,
            }
        else:
            required = {
                'UPBIT.KRW': notional + buy_fee,
                f'BITHUMB.{symbol}': qty,
            }
    elif direction == 'A':
        required = {
            f'UPBIT.{symbol}': qty,
            'BINANCE.USDT': (notional + buy_fee) / fx,
        }
    else:
        required = {
            'UPBIT.KRW': notional + buy_fee,
            f'BINANCE.{symbol}': qty,
        }
    for key, value in required.items():
        labels[key] = round(value, 10)
    return required, labels


def _available_assets(pair_id: str, symbol: str, upbit_balances: dict, right_balances: dict) -> dict:
    if pair_id == 'UPBIT_BITHUMB':
        return {
            'UPBIT.KRW': _num(upbit_balances.get('KRW')),
            f'UPBIT.{symbol}': _num(upbit_balances.get(symbol)),
            'BITHUMB.KRW': _num(right_balances.get('KRW')),
            f'BITHUMB.{symbol}': _num(right_balances.get(symbol)),
        }
    return {
        'UPBIT.KRW': _num(upbit_balances.get('KRW')),
        f'UPBIT.{symbol}': _num(upbit_balances.get(symbol)),
        'BINANCE.USDT': _num(right_balances.get('USDT')),
        f'BINANCE.{symbol}': _num(right_balances.get(symbol)),
    }


def _balance_result(required: dict, available: dict) -> tuple[bool, list[dict]]:
    missing = []
    for key, need in required.items():
        have = _num(available.get(key))
        if have + 1e-12 < _num(need):
            missing.append({'asset': key, 'required': round(_num(need), 10), 'available': round(have, 10)})
    return not missing, missing


def build_tiny_live_preflight(pair_id='UPBIT_BITHUMB', candidate=None, check_balances=True) -> dict:
    cal = _calibration_cfg()
    pair_id = pair_id or 'UPBIT_BITHUMB'
    config_blockers = []
    candidate_blockers = []
    balance_blockers = []
    executor_blockers = []
    risk_blockers = []
    warnings = ['READ_ONLY_PREFLIGHT', 'NO_ORDER_PLACED']
    selected, candidate_selection = _select_preflight_candidate(pair_id, candidate)
    if not selected:
        candidate_blockers.append('NO_ELIGIBLE_CANDIDATE')
    selected = selected or {}
    symbol = str(selected.get('symbol', '')).upper()
    direction = selected.get('best_direction') or selected.get('direction', '')
    entry_reason = selected.get('entry_reason') or (
        'RECHECK_ACTIONABLE' if selected.get('stale_recheck_status') == 'RECHECK_ACTIONABLE_FAST_PASS'
        else 'WIDE_SPREAD_RECHECK_ACTIONABLE' if selected.get('wide_spread_recheck_status') == 'WIDE_SPREAD_RECHECK_ACTIONABLE'
        else 'NORMAL_GO' if selected.get('reason_no_trade') == 'OK'
        else 'UNKNOWN'
    )
    allowed_pairs = set(cal.get('allowed_pairs') or [])
    allowed_symbols = {str(item).upper() for item in (cal.get('allowed_symbols') or [])}
    if not cfg.tiny_live_enabled:
        config_blockers.append('TINY_LIVE_DISABLED')
    if not cal.get('enabled', False):
        config_blockers.append('CALIBRATION_DISABLED')
    if not cfg.enable_live_trading:
        config_blockers.append('CONFIG_LIVE_DISABLED')
    if not cfg.live_order_enabled:
        config_blockers.append('LIVE_ORDER_ENABLED_FALSE')
    if pair_id == 'UPBIT_BITHUMB':
        if not cfg.bithumb_private_enabled:
            config_blockers.append('BITHUMB_PRIVATE_DISABLED')
        if not cfg.upbit_bithumb_live_enabled:
            config_blockers.append('UPBIT_BITHUMB_LIVE_DISABLED')
    if allowed_pairs and pair_id not in allowed_pairs:
        candidate_blockers.append('PAIR_NOT_ALLOWED')
    if allowed_symbols and symbol and symbol not in allowed_symbols:
        candidate_blockers.append('SYMBOL_NOT_ALLOWED')
    if not symbol:
        candidate_blockers.append('SYMBOL_UNAVAILABLE')
    if pair_id == 'UPBIT_BITHUMB' and direction not in ('UPBIT_BITHUMB_A', 'UPBIT_BITHUMB_B'):
        candidate_blockers.append('DIRECTION_UNAVAILABLE')
    if pair_id == 'UPBIT_BINANCE' and direction not in ('A', 'B'):
        candidate_blockers.append('DIRECTION_UNAVAILABLE')
    max_order = _num(cal.get('max_order_krw'), 10000)
    raw_notional = _num(selected.get('selected_notional_krw', selected.get('order_krw_used')), max_order)
    planned_notional = min(raw_notional or max_order, max_order)
    buy_price = _num(selected.get('selected_buy_price_krw'))
    if buy_price <= 0:
        if pair_id == 'UPBIT_BITHUMB':
            buy_price = _num(selected.get('bithumb_ask' if direction == 'UPBIT_BITHUMB_A' else 'upbit_ask'))
        elif direction == 'A':
            buy_price = _num(selected.get('binance_ask')) * (_num(selected.get('krw_usdt'), 1.0) or 1.0)
        else:
            buy_price = _num(selected.get('upbit_ask'))
    selected_qty = planned_notional / buy_price if buy_price > 0 else _num(selected.get('selected_qty'))
    plan_signal = {
        **selected,
        'entry_reason': entry_reason,
        'selected_notional_krw': planned_notional,
        'order_krw_used': planned_notional,
        'selected_qty': selected_qty,
        'effective_qty': selected_qty,
    }
    plan = selected.get('_preflight_plan') or (build_execution_plan(plan_signal, 'tiny_live', cfg) if selected else {})
    if plan:
        plan['entry_reason'] = entry_reason
    order_krw = _num(plan.get('selected_notional_krw'), planned_notional)
    if order_krw <= 0:
        candidate_blockers.append('ORDER_KRW_INVALID')
    if order_krw > max_order:
        candidate_blockers.append('MAX_ORDER_EXCEEDED')
    if not plan.get('leg_freshness_ok', True):
        candidate_blockers.append('LEG_QUOTE_TOO_OLD')
        if plan.get('leg_freshness_blocker'):
            candidate_blockers.append(plan['leg_freshness_blocker'])
    if not plan.get('depth_ok', False):
        candidate_blockers.append('DEPTH_INSUFFICIENT')
    if _num(plan.get('expected_net_profit_krw')) <= 0:
        candidate_blockers.append('EXPECTED_NET_NOT_POSITIVE')
    if not plan.get('plan_ok', False):
        candidate_blockers.extend(plan.get('execution_plan_blockers') or [])
    risk = {
        'risk_ok': bool(selected.get('reason_no_trade') == 'OK' or entry_reason in ('RECHECK_ACTIONABLE', 'WIDE_SPREAD_RECHECK_ACTIONABLE')),
        'reason_no_trade': selected.get('reason_no_trade', ''),
    }
    if not risk['risk_ok']:
        risk_blockers.append('RISK_GUARD_BLOCKED')
    should_check_balances = bool(cal.get('require_balance_check', True) and check_balances)
    upbit_balances, right_balances, balance_source_blockers = _balance_sources(pair_id, should_check_balances)
    balance_blockers.extend(balance_source_blockers)
    available = _available_assets(pair_id, symbol, upbit_balances, right_balances)
    required_raw, required = _required_assets_for_plan(plan, selected, pair_id, direction)
    balance_ok, missing = (
        _balance_result(required_raw, available)
        if should_check_balances else (True, [])
    )
    if balance_blockers:
        balance_ok = False
    if not balance_ok and not balance_blockers:
        balance_blockers.append('BALANCE_INSUFFICIENT')
    executor = {
        'exists': True,
        'name': 'TinyLiveExecutor',
        'submit_ready': cfg.tiny_live_enabled and cal.get('enabled', False),
    }
    if not executor['exists']:
        executor_blockers.append('EXECUTOR_NOT_FOUND')
    if not executor['submit_ready']:
        executor_blockers.append('EXECUTOR_NOT_READY')
    status = _status()
    session_submit_count = int(status.get('calibration_session_submit_count', 0) or 0)
    if bool(cal.get('one_shot_first', True)) and session_submit_count >= 1:
        executor_blockers.append('ONE_SHOT_LIMIT_REACHED')
    if session_submit_count >= int(cal.get('max_trades_per_session', 3) or 3):
        executor_blockers.append('SESSION_TRADE_LIMIT_REACHED')
    if abs(_num(status.get('daily_loss_krw'))) >= _num(cal.get('max_daily_loss_krw'), 3000):
        risk_blockers.append('DAILY_LOSS_LIMIT_REACHED')
        
    is_quote_too_old = any(b in candidate_blockers for b in ('LEG_QUOTE_TOO_OLD', 'BUY_LEG_QUOTE_TOO_OLD', 'SELL_LEG_QUOTE_TOO_OLD', 'MAX_LEG_QUOTE_AGE_EXCEEDED'))
    if is_quote_too_old and _num(plan.get('expected_net_profit_krw')) > 0 and plan.get('depth_ok', False) and symbol and direction in ('UPBIT_BITHUMB_A', 'UPBIT_BITHUMB_B'):
        import time
        from upbit_public import UpbitPublic
        from bithumb_public import BithumbPublic
        from execution_plan import build_execution_plan
        
        started_at = time.time()
        age_before = max(_num(plan.get('buy_leg_quote_age_ms')), _num(plan.get('sell_leg_quote_age_ms')))
        
        try:
            telemetry = _read_json('telemetry.json')
            telemetry['final_quote_refresh_count'] = telemetry.get('final_quote_refresh_count', 0) + 1
            
            if direction == 'UPBIT_BITHUMB_A':
                buy_quote = BithumbPublic().fetch_order_book(symbol)
                sell_quote = UpbitPublic().fetch_order_book(symbol)
            else:
                buy_quote = UpbitPublic().fetch_order_book(symbol)
                sell_quote = BithumbPublic().fetch_order_book(symbol)
                
            if buy_quote and sell_quote and buy_quote.get('ok') and sell_quote.get('ok'):
                if direction == 'UPBIT_BITHUMB_A':
                    selected['bithumb_ask'] = buy_quote.get('ask', 0)
                    selected['bithumb_ask_size'] = buy_quote.get('ask_size', 0)
                    selected['upbit_bid'] = sell_quote.get('bid', 0)
                    selected['upbit_bid_size'] = sell_quote.get('bid_size', 0)
                else:
                    selected['upbit_ask'] = buy_quote.get('ask', 0)
                    selected['upbit_ask_size'] = buy_quote.get('ask_size', 0)
                    selected['bithumb_bid'] = sell_quote.get('bid', 0)
                    selected['bithumb_bid_size'] = sell_quote.get('bid_size', 0)
                
                selected['buy_leg_quote'] = buy_quote
                selected['sell_leg_quote'] = sell_quote
                
                plan_signal = {
                    **selected,
                    'entry_reason': entry_reason,
                    'selected_notional_krw': planned_notional,
                    'order_krw_used': planned_notional,
                    'selected_qty': selected_qty,
                    'effective_qty': selected_qty,
                }
                new_plan = build_execution_plan(plan_signal, 'tiny_live', cfg)
                
                if new_plan and new_plan.get('plan_ok') and new_plan.get('leg_freshness_ok'):
                    plan = new_plan
                    plan['entry_reason'] = entry_reason
                    candidate_blockers = [b for b in candidate_blockers if b not in ('LEG_QUOTE_TOO_OLD', 'BUY_LEG_QUOTE_TOO_OLD', 'SELL_LEG_QUOTE_TOO_OLD', 'MAX_LEG_QUOTE_AGE_EXCEEDED')]
                    
                    if not plan.get('depth_ok', False):
                        if 'DEPTH_INSUFFICIENT' not in candidate_blockers:
                            candidate_blockers.append('DEPTH_INSUFFICIENT')
                    else:
                        candidate_blockers = [b for b in candidate_blockers if b != 'DEPTH_INSUFFICIENT']
                        
                    if _num(plan.get('expected_net_profit_krw')) <= 0:
                        if 'EXPECTED_NET_NOT_POSITIVE' not in candidate_blockers:
                            candidate_blockers.append('EXPECTED_NET_NOT_POSITIVE')
                    else:
                        candidate_blockers = [b for b in candidate_blockers if b != 'EXPECTED_NET_NOT_POSITIVE']
                    
                    if new_plan.get('execution_plan_blockers'):
                        candidate_blockers.extend(new_plan['execution_plan_blockers'])
                        
                    telemetry['final_quote_refresh_success_count'] = telemetry.get('final_quote_refresh_success_count', 0) + 1
                    telemetry['final_quote_refresh_last_symbol'] = symbol
                    telemetry['final_quote_refresh_last_age_before_ms'] = age_before
                    telemetry['final_quote_refresh_last_age_after_ms'] = max(_num(plan.get('buy_leg_quote_age_ms')), _num(plan.get('sell_leg_quote_age_ms')))
                    telemetry['final_quote_refresh_last_fetch_ms'] = (time.time() - started_at) * 1000
                    
            _write_json('telemetry.json', telemetry)
        except Exception:
            pass

    blockers = _unique(
        config_blockers + candidate_blockers + balance_blockers + executor_blockers + risk_blockers
    )
    can_submit = not blockers
    candidate_payload = None if not selected else {
        'pair_id': pair_id,
        'symbol': symbol,
        'direction': direction,
        'entry_reason': entry_reason,
        'selected_notional_krw': order_krw,
        'selected_qty': plan.get('selected_qty'),
        'buy_venue': plan.get('buy_venue'),
        'sell_venue': plan.get('sell_venue'),
        'expected_net_profit_krw': plan.get('expected_net_profit_krw'),
        'expected_net_bp': plan.get('expected_net_bp'),
        'total_fee_krw': plan.get('total_fee_krw'),
        'total_slippage_bp': plan.get('total_slippage_bp'),
        'buy_leg_quote_age_ms': plan.get('buy_leg_quote_age_ms'),
        'sell_leg_quote_age_ms': plan.get('sell_leg_quote_age_ms'),
        'max_leg_quote_age_ms': plan.get('max_leg_quote_age_ms'),
        'leg_quote_age_cap_ms': plan.get('leg_quote_age_cap_ms'),
        'leg_freshness_ok': plan.get('leg_freshness_ok'),
        'leg_freshness_blocker': plan.get('leg_freshness_blocker', ''),
        'depth_ok': plan.get('depth_ok'),
        'expected_fill_ratio_buy': plan.get('expected_fill_ratio_buy'),
        'expected_fill_ratio_sell': plan.get('expected_fill_ratio_sell'),
    }
    result = {
        'ok': can_submit,
        'ready': can_submit,
        'can_submit': can_submit,
        'blockers': blockers,
        'warnings': warnings,
        'candidate': candidate_payload,
        'candidate_selection': candidate_selection,
        'config_blockers': _unique(config_blockers),
        'candidate_blockers': _unique(candidate_blockers),
        'balance_blockers': _unique(balance_blockers),
        'executor_blockers': _unique(executor_blockers),
        'risk_blockers': _unique(risk_blockers),
        'required_assets': required,
        'available_assets': {key: round(_num(value), 10) for key, value in available.items()},
        'balance_ok': balance_ok,
        'balance_blocker': 'BALANCE_INSUFFICIENT' if missing else next((b for b in balance_blockers if 'BALANCE' in b or 'KEY' in b), ''),
        'missing_assets': missing,
        'risk': risk,
        'executor': executor,
        'plan': {**plan, 'blockers': blockers, 'executable': can_submit, 'preflight_status': 'PASS' if can_submit else 'BLOCKED'},
        'config': {
            'tiny_live_enabled': cfg.tiny_live_enabled,
            'calibration_enabled': bool(cal.get('enabled', False)),
            'require_preflight_pass': bool(cal.get('require_preflight_pass', True)),
            'require_balance_check': bool(cal.get('require_balance_check', True)),
            'one_shot_first': bool(cal.get('one_shot_first', True)),
            'max_order_krw': max_order,
            'allowed_pairs': list(cal.get('allowed_pairs') or []),
            'allowed_symbols': list(cal.get('allowed_symbols') or []),
        },
        'session_submit_count': session_submit_count,
        'updated_at': time.time(),
    }
    _write_json('tiny_live_preflight.json', result)
    return result


def _add_quote_and_risk_checks(blockers: list[str], quote: dict):
    calc = dict(quote.get('calc', {}))
    if not quote or not calc:
        blockers.append('LATEST_QUOTES_MISSING')
        return calc
    quote_ts = float(quote.get('timestamp', 0) or 0)
    if not quote_ts or time.time() - quote_ts > cfg.stale_quote_ms / 1000:
        blockers.append('STALE_QUOTE')
    if not RiskGuard().check_trade(calc):
        blockers.append(calc.get('reason_no_trade', 'RISK_GUARD_REJECTED'))
    return calc


def _inventory_and_filter_checks(blockers: list[str], symbol: str, direction: str, calc: dict) -> dict:
    order_krw = float(cfg.tiny_live_order_krw)
    fx = float(calc.get('krw_usdt', 0) or 0)
    upbit_bid = float(calc.get('upbit_bid', 0) or 0)
    binance_bid = float(calc.get('binance_bid', 0) or 0)
    if fx <= 0 or upbit_bid <= 0 or binance_bid <= 0:
        blockers.append('PRICE_UNAVAILABLE')
        return {'qty': 0, 'normalized_qty': 0, 'usdt': 0}
    qty = order_krw / (upbit_bid if direction == 'A' else binance_bid * fx)
    usdt = order_krw / fx
    upbit = UpbitPrivateClient().get_balances()
    binance_client = BinanceSpotPrivateClient()
    binance = binance_client.get_balances()
    blockers.extend(upbit.get('blockers', []))
    blockers.extend(binance.get('blockers', []))
    filters = (binance_client.normalize_quote_order(symbol, usdt) if direction == 'A'
               else binance_client.normalize_qty(symbol, qty))
    filter_blockers = filters.get('blockers', [])
    blockers.extend(filter_blockers)
    if filter_blockers:
        blockers.append('MIN_ORDER_FAIL')
    if blockers:
        return {'qty': qty, 'normalized_qty': qty, 'usdt': usdt}
    normalized_qty = float(filters.get('qty', qty) or qty)
    if direction == 'B' and normalized_qty * binance_bid < float(filters.get('min_notional', 0) or 0):
        blockers.extend(['MIN_ORDER_FAIL', 'BINANCE_MIN_NOTIONAL'])
    upbit_balances, binance_balances = upbit['balances'], binance['balances']
    if direction == 'A':
        if float(upbit_balances.get(symbol, 0) or 0) < qty:
            blockers.append(f'UPBIT_{symbol}_SHORTAGE')
        if float(binance_balances.get('USDT', 0) or 0) < usdt:
            blockers.append('BINANCE_USDT_SHORTAGE')
    elif direction == 'B':
        if float(upbit_balances.get('KRW', 0) or 0) < order_krw:
            blockers.append('UPBIT_KRW_SHORTAGE')
        if float(binance_balances.get(symbol, 0) or 0) < normalized_qty:
            blockers.append(f'BINANCE_{symbol}_SHORTAGE')
    else:
        blockers.append('DIRECTION_UNAVAILABLE')
    if any('SHORTAGE' in item for item in blockers):
        blockers.append('INVENTORY_SHORTAGE')
    return {'qty': qty, 'normalized_qty': normalized_qty, 'usdt': usdt}


def _create_upbit_bithumb_plan(readiness: dict) -> dict:
    blockers = list(readiness['blockers'])
    snapshot = _read_json('latest_opportunities.json')
    rows = [
        item for item in snapshot.get('all_opportunities', [])
        if item.get('pair_id') == 'UPBIT_BITHUMB'
    ]
    if not rows:
        blockers.append('LATEST_QUOTES_MISSING')
        return {**readiness, 'ready': False, 'blockers': _unique(blockers), 'plan': None}
    calc = max(rows, key=lambda item: item.get('best_net_surplus_bp', -9999))
    symbol, direction = calc.get('symbol', ''), calc.get('best_direction', '')
    if direction not in ('UPBIT_BITHUMB_A', 'UPBIT_BITHUMB_B'):
        blockers.append('DIRECTION_UNAVAILABLE')
    if calc.get('reason_no_trade') != 'OK':
        blockers.append(calc.get('reason_no_trade', 'RISK_GUARD_REJECTED'))
    quote_age_ms = max(0, time.time() - float(calc.get('bithumb_ts', 0) or 0)) * 1000
    if quote_age_ms > cfg.stale_quote_ms:
        blockers.append('STALE_QUOTE')
    order_krw = float(cfg.upbit_bithumb_order_krw)
    price = float(calc.get('upbit_bid' if direction == 'UPBIT_BITHUMB_A' else 'bithumb_bid', 0) or 0)
    qty = order_krw / price if price > 0 else 0
    if not blockers:
        upbit, bithumb = UpbitPrivateClient().get_balances(), BithumbPrivateClient().get_balances()
        blockers.extend(upbit.get('blockers', []))
        blockers.extend(bithumb.get('blockers', []))
        up, bh = upbit.get('balances', {}), bithumb.get('balances', {})
        if direction == 'UPBIT_BITHUMB_A':
            if float(up.get(symbol, 0) or 0) < qty or float(bh.get('KRW', 0) or 0) < order_krw:
                blockers.append('INVENTORY_SHORTAGE')
        elif direction == 'UPBIT_BITHUMB_B':
            if float(bh.get(symbol, 0) or 0) < qty or float(up.get('KRW', 0) or 0) < order_krw:
                blockers.append('INVENTORY_SHORTAGE')
    left_side, right_side = (
        ('SELL', 'BUY') if direction == 'UPBIT_BITHUMB_A' else ('BUY', 'SELL')
    )
    plan = ExecutionPlan(
        pair_id='UPBIT_BITHUMB', strategy_type='DOMESTIC_KRW',
        left_venue='UPBIT', right_venue='BITHUMB', domestic_only=True, fx_required=False,
        left_side=left_side, right_side=right_side, symbol=symbol, direction=direction,
        direction_label=direction, mode=cfg.mode, quote_available=bool(rows),
        inventory_sufficient='INVENTORY_SHORTAGE' not in blockers,
        plan_id=str(uuid.uuid4()), upbit_side=left_side,
        order_krw=order_krw, qty=qty, normalized_qty=qty, quantity=qty,
        quote_timestamp=float(calc.get('bithumb_ts', 0) or 0), quote_age_ms=round(quote_age_ms, 2),
        quote_source='rest', left_expected_price=float(calc.get('upbit_bid' if left_side == 'SELL' else 'upbit_ask', 0) or 0),
        right_expected_price=float(calc.get('bithumb_bid' if right_side == 'SELL' else 'bithumb_ask', 0) or 0),
        expected_net_profit_krw=float(calc.get('net_expected_profit_krw', 0) or 0),
        best_net_surplus_bp=float(calc.get('best_net_surplus_bp', 0) or 0),
        min_order_ok=order_krw >= cfg.bithumb_min_order_krw, risk_ok=calc.get('reason_no_trade') == 'OK',
        preflight_status='PASS' if not blockers else 'BLOCKED', blockers=_unique(blockers),
        warnings=list(readiness['warnings']), executable=not blockers,
    )
    plan_dict = plan.to_dict()
    plan_dict.update(build_execution_plan({**calc, **plan_dict}, cfg.mode, cfg))
    if not plan_dict.get('plan_ok'):
        blockers.extend(plan_dict.get('execution_plan_blockers', []))
        plan_dict['preflight_status'] = 'BLOCKED'
        plan_dict['executable'] = False
    return {**readiness, 'ready': not blockers, 'blockers': _unique(blockers), 'plan': plan_dict}


def create_preflight_plan(pair_id='UPBIT_BINANCE') -> dict:
    readiness = get_tiny_live_readiness(pair_id)
    if pair_id == 'UPBIT_BITHUMB':
        result = _create_upbit_bithumb_plan(readiness)
        _write_json(PREFLIGHT_FILE, result)
        return result
    blockers = list(readiness['blockers'])
    quotes = _read_json('latest_quotes.json')
    if not quotes:
        blockers.append('LATEST_QUOTES_MISSING')
        result = {**readiness, 'ready': False, 'blockers': _unique(blockers), 'plan': None}
        _write_json(PREFLIGHT_FILE, result)
        return result
    symbol, quote = max(quotes.items(), key=lambda item: item[1].get('calc', {}).get('best_net_surplus_bp', -9999))
    direction = quote.get('calc', {}).get('best_direction', '')
    if direction not in ('A', 'B'):
        blockers.append('DIRECTION_UNAVAILABLE')
    calc = _add_quote_and_risk_checks(blockers, quote)
    order = {'qty': 0, 'normalized_qty': 0, 'usdt': 0}
    if not blockers:
        order = _inventory_and_filter_checks(blockers, symbol, direction, calc)
    upbit_side, binance_side = ('SELL', 'BUY') if direction == 'A' else ('BUY', 'SELL')
    quote_ts = float(quote.get('timestamp', 0) or 0)
    plan = ExecutionPlan(
        symbol=symbol, direction=direction, mode=cfg.mode, quote_available=bool(quote),
        inventory_sufficient=not any('SHORTAGE' in item for item in blockers),
        plan_id=str(uuid.uuid4()), direction_label='A_KIMCHI' if direction == 'A' else 'B_REVERSE_KIMCHI',
        upbit_side=upbit_side, binance_side=binance_side,
        left_side=upbit_side, right_side=binance_side,
        order_krw=cfg.tiny_live_order_krw, order_usdt=order['usdt'], qty=order['qty'],
        normalized_qty=order['normalized_qty'], quantity=order['normalized_qty'], binance_usdt=order['usdt'],
        quote_timestamp=quote_ts, quote_age_ms=round(max(0, time.time() - quote_ts) * 1000, 2),
        upbit_bid=float(calc.get('upbit_bid', 0) or 0), upbit_ask=float(calc.get('upbit_ask', 0) or 0),
        binance_bid=float(calc.get('binance_bid', 0) or 0), binance_ask=float(calc.get('binance_ask', 0) or 0),
        upbit_expected_price=float(calc.get('upbit_bid' if direction == 'A' else 'upbit_ask', 0) or 0),
        binance_expected_price=float(calc.get('binance_ask' if direction == 'A' else 'binance_bid', 0) or 0),
        fx_rate=float(calc.get('krw_usdt', 0) or 0),
        expected_net_profit_krw=float(calc.get('net_expected_profit_krw', 0) or 0),
        best_net_surplus_bp=float(calc.get('best_net_surplus_bp', 0) or 0),
        preflight_status='PASS' if not blockers else 'BLOCKED', blockers=_unique(blockers),
        warnings=list(readiness['warnings']), executable=not blockers,
    )
    plan_dict = plan.to_dict()
    plan_dict.update(build_execution_plan({
        **calc, **plan_dict,
        'upbit_orderbook': quote.get('upbit', {}),
        'binance_orderbook': quote.get('binance', {}),
    }, cfg.mode, cfg))
    if not plan_dict.get('plan_ok'):
        blockers.extend(plan_dict.get('execution_plan_blockers', []))
        plan_dict['preflight_status'] = 'BLOCKED'
        plan_dict['executable'] = False
    result = {**readiness, 'ready': not blockers, 'blockers': _unique(blockers), 'plan': plan_dict}
    _write_json(PREFLIGHT_FILE, result)
    return result


class TinyLiveExecutor:
    def __init__(self):
        self.tracker = OrderTracker()
        self.emergency = EmergencyLiquidator()

    def preflight(self, pair_id=None, plan=None) -> dict:
        if plan:
            return build_tiny_live_preflight(
                pair_id or plan.get('pair_id') or _status().get('pair_id') or 'UPBIT_BITHUMB',
                candidate=plan,
                check_balances=True,
            )
        return create_preflight_plan(pair_id or _status().get('pair_id') or 'UPBIT_BINANCE')

    def arm(self, pair_id=None) -> dict:
        pair_id = pair_id or 'UPBIT_BINANCE'
        preflight = self.preflight(pair_id)
        if not preflight['ready']:
            status = _write_status(armed=False, status='DISARMED', blockers=preflight['blockers'],
                                   warnings=preflight.get('warnings', []), last_error='MODE_GUARD')
            return {'ok': False, **status}
        status = _write_status(armed=True, status='ARMED', pair_id=pair_id, blockers=[], warnings=preflight.get('warnings', []),
                               last_error='')
        return {'ok': True, **status}

    def disarm(self) -> dict:
        status = _write_status(armed=False, status='DISARMED')
        return {'ok': True, **status}

    def status(self) -> dict:
        return {
            **_status(), 'last_preflight': _read_json(PREFLIGHT_FILE), 'last_order': _read_json(ORDER_FILE),
            'order_tracker': self.tracker.to_dict(), 'emergency': self.emergency.status(self.tracker.to_dict()),
        }

    def manual_clear_partial_risk(self, reason: str) -> dict:
        tracker_state = self.tracker.to_dict()
        if tracker_state.get('status') not in BLOCKING_STATUSES and not tracker_state.get('emergency_required'):
            return {'ok': False, 'error': 'PARTIAL_RISK_NOT_ACTIVE', 'blockers': ['PARTIAL_RISK_NOT_ACTIVE']}
        tracker_state = self.tracker.manual_clear(reason)
        status = _write_status(
            armed=False, status='DISARMED', partial_risk=False, blockers=[], last_error='',
            warnings=['MANUAL_CLEAR_RECORDED'],
        )
        return {'ok': True, **status, 'order_tracker': tracker_state}

    def preview_emergency(self) -> dict:
        tracker_state = self.tracker.to_dict()
        last_order = _read_json(ORDER_FILE)
        last_preflight = _read_json(PREFLIGHT_FILE)
        plan = last_order.get('plan') or last_preflight.get('plan') or {}
        preview = self.emergency.preview(tracker_state, plan)
        if tracker_state.get('emergency_required'):
            tracker_state = self.tracker.set_emergency_preview(
                {'plan': preview.get('emergency_plan', {})}, preview['manual_action']
            )
        return {**preview, 'order_tracker': tracker_state}

    def execute_once(self, pair_id=None, plan=None) -> dict:
        status = _status()
        if not status.get('armed'):
            return {'ok': False, 'status': 'DISARMED', 'blockers': ['TINY_LIVE_DISARMED']}
        pair_id = pair_id or status.get('pair_id') or 'UPBIT_BINANCE'
        preflight = self.preflight(pair_id, plan=plan) if plan else self.preflight(pair_id)
        plan = preflight.get('plan') or {}
        if not preflight['ready'] or not plan.get('executable'):
            _write_status(status='BLOCKED', blockers=preflight['blockers'], last_error='PREFLIGHT_BLOCKED')
            return {'ok': False, 'status': 'BLOCKED', 'blockers': preflight['blockers'], 'plan': plan}
        if time.time() - float(plan.get('quote_timestamp', 0) or 0) > cfg.stale_quote_ms / 1000:
            _write_status(status='BLOCKED', blockers=['STALE_QUOTE'], last_error='STALE_QUOTE')
            return {'ok': False, 'status': 'BLOCKED', 'blockers': ['STALE_QUOTE'], 'plan': plan}
        return self.execute_plan(plan)

    def execute(self, plan: dict) -> dict:
        """Execute the shared ExecutionPlan through the existing guarded path."""
        return self.execute_once(pair_id=plan.get('pair_id', 'UPBIT_BINANCE'), plan=plan)

    def execute_plan(self, plan: dict) -> dict:
        current = _status()
        pair_id = plan.get('pair_id', 'UPBIT_BINANCE')
        blockers = _base_blockers(pair_id)
        last_preflight = _read_json(PREFLIGHT_FILE)
        last_plan = last_preflight.get('plan') or {}
        if not current.get('armed'):
            blockers.append('TINY_LIVE_DISARMED')
        if not last_preflight.get('ready') or not plan.get('plan_id') or plan.get('plan_id') != last_plan.get('plan_id'):
            blockers.append('PREFLIGHT_REQUIRED')
        if time.time() - float(plan.get('quote_timestamp', 0) or 0) > cfg.stale_quote_ms / 1000:
            blockers.append('STALE_QUOTE')
        if blockers:
            blockers = _unique(blockers)
            _write_status(status='BLOCKED', blockers=blockers, last_error='EXECUTION_GUARD_BLOCKED')
            return {'ok': False, 'status': 'BLOCKED', 'blockers': blockers, 'plan': plan}
        if cfg.order_tracker_enabled:
            self.tracker.start_plan(plan)
        status = _write_status(
            status='EXECUTING', blockers=[], last_error='',
            calibration_session_submit_count=int(current.get('calibration_session_submit_count', 0) or 0) + 1,
        )
        symbol, direction = plan['symbol'], plan['direction']
        upbit = UpbitPrivateClient()
        if pair_id == 'UPBIT_BITHUMB':
            right_client, calls = self.execute_upbit_bithumb(plan, upbit)
        else:
            right_client, calls = self.execute_upbit_binance(plan, upbit)
        results, errors = {}, {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = {pool.submit(call): name for name, call in calls.items()}
            for future in as_completed(pending):
                name = pending[future]
                try:
                    results[name] = future.result()
                    if cfg.order_tracker_enabled:
                        self.tracker.mark_submitted(name, results[name])
                except Exception as exc:
                    errors[name] = f'{type(exc).__name__}: {exc}'
                    if cfg.order_tracker_enabled:
                        self.tracker.mark_failed(name, errors[name])
        fills, wait_calls = {}, {}
        upbit_order_id = results.get('upbit', {}).get('uuid')
        right_name = 'bithumb' if pair_id == 'UPBIT_BITHUMB' else 'binance'
        right_order_id = results.get(right_name, {}).get('uuid' if right_name == 'bithumb' else 'orderId')
        if results.get('upbit') and upbit_order_id:
            wait_calls['upbit'] = lambda: upbit.wait_order_filled(upbit_order_id, cfg.order_ttl_sec)
        if results.get(right_name) and right_order_id is not None:
            wait_calls[right_name] = (
                (lambda: right_client.wait_order_filled(right_order_id, cfg.order_ttl_sec))
                if right_name == 'bithumb'
                else (lambda: right_client.wait_order_filled(symbol, right_order_id, cfg.order_ttl_sec))
            )
        if wait_calls:
            if cfg.order_tracker_enabled:
                for name in wait_calls:
                    self.tracker.mark_waiting_fill(name)
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = {pool.submit(call): name for name, call in wait_calls.items()}
                for future in as_completed(pending):
                    name = pending[future]
                    try:
                        fills[name] = future.result()
                        if cfg.order_tracker_enabled:
                            self.tracker.mark_filled(name, fills[name])
                            if not fills[name].get('filled'):
                                self.tracker.mark_timeout(name)
                    except Exception as exc:
                        errors[name] = f'{type(exc).__name__}: {exc}'
                        if cfg.order_tracker_enabled:
                            self.tracker.mark_failed(name, errors[name])
        return self._finalize_execution(plan, status, results, fills, errors)

    def execute_upbit_binance(self, plan, upbit=None):
        upbit, binance = upbit or UpbitPrivateClient(), BinanceSpotPrivateClient()
        symbol = plan['symbol']
        if plan['direction'] == 'A':
            calls = {
                'upbit': lambda: upbit.place_market_sell_qty(symbol, plan['normalized_qty']),
                'binance': lambda: binance.place_market_buy_quote(symbol, plan['binance_usdt']),
            }
        else:
            calls = {
                'upbit': lambda: upbit.place_market_buy_krw(symbol, plan['order_krw']),
                'binance': lambda: binance.place_market_sell_qty(symbol, plan['normalized_qty']),
            }
        return binance, calls

    def execute_upbit_bithumb(self, plan, upbit=None):
        upbit, bithumb = upbit or UpbitPrivateClient(), BithumbPrivateClient()
        symbol = plan['symbol']
        if plan['direction'] == 'UPBIT_BITHUMB_A':
            calls = {'upbit': lambda: upbit.place_market_sell_qty(symbol, plan['normalized_qty']),
                     'bithumb': lambda: bithumb.place_market_buy_krw(symbol, plan['order_krw'])}
        else:
            calls = {'upbit': lambda: upbit.place_market_buy_krw(symbol, plan['order_krw']),
                     'bithumb': lambda: bithumb.place_market_sell_qty(symbol, plan['normalized_qty'])}
        return bithumb, calls

    def _finalize_execution(self, plan, status, results, fills, errors):
        filled = (
            not errors and len(fills) == 2
            and all(item.get('filled') and float(item.get('fill_ratio', 0) or 0) >= cfg.min_fill_ratio
                    for item in fills.values())
        )
        partial = not filled and (bool(results) or any(float(item.get('fill_ratio', 0) or 0) > 0 for item in fills.values()))
        manual_action = self.emergency.manual_action(self.tracker.to_dict())
        emergency_result = {}
        if filled:
            next_status = _write_status(
                status='FILLED', trade_count=int(status.get('trade_count', 0)) + 1,
                calibration_session_success_count=int(status.get('calibration_session_success_count', 0) or 0) + 1,
                blockers=[], last_error='',
            )
        elif partial:
            if cfg.order_tracker_enabled:
                self.tracker.mark_partial_risk(manual_action)
            emergency_check = self.emergency.can_execute_emergency(self.tracker.to_dict(), plan)
            if cfg.order_tracker_enabled:
                self.tracker.set_emergency_preview(emergency_check, manual_action)
            emergency_result = {
                'ok': False, 'status': 'EMERGENCY_PENDING',
                'blockers': emergency_check['blockers'],
                'emergency_plan': emergency_check['plan'],
                'suggested_manual_action': manual_action,
            }
            if emergency_check.get('ready') and cfg.emergency_auto_execute:
                if cfg.order_tracker_enabled:
                    self.tracker.mark_emergency_attempted(emergency_check['plan'])
                emergency_result = self.emergency.execute_emergency_once(
                    self.tracker.to_dict(), plan, check=emergency_check
                )
                if cfg.order_tracker_enabled:
                    self.tracker.mark_emergency_result(emergency_result.get('ok', False), emergency_result)
            next_status = _write_status(
                armed=False, status='PARTIAL_RISK', partial_risk=True,
                calibration_session_fail_count=int(status.get('calibration_session_fail_count', 0) or 0) + 1,
                blockers=['PARTIAL_RISK_ACTIVE'], last_error='PARTIAL_RISK',
            )
        else:
            next_status = _write_status(
                status='BLOCKED', blockers=['ORDER_FAILED'], last_error='ORDER_FAILED',
                calibration_session_fail_count=int(status.get('calibration_session_fail_count', 0) or 0) + 1,
            )
        output = {
            'ok': filled, 'status': next_status['status'], 'plan': plan, 'results': results,
            'fills': fills, 'errors': errors, 'partial_risk': bool(next_status.get('partial_risk')),
            'order_tracker': self.tracker.to_dict() if cfg.order_tracker_enabled else {},
            'emergency': emergency_result,
            'suggested_manual_action': manual_action if partial else '',
        }
        _write_json(ORDER_FILE, output)
        return output


class LiveExecutor:
    """Placeholder live executor that preserves the unified interface.

    A full live executor is intentionally not wired yet. Keeping this class
    explicit lets the unified router select a live executor without adding any
    new order path or relaxing the disabled-by-default live guards.
    """

    def execute(self, plan: dict) -> dict:
        return {
            'ok': False,
            'status': 'BLOCKED',
            'plan': plan,
            'blockers': ['LIVE_EXECUTOR_NOT_CONFIGURED'],
            'order_submit_attempted': False,
        }
