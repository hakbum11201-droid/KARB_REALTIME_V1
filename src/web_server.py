"""
web_server.py - KARB Realtime V1 HTTP 서버.
엔드포인트:
  GET  /api/state          – state.json
  GET  /api/data           – state + quotes 통합
  GET  /api/perf           – performance_summary + recent_trades
  GET  /api/performance    – performance_summary
  GET  /api/trades/recent  – 최근 paper_trades.jsonl EXIT 20건
  GET  /api/keys/status    – 키 Set/Missing (localhost만)
  POST /api/keys/save      – 키 저장 (localhost만)
  GET  /api/session/last   – last_session_summary.json
  POST /api/stop           – stop_requested=true (localhost만)
  GET  /api/health         – ok
"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import os
import sys
import argparse
import time
from urllib.parse import parse_qs, urlparse

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
WEB_DIR     = os.path.normpath(os.path.join(BASE_DIR, '..', 'web'))
RUNTIME_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'runtime'))
LOGS_DIR    = os.path.normpath(os.path.join(BASE_DIR, '..', 'logs'))

sys.path.insert(0, BASE_DIR)
import secrets_manager
import control as ctrl_module
import process_manager
from config import cfg
from executors import (
    TinyLiveExecutor, build_tiny_live_preflight, create_preflight_plan,
    get_inventory_summary, get_tiny_live_readiness,
)
from venue_pair import venue_pair_payload
from bithumb_private import BithumbPrivateClient
from iceberg_planner import IcebergPlanner
from rate_limiter import rate_limiter
from risk_guard import RiskGuard
from execution_plan import build_notional_sweep

tiny_live_executor = TinyLiveExecutor()
_NOTIONAL_SWEEP_CACHE = {
    'updated_at': 0.0,
    'source_updated_at': 0.0,
    'payload': None,
}


def _read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _read_jsonl_tail(path, n=50):
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        out = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out
    except Exception:
        return []


def _is_mock_trade(trade: dict) -> bool:
    symbol = str(trade.get('symbol', '')).upper()
    return bool(trade.get('is_mock') or trade.get('test_only') or symbol in {'MOCK', 'MOCK2', 'NORMAL'})


def _recent_real_exit_trades(limit=20):
    recent = _read_jsonl_tail(os.path.join(LOGS_DIR, 'paper_trades.jsonl'), 200)
    exits = [r for r in recent if r.get('event') == 'EXIT' and not _is_mock_trade(r)][-limit:]
    for trade in exits:
        trade.setdefault('pair_id', 'UPBIT_BINANCE')
        trade.setdefault('entry_reason', 'UNKNOWN')
    return exits


def _live_readiness_payload(pair_id='UPBIT_BINANCE'):
    readiness = get_tiny_live_readiness(pair_id)
    quotes = _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json'))
    newest_quote = max((float(item.get('timestamp', 0) or 0) for item in quotes.values()), default=0)
    quote_age_ms = max(0, (time.time() - newest_quote) * 1000) if newest_quote else None
    inventory = readiness.get('inventory') or {}
    blockers = readiness.get('blockers', [])
    opportunities = _read_json(os.path.join(RUNTIME_DIR, 'latest_opportunities.json'))
    freshness_rows = [
        row for row in opportunities.get('all_opportunities', [])
        if row.get('pair_id', 'UPBIT_BINANCE') == pair_id
    ]
    freshness = RiskGuard.quote_freshness_status(
        max(freshness_rows, key=lambda row: row.get('best_net_surplus_bp', -9999))
        if freshness_rows else {'pair_id': pair_id}
    )
    if cfg.mode == 'live' and not cfg.live_freshness_observe_only:
        readiness['blockers'] = list(dict.fromkeys([
            *readiness.get('blockers', []), *freshness['live_freshness_blockers'],
        ]))
    elif cfg.mode == 'tiny_live' and cfg.tiny_live_freshness_observe_only:
        readiness['warnings'] = list(dict.fromkeys([
            *readiness.get('warnings', []), *freshness['tiny_live_freshness_blockers'],
        ]))
    readiness['ready'] = not readiness.get('blockers')
    readiness['live_guard'] = {
        'mode': cfg.mode,
        'enable_live_trading': cfg.enable_live_trading,
        'tiny_live_enabled': cfg.tiny_live_enabled,
        'live_order_enabled': cfg.live_order_enabled,
        'live_mode_enabled': cfg.live_mode_enabled,
        'withdrawals_enabled': cfg.withdrawals_enabled,
        'futures_hedge_enabled': cfg.futures_hedge_enabled,
        'manual_rebalance_only': cfg.manual_rebalance_only,
        'paper_pass': 'PAPER_PASS_REQUIRED' not in blockers,
        'key_status': readiness.get('key_status', {}),
        'inventory_status': 'OK' if inventory and not inventory.get('blockers') else 'BLOCKED',
        'quote_freshness': 'OK' if quote_age_ms is not None and quote_age_ms <= cfg.stale_quote_ms else 'STALE',
        'quote_age_ms': quote_age_ms,
        **freshness,
        'min_order_status': 'OK' if (
            cfg.upbit_bithumb_order_krw >= cfg.bithumb_min_order_krw
            if pair_id == 'UPBIT_BITHUMB' else cfg.tiny_live_order_krw >= 5000
        ) else 'BLOCKED',
    }
    return readiness


def _with_plan_quote_source(payload):
    plan = payload.get('plan') if isinstance(payload, dict) else None
    if not plan:
        return payload
    calc = {}
    if not plan.get('quote_source'):
        quotes = _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json'))
        quote = quotes.get(plan.get('symbol'), {})
        plan['quote_source'] = quote.get('source') or quote.get('upbit', {}).get('source') or quote.get('binance', {}).get('source') or 'unknown'
        calc = quote.get('calc', {})
    if plan.get('pair_id') == 'UPBIT_BITHUMB':
        opportunities = _read_json(os.path.join(RUNTIME_DIR, 'latest_opportunities.json'))
        calc = next((
            row for row in opportunities.get('all_opportunities', [])
            if row.get('pair_id') == 'UPBIT_BITHUMB' and row.get('symbol') == plan.get('symbol')
        ), calc)
    elif not calc:
        quotes = _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json'))
        calc = quotes.get(plan.get('symbol'), {}).get('calc', {})
    for key in (
        'selected_required_assets', 'order_krw_used', 'effective_qty',
        'max_fillable_qty_raw', 'selected_notional_krw', 'selected_qty',
        'selected_buy_price_krw', 'selected_sell_price_krw', 'notional_basis',
    ):
        if key in calc:
            plan[key] = calc[key]
    freshness = RiskGuard.quote_freshness_status({**calc, **plan})
    plan.update(freshness)
    freshness_blockers = (
        freshness['live_freshness_blockers']
        if cfg.mode == 'live' and not cfg.live_freshness_observe_only
        else []
    )
    freshness_warnings = (
        freshness['tiny_live_freshness_blockers']
        if cfg.mode == 'tiny_live' and cfg.tiny_live_freshness_observe_only
        else []
    )
    plan['blockers'] = list(dict.fromkeys([*plan.get('blockers', []), *freshness_blockers]))
    plan['warnings'] = list(dict.fromkeys([*plan.get('warnings', []), *freshness_warnings]))
    if freshness_blockers:
        plan['preflight_status'] = 'BLOCKED'
        plan['executable'] = False
        payload['ready'] = False
        payload['blockers'] = list(dict.fromkeys([*payload.get('blockers', []), *freshness_blockers]))
    iceberg = IcebergPlanner().build_placeholder_plan(plan, cfg)
    plan.update({
        'iceberg_required': iceberg['iceberg_required'],
        'iceberg_enabled': iceberg['enabled'],
        'iceberg_execution_enabled': iceberg['execution_enabled'],
        'iceberg_slice_count': iceberg['slice_count'],
        'iceberg_warning': ' / '.join(iceberg['warnings']),
    })
    return payload


def _preflight_payload(pair_id='UPBIT_BINANCE'):
    return _with_plan_quote_source(create_preflight_plan(pair_id))


def _last_plan_payload():
    return _with_plan_quote_source(_read_json(os.path.join(RUNTIME_DIR, 'tiny_live_last_preflight.json')))


def _pair_performance_payload():
    perf = _read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json'))
    return {
        'ok': True,
        'pair_summary': perf.get('pair_summary', {}),
        'best_pair_by_pnl': perf.get('best_pair_by_pnl', ''),
        'best_pair_by_win_rate': perf.get('best_pair_by_win_rate', ''),
        'most_active_pair': perf.get('most_active_pair', ''),
        'by_entry_reason': perf.get('by_entry_reason', {}),
        'best_entry_reason_by_pnl': perf.get('best_entry_reason_by_pnl', ''),
        'most_active_entry_reason': perf.get('most_active_entry_reason', ''),
        'updated_at': perf.get('updated_at', 0),
    }


def _is_actionable_signal(row):
    return (
        row.get('go_no_go') == 'GO'
        or row.get('reason_no_trade') == 'OK'
        or row.get('stale_recheck_status') in {
            'RECHECK_ACTIONABLE_FAST_PASS',
            'WIDE_SPREAD_RECHECK_ACTIONABLE',
        }
        or row.get('entry_reason') in {
            'NORMAL_GO',
            'RECHECK_ACTIONABLE',
            'WIDE_SPREAD_RECHECK_ACTIONABLE',
        }
    )


def _notional_sweep_summary(items, notionals):
    summary = {'item_count': len(items)}
    for notional in notionals:
        key = str(int(float(notional or 0)))
        best_symbol = ''
        best_net = None
        profitable = 0
        for item in items:
            for row in item.get('rows', []):
                if int(float(row.get('notional_krw', 0) or 0)) != int(float(notional or 0)):
                    continue
                net = float(row.get('expected_net_profit_krw', 0) or 0)
                if net > 0:
                    profitable += 1
                if best_net is None or net > best_net:
                    best_net = net
                    best_symbol = item.get('symbol', '')
        summary[f'best_symbol_{key}'] = best_symbol
        summary[f'profitable_count_{key}'] = profitable
    return summary


def _notional_sweep_payload(summary_only=False):
    now = time.time()
    notionals = [float(x) for x in (cfg.notional_sweep_notionals_krw or [10000, 50000, 100000])]
    opportunities = _read_json(os.path.join(RUNTIME_DIR, 'latest_opportunities.json'))
    source_updated_at = float(opportunities.get('updated_at', 0) or 0)
    cached = _NOTIONAL_SWEEP_CACHE.get('payload')
    cache_ttl = max(0.0, float(cfg.notional_sweep_cache_ttl_sec or 0))
    if (
        cached
        and _NOTIONAL_SWEEP_CACHE.get('source_updated_at') == source_updated_at
        and now - float(_NOTIONAL_SWEEP_CACHE.get('updated_at', 0) or 0) <= cache_ttl
    ):
        return {
            **cached,
            'cache_hit': True,
            'items': [] if summary_only else cached.get('items', []),
        }
    if not cfg.notional_sweep_enabled:
        payload = {
            'ok': True, 'error': '', 'blockers': [], 'updated_at': now,
            'enabled': False, 'notionals_krw': notionals, 'items': [],
            'summary': _notional_sweep_summary([], notionals),
        }
        _NOTIONAL_SWEEP_CACHE.update({'updated_at': now, 'source_updated_at': source_updated_at, 'payload': payload})
        return payload
    try:
        rows = opportunities.get('all_opportunities', [])
        if not isinstance(rows, list):
            rows = []
        if cfg.notional_sweep_include_only_actionable:
            rows = [row for row in rows if isinstance(row, dict) and _is_actionable_signal(row)]
        else:
            rows = [row for row in rows if isinstance(row, dict)]
        rows = sorted(
            rows,
            key=lambda row: float(row.get('best_net_surplus_bp', -999999) or -999999),
            reverse=True,
        )[:int(cfg.notional_sweep_max_symbols or 20)]
        items = [build_notional_sweep(row, notionals, cfg.mode, cfg) for row in rows]
        summary = _notional_sweep_summary(items, notionals)
        payload = {
            'ok': True,
            'error': '',
            'blockers': [],
            'enabled': True,
            'updated_at': now,
            'source_updated_at': source_updated_at,
            'cache_hit': False,
            'notionals_krw': notionals,
            'items': items,
            'summary': summary,
        }
    except Exception as exc:
        payload = {
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
            'blockers': ['NOTIONAL_SWEEP_ERROR'],
            'enabled': cfg.notional_sweep_enabled,
            'updated_at': now,
            'source_updated_at': source_updated_at,
            'notionals_krw': notionals,
            'items': [],
            'summary': _notional_sweep_summary([], notionals),
        }
    _NOTIONAL_SWEEP_CACHE.update({'updated_at': now, 'source_updated_at': source_updated_at, 'payload': payload})
    if summary_only:
        return {**payload, 'items': []}
    return payload


def _tiny_live_status_payload():
    status = tiny_live_executor.status()
    if status.get('last_preflight'):
        status['last_preflight'] = _with_plan_quote_source(status['last_preflight'])
    return status


def _tiny_live_preflight_payload(pair_id='UPBIT_BITHUMB'):
    return build_tiny_live_preflight(pair_id=pair_id or 'UPBIT_BITHUMB', check_balances=True)


def _telemetry_payload():
    telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
    if not cfg.runtime_store_enabled:
        telemetry['runtime_store_warning'] = 'RUNTIME_STORE_DISABLED_WARNING'
    sweep = _notional_sweep_payload(summary_only=True)
    summary = sweep.get('summary', {})
    notionals = sweep.get('notionals_krw', cfg.notional_sweep_notionals_krw)
    telemetry.update({
        'notional_sweep_enabled': cfg.notional_sweep_enabled,
        'notional_sweep_last_updated_at': sweep.get('updated_at', 0),
        'notional_sweep_item_count': summary.get('item_count', 0),
        'notional_sweep_last_error': sweep.get('error', ''),
        'notional_sweep_best_10000': summary.get('best_symbol_10000', ''),
        'notional_sweep_best_50000': summary.get('best_symbol_50000', ''),
        'notional_sweep_best_100000': summary.get('best_symbol_100000', ''),
        'notional_sweep_notionals_krw': notionals,
    })
    return {'ok': True, 'error': '', 'blockers': [], 'telemetry': telemetry}


def _execution_calibration_status_payload():
    cal_cfg = cfg.tiny_live_calibration if isinstance(cfg.tiny_live_calibration, dict) else {}
    status = _read_json(os.path.join(RUNTIME_DIR, 'execution_calibration_status.json'))
    keys = secrets_manager.get_key_status()
    key_missing = (
        keys.get('UPBIT_ACCESS_KEY') != 'Set'
        or keys.get('UPBIT_SECRET_KEY') != 'Set'
        or keys.get('BITHUMB_ACCESS_KEY') != 'Set'
        or keys.get('BITHUMB_SECRET_KEY') != 'Set'
    )
    enabled = bool(cal_cfg.get('enabled', False))
    blockers = []
    if enabled and not cfg.tiny_live_enabled:
        blockers.append('TINY_LIVE_DISABLED')
    if enabled and key_missing:
        blockers.append('LIVE_API_KEY_MISSING')
    if enabled and float(cal_cfg.get('max_order_krw', 0) or 0) > 10000:
        blockers.append('CALIBRATION_MAX_ORDER_TOO_HIGH')
    return {
        'ok': not blockers,
        'error': '',
        'blockers': blockers,
        'enabled': enabled,
        'tiny_live_enabled': cfg.tiny_live_enabled,
        'max_order_krw': cal_cfg.get('max_order_krw', 10000),
        'tiny_live_max_leg_quote_age_ms': cfg.tiny_live_max_leg_quote_age_ms,
        'live_max_leg_quote_age_ms': cfg.live_max_leg_quote_age_ms,
        'allowed_pairs': cal_cfg.get('allowed_pairs', []),
        'allowed_symbols': cal_cfg.get('allowed_symbols', []),
        'require_preflight_pass': cal_cfg.get('require_preflight_pass', True),
        'require_balance_check': cal_cfg.get('require_balance_check', True),
        'one_shot_first': cal_cfg.get('one_shot_first', True),
        'trade_count': status.get('trade_count', 0),
        'last_symbol': status.get('last_symbol', ''),
        'last_entry_reason': status.get('last_entry_reason', ''),
        'last_pnl_diff_krw': status.get('last_pnl_diff_krw'),
        'avg_pnl_diff_krw': status.get('avg_pnl_diff_krw'),
        'avg_actual_slippage_bp': status.get('avg_actual_slippage_bp'),
        'avg_ack_latency_ms': status.get('avg_ack_latency_ms'),
        'avg_submit_latency_ms': status.get('avg_submit_latency_ms'),
        'recommended_slippage_buffer_bp': status.get('recommended_slippage_buffer_bp'),
        'recommended_min_surplus_bp': status.get('recommended_min_surplus_bp'),
        'recommended_quote_age_cap_ms': status.get('recommended_quote_age_cap_ms'),
        'last_blocker': status.get('last_blocker', ''),
        'submit_attempt_count': status.get('submit_attempt_count', 0),
        'submit_success_count': status.get('submit_success_count', 0),
        'submit_fail_count': status.get('submit_fail_count', 0),
        'blocked_count': status.get('blocked_count', 0),
        'preflight_count': status.get('preflight_count', 0),
        'preflight_pass_count': status.get('preflight_pass_count', 0),
        'preflight_fail_count': status.get('preflight_fail_count', 0),
        'preflight_last_symbol': status.get('preflight_last_symbol', ''),
        'preflight_last_blocker': status.get('preflight_last_blocker', ''),
        'preflight_can_submit': status.get('preflight_can_submit', False),
        'preflight_balance_ok': status.get('preflight_balance_ok', False),
        'preflight_executor_ok': status.get('preflight_executor_ok', False),
        'preflight_expected_net_krw': status.get('preflight_expected_net_krw', 0),
        'preflight_total_fee_krw': status.get('preflight_total_fee_krw', 0),
        'preflight_total_slippage_bp': status.get('preflight_total_slippage_bp', 0),
        'session_submit_count': status.get('session_submit_count', 0),
        'session_success_count': status.get('session_success_count', 0),
        'session_fail_count': status.get('session_fail_count', 0),
        'updated_at': status.get('updated_at'),
    }


def _stale_recheck_status_payload():
    telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
    cache_statuses = [
        telemetry.get('rest_fallback_cache_status', {}) or {},
        telemetry.get('bithumb_quote_cache_status', {}) or {},
    ]
    def value_or(name, fallback):
        value = telemetry.get(name)
        return fallback if value is None else value
    def sum_cache_field(name):
        return sum(int(item.get(name, 0) or 0) for item in cache_statuses)
    def avg_cache_field(name):
        values = [
            float(item.get(name, 0) or 0)
            for item in cache_statuses if float(item.get(name, 0) or 0) > 0
        ]
        return round(sum(values) / len(values), 2) if values else 0.0
    def summary_bucket():
        return {
            'request_count': 0, 'fast_pass_count': 0, 'late_pass_count': 0,
            'actionable_fast_pass_count': 0, 'fail_count': 0, 'timeout_count': 0,
            '_elapsed_sum': 0.0, '_elapsed_count': 0,
            '_new_surplus_sum': 0.0, '_new_surplus_count': 0,
        }
    def finalize_bucket(bucket):
        done = (
            bucket['fast_pass_count'] + bucket['late_pass_count']
            + bucket['fail_count'] + bucket['timeout_count']
        )
        bucket['avg_elapsed_ms'] = round(bucket['_elapsed_sum'] / bucket['_elapsed_count'], 2) if bucket['_elapsed_count'] else 0.0
        bucket['avg_new_surplus_bp'] = round(bucket['_new_surplus_sum'] / bucket['_new_surplus_count'], 2) if bucket['_new_surplus_count'] else 0.0
        bucket['pass_ratio'] = round((bucket['fast_pass_count'] + bucket['late_pass_count']) / max(1, done), 4)
        bucket['fast_pass_ratio'] = round(bucket['fast_pass_count'] / max(1, done), 4)
        bucket['actionable_fast_pass_ratio'] = round(bucket['actionable_fast_pass_count'] / max(1, done), 4)
        bucket.pop('_elapsed_sum', None); bucket.pop('_elapsed_count', None)
        bucket.pop('_new_surplus_sum', None); bucket.pop('_new_surplus_count', None)
        return bucket
    def summarize_recent(items):
        by_pair = {}
        by_symbol = {}
        for item in items:
            pair = item.get('pair_id') or 'UNKNOWN'
            symbol = item.get('symbol') or ''
            buckets = [by_pair.setdefault(pair, summary_bucket())]
            if symbol:
                buckets.append(by_symbol.setdefault(symbol, summary_bucket()))
            status = item.get('status', '')
            for bucket in buckets:
                if status == 'RECHECK_REQUESTED':
                    bucket['request_count'] += 1
                elif status in ('RECHECK_FAST_PASS', 'RECHECK_ACTIONABLE_FAST_PASS'):
                    bucket['fast_pass_count'] += 1
                    if status == 'RECHECK_ACTIONABLE_FAST_PASS' or item.get('actionable_fast_pass'):
                        bucket['actionable_fast_pass_count'] += 1
                elif status == 'RECHECK_LATE_PASS':
                    bucket['late_pass_count'] += 1
                elif status == 'RECHECK_FAIL':
                    bucket['fail_count'] += 1
                elif status == 'RECHECK_TIMEOUT':
                    bucket['timeout_count'] += 1
                elapsed = item.get('elapsed_total_ms', item.get('elapsed_ms'))
                if elapsed is not None:
                    bucket['_elapsed_sum'] += float(elapsed or 0)
                    bucket['_elapsed_count'] += 1
                new_surplus = item.get('new_surplus_bp')
                if new_surplus is not None:
                    bucket['_new_surplus_sum'] += float(new_surplus or 0)
                    bucket['_new_surplus_count'] += 1
        symbol_rows = [{'symbol': symbol, **finalize_bucket(bucket)} for symbol, bucket in by_symbol.items()]
        symbol_rows.sort(
            key=lambda row: (
                row.get('actionable_fast_pass_count', 0),
                row.get('fast_pass_count', 0),
                row.get('request_count', 0),
            ),
            reverse=True,
        )
        return {pair: finalize_bucket(bucket) for pair, bucket in by_pair.items()}, symbol_rows[:10]
    recent = telemetry.get('stale_recheck_recent', [])[:100]
    fallback_by_pair, fallback_by_symbol = summarize_recent(recent)
    fallback_fast = sum(
        1 for item in recent
        if item.get('status') == 'RECHECK_PASS'
        and float(item.get('elapsed_total_ms', item.get('elapsed_ms', 0)) or 0) <= cfg.stale_recheck_fast_pass_ms
    )
    fallback_late = sum(
        1 for item in recent
        if item.get('status') == 'RECHECK_PASS'
        and float(item.get('elapsed_total_ms', item.get('elapsed_ms', 0)) or 0) > cfg.stale_recheck_fast_pass_ms
    )
    fast_pass_count = value_or('stale_recheck_fast_pass_count', fallback_fast)
    late_pass_count = value_or('stale_recheck_late_pass_count', fallback_late)
    if not fast_pass_count and not late_pass_count and (fallback_fast or fallback_late):
        fast_pass_count = fallback_fast
        late_pass_count = fallback_late
    avg_total_elapsed_ms = value_or(
        'stale_recheck_avg_total_elapsed_ms',
        telemetry.get('stale_recheck_avg_elapsed_ms', 0),
    )
    if not avg_total_elapsed_ms:
        avg_total_elapsed_ms = telemetry.get('stale_recheck_avg_elapsed_ms', 0)
    timeout_count = telemetry.get('stale_recheck_timeout_count', 0)
    done_count = (
        fast_pass_count + late_pass_count
        + telemetry.get('stale_recheck_fail_count', 0) + timeout_count
    )
    timeout_ratio = value_or(
        'stale_recheck_timeout_ratio',
        round(timeout_count / max(1, done_count), 4),
    )
    return {
        'ok': True,
        'error': '',
        'blockers': [],
        'enabled': telemetry.get('stale_recheck_enabled', cfg.stale_recheck_enabled),
        'paper_only': telemetry.get('stale_recheck_paper_only', cfg.stale_recheck_paper_only),
        'request_count': telemetry.get('stale_recheck_request_count', 0),
        'execute_count': telemetry.get('stale_recheck_execute_count', 0),
        'pass_count': telemetry.get('stale_recheck_pass_count', 0),
        'fast_pass_count': fast_pass_count,
        'late_pass_count': late_pass_count,
        'actionable_fast_pass_count': telemetry.get('stale_recheck_actionable_fast_pass_count', 0),
        'fail_count': telemetry.get('stale_recheck_fail_count', 0),
        'timeout_count': timeout_count,
        'skip_cooldown_count': telemetry.get('stale_recheck_skip_cooldown_count', 0),
        'skip_rate_limit_count': telemetry.get('stale_recheck_skip_rate_limit_count', 0),
        'queue_size': telemetry.get('stale_recheck_queue_size', 0),
        'priority_worker_wake_count': value_or(
            'stale_recheck_priority_worker_wake_count',
            sum_cache_field('priority_worker_wake_count'),
        ),
        'priority_symbol_fetch_count': value_or(
            'stale_recheck_priority_symbol_fetch_count',
            sum_cache_field('priority_symbol_fetch_count'),
        ),
        'priority_full_refresh_fallback_count': value_or(
            'stale_recheck_priority_full_refresh_fallback_count',
            sum_cache_field('priority_full_refresh_fallback_count'),
        ),
        'priority_fetch_avg_ms': value_or(
            'stale_recheck_priority_fetch_avg_ms',
            avg_cache_field('priority_fetch_avg_ms'),
        ),
        'priority_fetch_last_ms': value_or(
            'stale_recheck_priority_fetch_last_ms',
            max(float(item.get('priority_fetch_last_ms', 0) or 0) for item in cache_statuses),
        ),
        'recheck_inflight_count': value_or(
            'stale_recheck_inflight_count',
            sum_cache_field('recheck_inflight_count'),
        ),
        'recheck_deduped_count': value_or(
            'stale_recheck_deduped_count',
            sum_cache_field('recheck_deduped_count'),
        ),
        'completed_handoff_count': telemetry.get('stale_recheck_completed_handoff_count', 0),
        'completed_handoff_pending_match_count': telemetry.get('stale_recheck_completed_handoff_pending_match_count', 0),
        'completed_handoff_unmatched_count': telemetry.get('stale_recheck_completed_handoff_unmatched_count', 0),
        'avg_handoff_fetch_ms': telemetry.get('stale_recheck_avg_handoff_fetch_ms', 0),
        'avg_handoff_decision_wait_ms': telemetry.get('stale_recheck_avg_handoff_decision_wait_ms', 0),
        'last_symbol': telemetry.get('stale_recheck_last_symbol', ''),
        'last_status': telemetry.get('stale_recheck_last_status', 'NONE'),
        'avg_elapsed_ms': telemetry.get('stale_recheck_avg_elapsed_ms', 0),
        'avg_total_elapsed_ms': avg_total_elapsed_ms,
        'avg_queue_wait_ms': value_or('stale_recheck_avg_queue_wait_ms', 0),
        'avg_fetch_ms': value_or('stale_recheck_avg_fetch_ms', 0),
        'avg_decision_wait_ms': value_or('stale_recheck_avg_decision_wait_ms', 0),
        'pass_ratio': telemetry.get('stale_recheck_pass_ratio', 0),
        'fast_pass_ratio': value_or(
            'stale_recheck_fast_pass_ratio',
            round(fast_pass_count / max(1, telemetry.get('stale_recheck_pass_count', 0)), 4),
        ),
        'late_pass_ratio': value_or(
            'stale_recheck_late_pass_ratio',
            round(late_pass_count / max(1, telemetry.get('stale_recheck_pass_count', 0)), 4),
        ),
        'actionable_fast_pass_ratio': value_or('stale_recheck_actionable_fast_pass_ratio', 0),
        'timeout_ratio': timeout_ratio,
        'health': telemetry.get('stale_recheck_health', 'WATCH'),
        'stale_recheck_health': telemetry.get('stale_recheck_health', 'WATCH'),
        'avg_new_surplus_bp': telemetry.get('stale_recheck_avg_new_surplus_bp', 0),
        'by_pair': telemetry.get('stale_recheck_by_pair') or fallback_by_pair,
        'by_symbol': telemetry.get('stale_recheck_by_symbol') or fallback_by_symbol,
        'recent': recent[:20],
    }


def _rate_limit_status_payload():
    telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
    status = telemetry.get('rate_limit_status') or rate_limiter.get_status()
    return {
        'ok': True, 'error': '', 'blockers': [],
        'rate_limit': status,
        'rest_fallback_count': telemetry.get('rest_fallback_count', 0),
        'rest_fallback_skip_count': telemetry.get('rest_fallback_skip_count', 0),
    }


def _decisions_payload():
    snapshot = _read_json(os.path.join(RUNTIME_DIR, 'latest_decisions.json'))
    return {
        'ok': True, 'error': '', 'blockers': [],
        'updated_at': snapshot.get('updated_at'),
        'decisions': list(reversed(snapshot.get('decisions', [])))[:100],
    }


def _last_session_payload():
    return {
        'ok': True, 'error': '', 'blockers': [],
        **_read_json(os.path.join(RUNTIME_DIR, 'last_session_summary.json')),
    }


def _order_tracker_status_payload():
    return {'ok': True, 'error': '', 'blockers': [], 'order_tracker': tiny_live_executor.tracker.to_dict()}


def _order_tracker_recent_payload():
    return {'ok': True, 'error': '', 'blockers': [], 'events': tiny_live_executor.tracker.recent()}


def _emergency_status_payload():
    tracker = tiny_live_executor.tracker.to_dict()
    return {'ok': True, 'error': '', 'blockers': [], 'emergency': tiny_live_executor.emergency.status(tracker)}


def _strategy_pairs_payload():
    return {'ok': True, 'error': '', 'blockers': [], 'pairs': venue_pair_payload()}


def _opportunities_payload():
    return {
        'ok': True, 'error': '', 'blockers': [],
        **_read_json(os.path.join(RUNTIME_DIR, 'latest_opportunities.json')),
    }


def _bithumb_status_payload():
    keys = secrets_manager.get_key_status()
    return {
        'ok': True, 'error': '', 'blockers': [],
        'public_enabled': cfg.bithumb_public_enabled,
        'private_enabled': cfg.bithumb_private_enabled,
        'upbit_bithumb_live_enabled': cfg.upbit_bithumb_live_enabled,
        'key_status': {
            'BITHUMB_ACCESS_KEY': keys['BITHUMB_ACCESS_KEY'],
            'BITHUMB_SECRET_KEY': keys['BITHUMB_SECRET_KEY'],
        },
        'withdrawals_enabled': False, 'deposits_enabled': False,
    }


def _bithumb_cache_status_payload():
    telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
    cache = telemetry.get('bithumb_quote_cache_status', {})
    return {
        'ok': True, 'error': '', 'blockers': [],
        'cache': cache,
        'stale_grace_count': cache.get('stale_grace_count', 0),
        'stale_hard_count': cache.get('stale_hard_count', 0),
        'last_good_age_ms': cache.get('last_good_age_ms'),
        'skipped_bithumb_symbol_count': telemetry.get('skipped_bithumb_symbol_count', 0),
        'skipped_bithumb_quote_reasons': telemetry.get('skipped_bithumb_quote_reasons', {}),
        'quote_history_key_count': telemetry.get('quote_history_key_count', 0),
        'quote_history_cleanup_count': telemetry.get('quote_history_cleanup_count', 0),
        'skipped_bithumb_symbol_count_last_loop': telemetry.get('skipped_bithumb_symbol_count_last_loop', 0),
        'skipped_bithumb_quote_reasons_last_loop': telemetry.get('skipped_bithumb_quote_reasons_last_loop', {}),
        'bithumb_stale_grace_used_count': telemetry.get('bithumb_stale_grace_used_count', 0),
    }


def _rest_fallback_cache_status_payload():
    telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
    return {
        'ok': True, 'error': '', 'blockers': [],
        'cache': telemetry.get('rest_fallback_cache_status', {}),
        'rest_direct_call_count': telemetry.get('rest_direct_call_count', 0),
        'rest_fallback_cache_hit_count': telemetry.get('rest_fallback_cache_hit_count', 0),
        'rest_fallback_cache_miss_count': telemetry.get('rest_fallback_cache_miss_count', 0),
        'rest_fallback_cache_stale_count': telemetry.get('rest_fallback_cache_stale_count', 0),
        'rest_fallback_older_than_ws_drop_count': telemetry.get('rest_fallback_older_than_ws_drop_count', 0),
        'upbit_rest_call_count_total': telemetry.get('upbit_rest_call_count_total', 0),
        'upbit_rest_call_count_rest_cache': telemetry.get('upbit_rest_call_count_rest_cache', 0),
        'upbit_rest_call_count_scanner': telemetry.get('upbit_rest_call_count_scanner', 0),
        'upbit_rest_call_count_fx': telemetry.get('upbit_rest_call_count_fx', 0),
        'upbit_rest_call_count_other': telemetry.get('upbit_rest_call_count_other', 0),
        'upbit_429_count_rest_cache': telemetry.get('upbit_429_count_rest_cache', 0),
        'upbit_429_count_scanner': telemetry.get('upbit_429_count_scanner', 0),
        'upbit_429_count_fx': telemetry.get('upbit_429_count_fx', 0),
        'upbit_429_count_other': telemetry.get('upbit_429_count_other', 0),
        'upbit_ws_fresh_skip_count': telemetry.get(
            'rest_fallback_cache_status', {}
        ).get('upbit_ws_fresh_skip_count', 0),
        'skipped_bithumb_symbol_count_last_loop': telemetry.get('skipped_bithumb_symbol_count_last_loop', 0),
        'skipped_bithumb_quote_reasons_last_loop': telemetry.get('skipped_bithumb_quote_reasons_last_loop', {}),
        'p95_quote_age_ms': telemetry.get('p95_quote_age_ms', 0),
        'p95_quote_fetch_latency_ms': telemetry.get('p95_quote_fetch_latency_ms', 0),
        'stale_symbol_ratio': telemetry.get('stale_symbol_ratio', 0),
    }


def _bithumb_balances_payload():
    return BithumbPrivateClient().get_balances()


def _market_scanner_payload():
    return {
        'ok': True, 'error': '', 'blockers': [],
        **_read_json(os.path.join(RUNTIME_DIR, 'market_scanner.json')),
    }


def _runtime_store_status_payload():
    status = _read_json(os.path.join(RUNTIME_DIR, 'runtime_store_status.json'))
    last_snapshot_at = float(status.get('last_snapshot_at', 0) or 0)
    if last_snapshot_at:
        status['snapshot_age_sec'] = round(max(0.0, time.time() - last_snapshot_at), 2)
    return {
        'ok': True, 'error': '', 'blockers': [],
        'enabled': cfg.runtime_store_enabled,
        'snapshot_interval_sec': cfg.runtime_snapshot_interval_sec,
        **status,
    }


def _slippage_model_payload():
    return {
        'ok': True, 'error': '', 'blockers': [],
        'use_dynamic_slippage': cfg.use_dynamic_slippage,
        'base_slippage_bp': cfg.base_slippage_bp,
        'max_dynamic_slippage_bp': cfg.max_dynamic_slippage_bp,
        'depth_safety_multiplier': cfg.depth_safety_multiplier,
        'paper_latency_sim_enabled': cfg.paper_latency_sim_enabled,
        'paper_upbit_latency_ms': cfg.paper_upbit_latency_ms,
        'paper_bithumb_latency_ms': cfg.paper_bithumb_latency_ms,
        'paper_binance_latency_ms': cfg.paper_binance_latency_ms,
        'paper_latency_jitter_ms': cfg.paper_latency_jitter_ms,
        'paper_slippage_stress_bp': cfg.paper_slippage_stress_bp,
    }


def _iceberg_status_payload():
    return {
        'ok': True, 'error': '', 'blockers': [],
        'iceberg': IcebergPlanner().get_status(cfg),
    }


class KarbHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def _is_localhost(self):
        return self.client_address[0] in ('127.0.0.1', '::1', 'localhost')

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _send_403(self):
        self._send_json({'ok': False, 'error': 'localhost only', 'blockers': [], 'warnings': []}, 403)

    def _send_guarded_json(self, action):
        try:
            data = action()
            if not isinstance(data, dict):
                data = {'result': data}
            data.setdefault('ok', bool(data.get('ready', True)))
            data.setdefault('error', '')
            data.setdefault('blockers', [])
            data.setdefault('warnings', [])
            self._send_json(data)
        except Exception as exc:
            self._send_json({
                'ok': False,
                'status': 'BLOCKED',
                'blockers': ['INTERNAL_ERROR'],
                'error': type(exc).__name__,
            }, 500)

    def _request_json(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length).decode('utf-8')) if length else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == '/api/engine/status':
            self._send_json(process_manager.get_engine_status())

        elif self.path == '/api/state':
            self._send_json(_read_json(os.path.join(RUNTIME_DIR, 'state.json')))

        elif path == '/api/data':
            self._send_json({
                'state':   _read_json(os.path.join(RUNTIME_DIR, 'state.json')),
                'quotes':  _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json')),
                'performance': _read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json')),
                'limits': {
                    'min_net_surplus_bp': cfg.min_net_surplus_bp,
                    'daily_loss_limit_krw': cfg.daily_loss_limit_krw,
                },
                'control': ctrl_module.get_control_state(),
                'engine':  process_manager.get_engine_status(),
                'strategy': _read_json(os.path.join(RUNTIME_DIR, 'latest_opportunities.json')),
                'strategy_pairs': venue_pair_payload(),
            })

        elif self.path == '/api/perf':
            perf = _read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json'))
            exits = _recent_real_exit_trades(20)
            self._send_json({'performance': perf, 'recent_trades': exits})

        elif self.path == '/api/performance':
            self._send_json(_read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json')))

        elif self.path == '/api/performance/pairs':
            self._send_guarded_json(_pair_performance_payload)

        elif self.path == '/api/telemetry':
            self._send_guarded_json(_telemetry_payload)

        elif path == '/api/notional-sweep':
            self._send_guarded_json(_notional_sweep_payload)

        elif self.path == '/api/execution-calibration/status':
            self._send_guarded_json(_execution_calibration_status_payload)

        elif self.path == '/api/rate-limit/status':
            self._send_guarded_json(_rate_limit_status_payload)

        elif self.path == '/api/stale-recheck/status':
            self._send_guarded_json(_stale_recheck_status_payload)

        elif self.path == '/api/decisions/recent':
            self._send_guarded_json(_decisions_payload)

        elif path == '/api/inventory':
            pair_id = query.get('pair', ['UPBIT_BINANCE'])[0]
            self._send_json(get_inventory_summary(pair_id))

        elif path == '/api/live/readiness':
            pair_id = query.get('pair', ['UPBIT_BINANCE'])[0]
            self._send_guarded_json(lambda: _live_readiness_payload(pair_id))

        elif self.path == '/api/execution/preflight':
            self._send_guarded_json(_preflight_payload)

        elif self.path == '/api/execution/last-plan':
            self._send_guarded_json(_last_plan_payload)

        elif self.path == '/api/tiny-live/status':
            self._send_guarded_json(_tiny_live_status_payload)

        elif path == '/api/tiny-live/preflight':
            pair_id = query.get('pair', ['UPBIT_BITHUMB'])[0]
            self._send_guarded_json(lambda: _tiny_live_preflight_payload(pair_id))

        elif self.path == '/api/order-tracker/status':
            self._send_guarded_json(_order_tracker_status_payload)

        elif self.path == '/api/order-tracker/recent':
            self._send_guarded_json(_order_tracker_recent_payload)

        elif self.path == '/api/emergency/status':
            self._send_guarded_json(_emergency_status_payload)

        elif self.path == '/api/strategy/pairs':
            self._send_guarded_json(_strategy_pairs_payload)

        elif path == '/api/opportunities':
            self._send_guarded_json(_opportunities_payload)

        elif path == '/api/bithumb/status':
            self._send_guarded_json(_bithumb_status_payload)

        elif path == '/api/bithumb/cache-status':
            self._send_guarded_json(_bithumb_cache_status_payload)

        elif path == '/api/rest-fallback-cache/status':
            self._send_guarded_json(_rest_fallback_cache_status_payload)

        elif path == '/api/bithumb/balances':
            self._send_guarded_json(_bithumb_balances_payload)

        elif path == '/api/market/scanner':
            self._send_guarded_json(_market_scanner_payload)

        elif path == '/api/runtime-store/status':
            self._send_guarded_json(_runtime_store_status_payload)

        elif path == '/api/slippage/model':
            self._send_guarded_json(_slippage_model_payload)

        elif path == '/api/iceberg/status':
            self._send_guarded_json(_iceberg_status_payload)

        elif self.path == '/api/trades/recent':
            self._send_json({'trades': _recent_real_exit_trades(20)})

        elif self.path == '/api/session/last':
            self._send_guarded_json(_last_session_payload)

        elif self.path == '/api/keys/status':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_json(secrets_manager.get_key_status())

        elif self.path == '/api/health':
            telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
            self._send_json({
                'status': 'ok',
                'ws_connected': telemetry.get('ws_connected', False),
                'upbit_last_msg_age_ms': telemetry.get('upbit_last_msg_age_ms'),
                'binance_last_msg_age_ms': telemetry.get('binance_last_msg_age_ms'),
                'reconnect_count': telemetry.get('ws_reconnect_count', 0),
                'last_error': telemetry.get('ws_last_error', ''),
                'out_of_order_drop_count': telemetry.get('out_of_order_drop_count', 0),
                'runtime_store_warning': (
                    'RUNTIME_STORE_DISABLED_WARNING' if not cfg.runtime_store_enabled else ''
                ),
            })

        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/execution/preflight':
            if not self._is_localhost():
                self._send_403()
                return
            body = self._request_json()
            self._send_guarded_json(lambda: _preflight_payload(body.get('pair_id', 'UPBIT_BINANCE')))

        elif self.path == '/api/tiny-live/arm':
            if not self._is_localhost():
                self._send_403()
                return
            body = self._request_json()
            self._send_guarded_json(lambda: tiny_live_executor.arm(body.get('pair_id', 'UPBIT_BINANCE')))

        elif self.path == '/api/tiny-live/disarm':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_guarded_json(tiny_live_executor.disarm)

        elif self.path == '/api/tiny-live/execute-once':
            if not self._is_localhost():
                self._send_403()
                return
            body = self._request_json()
            self._send_guarded_json(lambda: tiny_live_executor.execute_once(body.get('pair_id')))

        elif self.path == '/api/emergency/manual-clear':
            if not self._is_localhost():
                self._send_403()
                return
            length = int(self.headers.get('Content-Length', 0))
            try:
                body = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
                reason = str(body.get('reason', '')).strip()
                if not reason:
                    self._send_json({'ok': False, 'error': 'CLEARING_REASON_REQUIRED', 'blockers': ['CLEARING_REASON_REQUIRED'], 'warnings': []}, 400)
                    return
                self._send_guarded_json(lambda: tiny_live_executor.manual_clear_partial_risk(reason))
            except Exception as exc:
                self._send_json({'ok': False, 'error': type(exc).__name__, 'blockers': ['MANUAL_CLEAR_FAILED'], 'warnings': []}, 400)

        elif self.path == '/api/emergency/preview':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_guarded_json(tiny_live_executor.preview_emergency)

        elif self.path == '/api/engine/start':
            if not self._is_localhost():
                self._send_403()
                return
            length = int(self.headers.get('Content-Length', 0))
            body_bytes = self.rfile.read(length)
            try:
                body = json.loads(body_bytes.decode('utf-8'))
            except Exception:
                body = {}
            mode = body.get('mode', 'paper')
            if mode == 'live':
                self._send_json({'ok': False, 'message': 'Full live mode is disabled. Use guarded tiny_live only.'}, 403)
                return
            result = process_manager.start_engine(mode)
            self._send_json(result)

        elif self.path == '/api/engine/stop':
            if not self._is_localhost():
                self._send_403()
                return
            result = process_manager.stop_engine()
            self._send_json(result)

        elif self.path == '/api/stop':
            if not self._is_localhost():
                self._send_403()
                return
            result = ctrl_module.request_stop()
            self._send_json({
                'ok': True,
                'message': 'Stop requested. Engine will finalize session report.',
                'run_id': result.get('run_id', ''),
            })

        elif self.path == '/api/keys/save':
            if not self._is_localhost():
                self._send_403()
                return
            length = int(self.headers.get('Content-Length', 0))
            body_bytes = self.rfile.read(length)
            try:
                body = json.loads(body_bytes.decode('utf-8'))
            except Exception:
                self._send_json({'ok': False, 'message': '잘못된 JSON'}, 400)
                return
            result = secrets_manager.save_keys(
                upbit_access  =body.get('upbit_access_key', ''),
                upbit_secret  =body.get('upbit_secret_key', ''),
                binance_api   =body.get('binance_api_key', ''),
                binance_secret=body.get('binance_api_secret', ''),
                bithumb_access=body.get('bithumb_access_key', ''),
                bithumb_secret=body.get('bithumb_secret_key', ''),
            )
            self._send_json(result)
        else:
            self.send_error(404)


def run(port=8000, once=False):
    httpd = ThreadingHTTPServer(('', port), KarbHandler)
    print(f"[WebServer] http://localhost:{port}")
    if once:
        httpd.handle_request()
    else:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="KARB Web Server")
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    run(port=args.port, once=args.once)
