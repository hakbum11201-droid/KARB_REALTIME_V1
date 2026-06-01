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
from execution_plan import ExecutionPlan
from inventory_manager import InventoryManager
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


def get_inventory_summary() -> dict:
    return _public_inventory_summary(_read_json('latest_quotes.json'))


def _base_blockers() -> list[str]:
    keys = get_key_status()
    blockers = []
    if keys['UPBIT_ACCESS_KEY'] != 'Set' or keys['UPBIT_SECRET_KEY'] != 'Set':
        blockers.append('UPBIT_KEY_MISSING')
    if keys['BINANCE_API_KEY'] != 'Set' or keys['BINANCE_API_SECRET'] != 'Set':
        blockers.append('BINANCE_KEY_MISSING')
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
    return _unique(blockers)


def get_tiny_live_readiness() -> dict:
    perf = _read_json('performance_summary.json')
    last_session = _read_json('last_session_summary.json')
    quotes = _read_json('latest_quotes.json')
    blockers = _base_blockers()
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
    inventory = _public_inventory_summary(quotes) if not blockers else {}
    blockers.extend(inventory.get('blockers', []))
    return {
        'ready': not blockers,
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
        },
    }


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


def create_preflight_plan() -> dict:
    readiness = get_tiny_live_readiness()
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
    result = {**readiness, 'ready': not blockers, 'blockers': _unique(blockers), 'plan': plan.to_dict()}
    _write_json(PREFLIGHT_FILE, result)
    return result


class TinyLiveExecutor:
    def preflight(self, plan=None) -> dict:
        return create_preflight_plan()

    def arm(self) -> dict:
        preflight = self.preflight()
        if not preflight['ready']:
            status = _write_status(armed=False, status='DISARMED', blockers=preflight['blockers'],
                                   warnings=preflight.get('warnings', []), last_error='MODE_GUARD')
            return {'ok': False, **status}
        status = _write_status(armed=True, status='ARMED', blockers=[], warnings=preflight.get('warnings', []),
                               last_error='')
        return {'ok': True, **status}

    def disarm(self) -> dict:
        status = _write_status(armed=False, status='DISARMED')
        return {'ok': True, **status}

    def status(self) -> dict:
        return {**_status(), 'last_preflight': _read_json(PREFLIGHT_FILE), 'last_order': _read_json(ORDER_FILE)}

    def execute_once(self, plan=None) -> dict:
        status = _status()
        if not status.get('armed'):
            return {'ok': False, 'status': 'DISARMED', 'blockers': ['TINY_LIVE_DISARMED']}
        preflight = self.preflight()
        plan = preflight.get('plan') or {}
        if not preflight['ready'] or not plan.get('executable'):
            _write_status(status='BLOCKED', blockers=preflight['blockers'], last_error='PREFLIGHT_BLOCKED')
            return {'ok': False, 'status': 'BLOCKED', 'blockers': preflight['blockers'], 'plan': plan}
        if time.time() - float(plan.get('quote_timestamp', 0) or 0) > cfg.stale_quote_ms / 1000:
            _write_status(status='BLOCKED', blockers=['STALE_QUOTE'], last_error='STALE_QUOTE')
            return {'ok': False, 'status': 'BLOCKED', 'blockers': ['STALE_QUOTE'], 'plan': plan}
        return self.execute_plan(plan)

    def execute_plan(self, plan: dict) -> dict:
        current = _status()
        blockers = _base_blockers()
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
        status = _write_status(status='EXECUTING', blockers=[], last_error='')
        symbol, direction = plan['symbol'], plan['direction']
        upbit, binance = UpbitPrivateClient(), BinanceSpotPrivateClient()
        if direction == 'A':
            calls = {
                'upbit': lambda: upbit.place_market_sell_qty(symbol, plan['normalized_qty']),
                'binance': lambda: binance.place_market_buy_quote(symbol, plan['binance_usdt']),
            }
        else:
            calls = {
                'upbit': lambda: upbit.place_market_buy_krw(symbol, plan['order_krw']),
                'binance': lambda: binance.place_market_sell_qty(symbol, plan['normalized_qty']),
            }
        results, errors = {}, {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = {pool.submit(call): name for name, call in calls.items()}
            for future in as_completed(pending):
                name = pending[future]
                try:
                    results[name] = future.result()
                except Exception as exc:
                    errors[name] = f'{type(exc).__name__}: {exc}'
        fills = {}
        if not errors:
            try:
                upbit_order_id = results['upbit'].get('uuid')
                binance_order_id = results['binance'].get('orderId')
                if not upbit_order_id or binance_order_id is None:
                    raise ValueError('ORDER_ID_MISSING')
                with ThreadPoolExecutor(max_workers=2) as pool:
                    pending = {
                        pool.submit(upbit.wait_order_filled, upbit_order_id, cfg.order_ttl_sec): 'upbit',
                        pool.submit(binance.wait_order_filled, symbol, binance_order_id, cfg.order_ttl_sec): 'binance',
                    }
                    for future in as_completed(pending):
                        name = pending[future]
                        try:
                            fills[name] = future.result()
                        except Exception as exc:
                            errors[name] = f'{type(exc).__name__}: {exc}'
            except Exception as exc:
                errors['fill_check'] = f'{type(exc).__name__}: {exc}'
        filled = (
            not errors and len(fills) == 2
            and all(item.get('filled') and float(item.get('fill_ratio', 0) or 0) >= cfg.min_fill_ratio
                    for item in fills.values())
        )
        partial = not filled and (bool(results) or any(float(item.get('fill_ratio', 0) or 0) > 0 for item in fills.values()))
        if filled:
            next_status = _write_status(
                status='FILLED', trade_count=int(status.get('trade_count', 0)) + 1,
                blockers=[], last_error='',
            )
        elif partial:
            next_status = _write_status(
                armed=False, status='PARTIAL_RISK', partial_risk=True,
                blockers=['PARTIAL_RISK_ACTIVE'], last_error='PARTIAL_RISK',
            )
        else:
            next_status = _write_status(status='BLOCKED', blockers=['ORDER_FAILED'], last_error='ORDER_FAILED')
        output = {
            'ok': filled, 'status': next_status['status'], 'plan': plan, 'results': results,
            'fills': fills, 'errors': errors, 'partial_risk': bool(next_status.get('partial_risk')),
            'suggested_manual_action': (
                'Stop new entries and manually inspect both exchange orders and balances. Automatic unwind is disabled.'
                if partial else ''
            ),
        }
        _write_json(ORDER_FILE, output)
        return output
