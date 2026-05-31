"""Tiny-live preflight checks. No order execution is implemented in this module."""
import json
import os

from config import cfg
from execution_plan import ExecutionPlan
from inventory_manager import InventoryManager
from secrets_manager import get_api_permission_policy, get_key_status


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'runtime'))


def _read_json(name: str) -> dict:
    try:
        with open(os.path.join(RUNTIME_DIR, name), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def get_inventory_summary() -> dict:
    quotes = _read_json('latest_quotes.json')
    return InventoryManager().inventory_summary(quotes=quotes, mode=cfg.mode)


def get_tiny_live_readiness() -> dict:
    perf = _read_json('performance_summary.json')
    last_session = _read_json('last_session_summary.json')
    inventory = get_inventory_summary()
    keys = get_key_status()
    blockers = []
    warnings = [
        'WITHDRAWALS_DISABLED_BY_POLICY',
        'MANUAL_REBALANCE_ONLY',
        'MIN_ORDER_CHECK_NOT_IMPLEMENTED',
    ]

    if keys['UPBIT_ACCESS_KEY'] != 'Set' or keys['UPBIT_SECRET_KEY'] != 'Set':
        blockers.append('UPBIT_KEY_MISSING')
    if keys['BINANCE_API_KEY'] != 'Set' or keys['BINANCE_API_SECRET'] != 'Set':
        blockers.append('BINANCE_KEY_MISSING')
    if not cfg.enable_live_trading:
        blockers.append('ENABLE_LIVE_TRADING_FALSE')
    if not cfg.tiny_live_enabled:
        blockers.append('TINY_LIVE_DISABLED')
    if not cfg.live_order_enabled:
        blockers.append('LIVE_ORDER_ENABLED_FALSE')
    if cfg.mode not in ('tiny_live', 'live'):
        blockers.append('MODE_PAPER_EXECUTION_BLOCKED')
    if cfg.withdrawals_enabled:
        blockers.append('WITHDRAWALS_MUST_REMAIN_DISABLED')
    if cfg.futures_hedge_enabled:
        blockers.append('FUTURES_HEDGE_MUST_REMAIN_DISABLED')
    if not cfg.manual_rebalance_only:
        blockers.append('MANUAL_REBALANCE_ONLY_REQUIRED')
    if cfg.require_paper_pass_for_tiny_live and last_session.get('judgement') != 'PAPER_EDGE_PASS':
        blockers.append('PAPER_PASS_REQUIRED')

    closed = int(perf.get('closed_trade_count', 0))
    if closed < cfg.min_paper_closed_trades_for_tiny_live:
        blockers.append('NOT_ENOUGH_PAPER_TRADES')
    if float(perf.get('net_pnl_krw', 0)) <= cfg.min_paper_net_pnl_krw_for_tiny_live:
        blockers.append('PAPER_NET_PNL_TOO_LOW')
    if float(perf.get('win_rate', 0)) < cfg.min_paper_win_rate_for_tiny_live * 100:
        blockers.append('PAPER_WIN_RATE_TOO_LOW')
    if float(perf.get('avg_pnl_krw', 0)) <= 0:
        blockers.append('PAPER_AVG_PNL_TOO_LOW')
    if not any(row['direction_a_possible'] or row['direction_b_possible']
               for row in inventory['symbols']):
        blockers.append('INVENTORY_SHORTAGE')

    return {
        'ready': not blockers,
        'blockers': blockers,
        'warnings': warnings,
        'next_action': (
            'Review blockers and rebalance manually. No automatic transfer is available.'
            if blockers else
            'Preflight passed. Order execution remains disabled until explicitly implemented.'
        ),
        'key_status': keys,
        'permission_policy': get_api_permission_policy(),
        'inventory': inventory,
    }


def create_preflight_plan() -> dict:
    quotes = _read_json('latest_quotes.json')
    readiness = get_tiny_live_readiness()
    if not quotes:
        readiness['blockers'].append('LATEST_QUOTES_MISSING')
        readiness['ready'] = False
        return {**readiness, 'ready': False, 'plan': None}

    symbol, quote = max(
        quotes.items(),
        key=lambda item: item[1].get('calc', {}).get('best_net_surplus_bp', -9999),
    )
    direction = quote.get('calc', {}).get('best_direction', '')
    reason = quote.get('calc', {}).get('reason_no_trade', '')
    if reason != 'OK':
        readiness['blockers'].append('QUOTE_NOT_TRADEABLE')
        readiness['ready'] = False
    inventory = readiness['inventory']
    row = next((item for item in inventory['symbols'] if item['symbol'] == symbol), {})
    possible = row.get('direction_a_possible') if direction == 'A' else row.get('direction_b_possible')
    plan = ExecutionPlan(
        symbol=symbol,
        direction=direction,
        mode=cfg.mode,
        quote_available=True,
        inventory_sufficient=bool(possible),
        blockers=list(readiness['blockers']),
        warnings=list(readiness['warnings']),
        executable=False,
    )
    return {**readiness, 'ready': readiness['ready'] and bool(possible), 'plan': plan.to_dict()}
