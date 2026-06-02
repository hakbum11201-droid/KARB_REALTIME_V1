import argparse
import json
import os
import queue
import sys
import threading
import time
from collections import deque

from config import cfg
from secrets_manager import assert_live_credentials_available
from upbit_public import UpbitPublic
from binance_public import BinancePublic
from bithumb_public import BithumbPublic
from fx_oracle import FxOracle
from quote_engine import QuoteEngine
from arb_calculator import ArbCalculator
from inventory_manager import InventoryManager
from risk_guard import RiskGuard
from paper_engine import PaperEngine
from event_logger import EventLogger
from performance_tracker import PerformanceTracker
from bounded_collector import BoundedCollector
import control
from session_analyzer import SessionAnalyzer
from ws_market_data import WebSocketMarketData
from strategy_selector import StrategySelector
from runtime_store import RuntimeStore
from market_scanner import MarketScanner
from paper_fill_simulator import simulate_paper_fill
from rate_limiter import rate_limiter
from bithumb_quote_cache import BithumbQuoteCache
from rest_fallback_cache import RestFallbackQuoteCache


def _decision_record(calc_res, reason, is_safe, quote_source, quote_age_ms):
    surplus = calc_res.get('best_net_surplus_bp', -9999)
    direction = calc_res.get('best_direction', '')
    return {
        'time': time.time(),
        'pair_id': calc_res.get('pair_id', 'UPBIT_BINANCE'),
        'paper_only': bool(calc_res.get('paper_only')),
        'symbol': calc_res.get('symbol', ''),
        'direction': direction,
        'direction_label': (
            'A_KIMCHI' if direction == 'A'
            else 'B_REVERSE_KIMCHI' if direction == 'B'
            else direction
        ),
        'best_net_surplus_bp': surplus,
        'expected_net_profit_krw': calc_res.get('net_expected_profit_krw', 0),
        'reason_no_trade': reason,
        'threshold_gap_bp': round(max(0, cfg.min_net_surplus_bp - surplus), 4),
        'quote_source': quote_source,
        'quote_age_ms': round(float(quote_age_ms or 0), 2),
        'go_no_go': 'GO' if is_safe else 'NO-GO',
        'blockers': [] if is_safe else [reason],
        'dynamic_slippage_bp': calc_res.get('dynamic_slippage_bp', cfg.slippage_bp),
        'liquidity_class': calc_res.get('liquidity_class', 'NORMAL'),
        'latency_used_ms': calc_res.get('latency_used_ms', 0),
        'fill_quality': calc_res.get('fill_quality', calc_res.get('paper_edge_quality', 'PENDING')),
    }


def _write_json(path: str, data) -> None:
    """overwrite 전용."""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _percentile(values, pct: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    idx = min(int(len(sorted_values) * pct / 100), len(sorted_values) - 1)
    return round(sorted_values[idx], 2)


def _bithumb_skip_reason(upbit_quote, bithumb_quote) -> str:
    if not upbit_quote:
        return 'MISSING_UPBIT_QUOTE'
    if not bithumb_quote:
        return 'MISSING_BITHUMB_QUOTE'
    if bithumb_quote.get('stale'):
        return 'STALE_BITHUMB_QUOTE'
    if not bithumb_quote.get('ok'):
        return 'BITHUMB_QUOTE_NOT_OK'
    return ''


def _cleanup_quote_history(quote_history, active_symbols) -> int:
    keep = set(active_symbols)
    keep.update(f'UPBIT_BITHUMB:{symbol}' for symbol in active_symbols)
    stale_keys = [key for key in quote_history if key not in keep]
    for key in stale_keys:
        del quote_history[key]
    return len(stale_keys)


def _light_quote(quote):
    return {
        key: quote.get(key)
        for key in ('bid', 'ask', 'bid_size', 'ask_size', 'ts', 'source')
        if key in quote
    }


def _history_row(**quotes):
    return {'_ts': time.time(), **{venue: _light_quote(quote) for venue, quote in quotes.items()}}


def _memory_telemetry(enabled):
    if not enabled:
        return {'process_memory_mb': None, 'memory_metric_available': False}
    try:
        import psutil
        process_memory_mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
        return {'process_memory_mb': round(process_memory_mb, 2), 'memory_metric_available': True}
    except Exception:
        return {'process_memory_mb': None, 'memory_metric_available': False}


def _upbit_rest_metrics(rate_limit_status):
    upbit = rate_limit_status.get('exchanges', {}).get('upbit', {})
    calls = upbit.get('rest_call_counts', {})
    errors = upbit.get('api_429_counts', {})
    return {
        'upbit_rest_call_count_total': sum(calls.values()),
        'upbit_rest_call_count_rest_cache': calls.get('rest_cache', 0),
        'upbit_rest_call_count_scanner': calls.get('scanner', 0),
        'upbit_rest_call_count_fx': calls.get('fx', 0),
        'upbit_rest_call_count_other': calls.get('other', 0),
        'upbit_429_count_rest_cache': errors.get('rest_cache', 0),
        'upbit_429_count_scanner': errors.get('scanner', 0),
        'upbit_429_count_fx': errors.get('fx', 0),
        'upbit_429_count_other': errors.get('other', 0),
    }


def _merge_scanner_snapshot(current_snapshot, next_snapshot, active_symbols):
    if next_snapshot.get('source') != 'fallback':
        return next_snapshot
    return {
        **next_snapshot,
        'active_symbols': list(active_symbols),
        'standby_symbols': current_snapshot.get('standby_symbols', []),
        'common_upbit_binance': current_snapshot.get('common_upbit_binance', list(active_symbols)),
        'common_upbit_bithumb': current_snapshot.get('common_upbit_bithumb', list(active_symbols)),
    }


def _start_background_scanner_refresh(market_scanner, timeout_sec):
    results = queue.Queue(maxsize=1)

    def refresh():
        snapshot = market_scanner.scan_with_timeout(timeout_sec)
        try:
            results.put(snapshot, block=False)
        except queue.Full:
            pass

    thread = threading.Thread(target=refresh, name='market-scanner-refresh', daemon=True)
    thread.start()
    return thread, results


def _stale_recheck_key(item):
    return (item.get('pair_id', 'UPBIT_BINANCE'), item.get('symbol', ''))


def _stale_recheck_candidate(item):
    if not cfg.stale_recheck_enabled:
        return False
    if cfg.stale_recheck_paper_only and cfg.mode != 'paper':
        return False
    if item.get('pair_id', 'UPBIT_BINANCE') not in cfg.stale_recheck_pair_ids:
        return False
    stale_like = (
        item.get('reason_no_trade') == 'STALE_QUOTE'
        or item.get('has_stale_quote')
        or item.get('stale')
        or item.get('quote_source') == 'rest'
    )
    if not stale_like:
        return False
    threshold = cfg.min_net_surplus_bp + cfg.stale_recheck_min_surplus_bp_extra
    if float(item.get('best_net_surplus_bp', -9999) or -9999) < threshold:
        return False
    if float(item.get('net_expected_profit_krw', 0) or 0) < cfg.stale_recheck_min_net_profit_krw:
        return False
    return item.get('liquidity_class', 'NORMAL') in cfg.stale_recheck_allowed_liquidity


def _record_stale_recheck_event(recent, event):
    recent.appendleft({'time': time.time(), **event})


def _stale_recheck_breakdown(request, decided_at):
    requested_at = float(request.get('requested_at', 0) or 0)
    queued_at = request.get('queued_at')
    refresh_started_at = request.get('refresh_started_at')
    refreshed_at = request.get('refreshed_at')
    if not queued_at:
        queued_at = requested_at or None
    return {
        'requested_at': requested_at or None,
        'queued_at': queued_at,
        'refresh_started_at': refresh_started_at,
        'refreshed_at': refreshed_at,
        'decided_at': decided_at,
        'elapsed_total_ms': round(max(0.0, decided_at - requested_at) * 1000, 2) if requested_at else None,
        'elapsed_queue_wait_ms': (
            round(max(0.0, float(refresh_started_at) - float(queued_at)) * 1000, 2)
            if refresh_started_at and queued_at else None
        ),
        'elapsed_fetch_ms': (
            round(max(0.0, float(refreshed_at) - float(refresh_started_at)) * 1000, 2)
            if refreshed_at and refresh_started_at else None
        ),
        'elapsed_decision_wait_ms': (
            round(max(0.0, decided_at - float(refreshed_at)) * 1000, 2)
            if refreshed_at else round(max(0.0, decided_at - requested_at) * 1000, 2) if requested_at else None
        ),
    }


def _stale_recheck_pass_status(elapsed_ms):
    return (
        'RECHECK_FAST_PASS'
        if float(elapsed_ms or 0) <= cfg.stale_recheck_fast_pass_ms
        else 'RECHECK_LATE_PASS'
    )


def _request_stale_recheck(item, pending, request_times, counters, recent, rest_cache, bithumb_cache):
    now = time.time()
    while request_times and now - request_times[0] > 60:
        request_times.popleft()
    key = _stale_recheck_key(item)
    if key in pending:
        counters['skip_cooldown'] += 1
        return {'stale_recheck_status': 'REQUESTED', 'stale_recheck_reason': 'RECHECK_ALREADY_PENDING'}
    if len(request_times) >= cfg.stale_recheck_max_per_minute:
        counters['skip_rate_limit'] += 1
        return {'stale_recheck_status': 'NONE', 'stale_recheck_reason': 'RECHECK_RATE_LIMIT'}
    pair_id, symbol = key
    if pair_id == 'UPBIT_BITHUMB':
        result = bithumb_cache.request_priority_refresh(symbol, reason=item.get('reason_no_trade'))
    else:
        result = rest_cache.request_priority_refresh(pair_id, symbol, reason=item.get('reason_no_trade'))
    if result.get('queued'):
        request_times.append(now)
        threshold = cfg.min_net_surplus_bp + cfg.stale_recheck_min_surplus_bp_extra
        pending[key] = {
            'pair_id': pair_id,
            'symbol': symbol,
            'requested_at': now,
            'queued_at': now,
            'refresh_started_at': None,
            'refreshed_at': None,
            'threshold_bp': threshold,
            'original_surplus_bp': float(item.get('best_net_surplus_bp', 0) or 0),
            'original_net_krw': float(item.get('net_expected_profit_krw', 0) or 0),
            'reason': item.get('reason_no_trade', ''),
        }
        counters['request'] += 1
        _record_stale_recheck_event(recent, {
            'pair_id': pair_id, 'symbol': symbol, 'status': 'RECHECK_REQUESTED',
            'original_surplus_bp': pending[key]['original_surplus_bp'],
            'original_net_krw': pending[key]['original_net_krw'],
            **_stale_recheck_breakdown(pending[key], now),
        })
        return {
            'stale_recheck_status': 'REQUESTED',
            'stale_recheck_original_surplus_bp': pending[key]['original_surplus_bp'],
            'stale_recheck_original_net_krw': pending[key]['original_net_krw'],
            'stale_recheck_elapsed_total_ms': 0.0,
            'stale_recheck_elapsed_queue_wait_ms': None,
            'stale_recheck_elapsed_fetch_ms': None,
            'stale_recheck_elapsed_decision_wait_ms': 0.0,
            'stale_recheck_reason': 'RECHECK_REQUESTED',
        }
    reason = result.get('reason', 'RECHECK_NOT_QUEUED')
    if 'COOLDOWN' in reason or 'QUEUED' in reason:
        counters['skip_cooldown'] += 1
    else:
        counters['skip_rate_limit'] += 1
    return {'stale_recheck_status': 'NONE', 'stale_recheck_reason': reason}


def _resolve_stale_recheck(item, pending, counters, recent):
    key = _stale_recheck_key(item)
    request = pending.get(key)
    if not request:
        return {}
    now = time.time()
    fresh = (
        item.get('reason_no_trade') != 'STALE_QUOTE'
        and not item.get('has_stale_quote')
        and not item.get('stale')
    )
    elapsed_ms = round((now - request['requested_at']) * 1000, 2)
    breakdown = _stale_recheck_breakdown(request, now)
    if not fresh:
        if now - request['requested_at'] <= cfg.stale_recheck_result_ttl_sec:
            return {
                'stale_recheck_status': 'REQUESTED',
                'stale_recheck_elapsed_ms': elapsed_ms,
                'stale_recheck_elapsed_total_ms': breakdown['elapsed_total_ms'],
                'stale_recheck_elapsed_queue_wait_ms': breakdown['elapsed_queue_wait_ms'],
                'stale_recheck_elapsed_fetch_ms': breakdown['elapsed_fetch_ms'],
                'stale_recheck_elapsed_decision_wait_ms': breakdown['elapsed_decision_wait_ms'],
            }
        status = 'RECHECK_TIMEOUT'
        counters['timeout'] += 1
    else:
        new_surplus = float(item.get('best_net_surplus_bp', -9999) or -9999)
        new_net = float(item.get('net_expected_profit_krw', 0) or 0)
        status = _stale_recheck_pass_status(elapsed_ms) if new_surplus >= request['threshold_bp'] and new_net > 0 else 'RECHECK_FAIL'
        if status == 'RECHECK_FAST_PASS':
            counters['fast_pass'] += 1
        elif status == 'RECHECK_LATE_PASS':
            counters['late_pass'] += 1
        else:
            counters['fail'] += 1
    pending.pop(key, None)
    result = {
        'stale_recheck_status': status,
        'stale_recheck_original_surplus_bp': request['original_surplus_bp'],
        'stale_recheck_new_surplus_bp': float(item.get('best_net_surplus_bp', 0) or 0),
        'stale_recheck_original_net_krw': request['original_net_krw'],
        'stale_recheck_new_net_krw': float(item.get('net_expected_profit_krw', 0) or 0),
        'stale_recheck_elapsed_ms': elapsed_ms,
        'stale_recheck_elapsed_total_ms': breakdown['elapsed_total_ms'],
        'stale_recheck_elapsed_queue_wait_ms': breakdown['elapsed_queue_wait_ms'],
        'stale_recheck_elapsed_fetch_ms': breakdown['elapsed_fetch_ms'],
        'stale_recheck_elapsed_decision_wait_ms': breakdown['elapsed_decision_wait_ms'],
        'stale_recheck_reason': status,
    }
    _record_stale_recheck_event(recent, {
        'pair_id': key[0], 'symbol': key[1], 'status': status,
        'elapsed_ms': elapsed_ms,
        **breakdown,
        'original_surplus_bp': request['original_surplus_bp'],
        'new_surplus_bp': result['stale_recheck_new_surplus_bp'],
    })
    return result


def _expire_stale_rechecks(pending, counters, recent):
    now = time.time()
    expired = [
        key for key, item in pending.items()
        if now - item['requested_at'] > cfg.stale_recheck_result_ttl_sec
    ]
    for key in expired:
        item = pending.pop(key)
        elapsed_ms = round((now - item['requested_at']) * 1000, 2)
        breakdown = _stale_recheck_breakdown(item, now)
        counters['timeout'] += 1
        _record_stale_recheck_event(recent, {
            'pair_id': key[0], 'symbol': key[1], 'status': 'RECHECK_TIMEOUT',
            'elapsed_ms': elapsed_ms,
            **breakdown,
            'original_surplus_bp': item['original_surplus_bp'],
            'new_surplus_bp': None,
        })


def main():
    parser = argparse.ArgumentParser(description="KARB_REALTIME_V1 Engine")
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--duration-sec', type=int, default=0, help='Run for X seconds')
    parser.add_argument('--until-stop', action='store_true',
                        help='Run until STOP_PAPER.bat sets stop_requested')
    parser.add_argument('--mode', type=str, default='', help='Override config mode (paper/tiny_live/live)')
    args = parser.parse_args()

    if args.mode:
        cfg.set_mode(args.mode)

    print(f"[KARB] Mode: {cfg.mode.upper()}")

    # ── 모드 가드 ────────────────────────────────────────────────────────
    try:
        assert_live_credentials_available(cfg.mode)
    except RuntimeError as e:
        print(f"[STARTUP ERROR] {e}")
        sys.exit(1)

    # ── 세션 시작 ────────────────────────────────────────────────────────
    if args.until_stop:
        ctrl = control.start_run()
        run_id = ctrl['run_id']
        started_at = ctrl['started_at']
        print(f"[KARB] Session: {run_id}")
        print(f"[KARB] Stop: run STOP_PAPER.bat or POST /api/stop")
    else:
        run_id = ''
        started_at = time.time()

    base_dir     = os.path.dirname(os.path.abspath(__file__))
    runtime_dir  = os.path.normpath(os.path.join(base_dir, '..', 'runtime'))
    os.makedirs(runtime_dir, exist_ok=True)
    scanner_path = os.path.join(runtime_dir, 'market_scanner.json')

    # ── 컴포넌트 초기화 ──────────────────────────────────────────────────
    upbit_pub    = UpbitPublic()
    binance_pub  = BinancePublic()
    bithumb_pub  = BithumbPublic()
    market_scanner = MarketScanner()
    scanner_snapshot = (
        market_scanner.get_startup_snapshot(
            scanner_path, cfg.symbols, cfg.market_scanner_cache_max_age_sec
        ) if cfg.use_dynamic_symbols
        else market_scanner.fallback('DYNAMIC_SYMBOLS_DISABLED')
    )
    active_symbols = scanner_snapshot.get('active_symbols') or list(cfg.symbols)
    print(f"[KARB] Active symbols: {len(active_symbols)} ({scanner_snapshot.get('source', 'fallback')})")
    fx_oracle    = FxOracle(
        upbit_pub,
        binance_pub,
        cache_enabled=cfg.fx_cache_enabled,
        cache_interval_sec=cfg.fx_cache_interval_sec,
        cache_max_age_sec=cfg.fx_cache_max_age_sec,
    )
    quote_engine = QuoteEngine(upbit_pub, binance_pub, active_symbols)
    rest_fallback_cache = RestFallbackQuoteCache(
        upbit_pub,
        binance_pub,
        enabled=cfg.rest_fallback_cache_enabled,
        refresh_ms=cfg.rest_fallback_cache_refresh_ms,
        stale_ms=cfg.rest_fallback_cache_stale_ms,
        skip_on_backoff=cfg.rest_fallback_cache_skip_on_backoff,
        rest_cache_upbit_refresh_ms=cfg.rest_cache_upbit_refresh_ms,
        rest_cache_binance_refresh_ms=cfg.rest_cache_binance_refresh_ms,
        rest_cache_skip_upbit_when_ws_ok=cfg.rest_cache_skip_upbit_when_ws_ok,
        rest_cache_ws_fresh_threshold_ms=cfg.rest_cache_ws_fresh_threshold_ms,
        recheck_cooldown_sec=cfg.stale_recheck_cooldown_sec,
        recheck_max_queue_size=cfg.stale_recheck_max_queue_size,
    )
    rest_fallback_cache.start(active_symbols)
    bithumb_quote_cache = BithumbQuoteCache(
        bithumb_pub,
        enabled=cfg.bithumb_public_enabled and cfg.bithumb_quote_cache_enabled,
        refresh_ms=cfg.bithumb_quote_cache_refresh_ms,
        stale_ms=cfg.bithumb_quote_cache_stale_ms,
        grace_ms=cfg.bithumb_quote_cache_grace_ms,
        allow_last_good_on_stale=cfg.bithumb_quote_cache_allow_last_good_on_stale,
        max_failures=cfg.bithumb_quote_cache_max_failures,
        recheck_cooldown_sec=cfg.stale_recheck_cooldown_sec,
        recheck_max_queue_size=cfg.stale_recheck_max_queue_size,
    )
    bithumb_quote_cache.start(active_symbols)
    ws_market_data = None
    if cfg.use_websocket_market_data:
        ws_market_data = WebSocketMarketData(
            active_symbols, stale_quote_ms=cfg.stale_quote_ms,
            rest_fallback_enabled=cfg.rest_fallback_enabled,
        )
        ws_market_data.start()
    arb_calc     = ArbCalculator()
    strategy_selector = StrategySelector()
    inv_mgr      = InventoryManager()
    risk_guard   = RiskGuard()
    paper_eng    = PaperEngine(inventory_manager=inv_mgr)
    event_logger = EventLogger()
    perf_tracker = PerformanceTracker()
    collector    = BoundedCollector()

    # ── 경로 ─────────────────────────────────────────────────────────────
    state_path   = os.path.join(runtime_dir, 'state.json')
    quotes_path  = os.path.join(runtime_dir, 'latest_quotes.json')
    telemetry_path = os.path.join(runtime_dir, 'telemetry.json')
    decisions_path = os.path.join(runtime_dir, 'latest_decisions.json')
    opportunities_path = os.path.join(runtime_dir, 'latest_opportunities.json')
    performance_path = os.path.join(runtime_dir, 'performance_summary.json')
    execution_plan_path = os.path.join(runtime_dir, 'last_execution_plan.json')
    runtime_store_status_path = os.path.join(runtime_dir, 'runtime_store_status.json')
    snapshot_paths = {
        'latest_quotes': quotes_path,
        'telemetry': telemetry_path,
        'latest_decisions': decisions_path,
        'performance_summary': performance_path,
        'last_execution_plan': execution_plan_path,
        'market_scanner': scanner_path,
    }
    runtime_store = RuntimeStore(
        enabled=cfg.runtime_store_enabled,
        max_failures=cfg.runtime_snapshot_max_failures,
    )
    runtime_store.hydrate(snapshot_paths)
    runtime_store.set_state('market_scanner', scanner_snapshot)
    if runtime_store.get_state('last_execution_plan') is None:
        runtime_store.set_state('last_execution_plan', {})
    runtime_store.start_background_writer(
        cfg.runtime_snapshot_interval_sec, snapshot_paths, runtime_store_status_path
    )

    # ── 세션 통계 누적기 ──────────────────────────────────────────────────
    start_time         = started_at
    last_state_write   = 0.0
    last_telemetry_write = 0.0
    last_console_print = 0.0
    console_interval   = cfg.get('console_summary_interval_sec', 15)
    krw_usdt           = None
    fx_status          = "INIT"

    total_loops      = 0
    quote_count      = 0
    candidate_count  = 0
    paper_entry_count = 0
    paper_exit_count  = 0
    error_count      = 0
    reason_counts:   dict[str, int] = {}
    surplus_bp_list: deque[float]   = deque(maxlen=1000)
    loop_lat_list:   deque[float]   = deque(maxlen=1000)
    quote_lat_list:  deque[float]   = deque(maxlen=1000)
    quote_age_list:  deque[float]   = deque(maxlen=1000)
    quote_age_tradable_list: deque[float] = deque(maxlen=1000)
    quote_age_cross_border_list: deque[float] = deque(maxlen=1000)
    quote_age_domestic_list: deque[float] = deque(maxlen=1000)
    latest_reason    = ''
    last_quote_at    = 0.0
    latest_decision_at = 0.0
    decisions = deque(maxlen=cfg.decision_log_max_items)
    signal_counts: dict[str, int] = {}
    symbol_surplus_max: dict[str, float] = {}
    quote_source_counts = {'ws': 0, 'rest': 0}
    last_scanner_refresh = time.time()
    quote_history: dict[str, deque] = {}
    dynamic_slippage_list: deque[float] = deque(maxlen=1000)
    paper_latency_list: deque[float] = deque(maxlen=1000)
    liquidity_class_counts: dict[str, int] = {}
    paper_edge_counts = {'PAPER_EDGE_PASS': 0, 'PAPER_EDGE_FAIL': 0}
    last_percentile_calc = 0.0
    cached_p95_loop_latency_ms = 0.0
    cached_p95_quote_latency_ms = 0.0
    cached_p95_quote_age_ms = 0.0
    cached_p95_quote_age_tradable_ms = 0.0
    cached_p95_quote_age_cross_border_ms = 0.0
    cached_p95_quote_age_domestic_ms = 0.0
    live_fresh_candidate_count = 0
    tiny_live_fresh_candidate_count = 0
    live_blocked_quote_age_count = 0
    tiny_live_blocked_quote_age_count = 0
    live_blocked_stale_grace_count = 0
    tiny_live_blocked_stale_grace_count = 0
    symbol_not_in_live_watchlist_count = 0
    stale_grace_opportunity_count = 0
    stale_recheck_pending = {}
    stale_recheck_recent = deque(maxlen=20)
    stale_recheck_request_times = deque()
    stale_recheck_counters = {
        'request': 0, 'fast_pass': 0, 'late_pass': 0, 'fail': 0, 'timeout': 0,
        'skip_cooldown': 0, 'skip_rate_limit': 0,
    }
    skipped_bithumb_symbol_count = 0
    skipped_bithumb_quote_reasons: dict[str, int] = {}
    skipped_bithumb_timestamps: deque[float] = deque()
    bithumb_stale_grace_used_count = 0
    quote_history_cleanup_count = 0
    last_quote_history_cleanup_at = 0.0
    scanner_refresh_thread = None
    scanner_refresh_results = None
    if cfg.use_dynamic_symbols and cfg.market_scanner_background_refresh_on_start:
        scanner_refresh_thread, scanner_refresh_results = _start_background_scanner_refresh(
            market_scanner, cfg.market_scanner_timeout_sec
        )

    while True:
        loop_start = time.time()
        total_loops += 1

        next_scanner_snapshot = None
        if scanner_refresh_results:
            try:
                next_scanner_snapshot = scanner_refresh_results.get_nowait()
            except queue.Empty:
                pass
        if next_scanner_snapshot:
            next_scanner_snapshot = _merge_scanner_snapshot(
                scanner_snapshot, next_scanner_snapshot, active_symbols
            )
            next_symbols = next_scanner_snapshot.get('active_symbols') or list(cfg.symbols)
            if next_scanner_snapshot.get('source') != 'fallback' and next_symbols != active_symbols:
                if ws_market_data:
                    ws_market_data.stop()
                active_symbols = next_symbols
                bithumb_quote_cache.update_symbols(active_symbols)
                rest_fallback_cache.update_symbols(active_symbols)
                removed_history_keys = _cleanup_quote_history(quote_history, active_symbols)
                if removed_history_keys:
                    quote_history_cleanup_count += removed_history_keys
                    last_quote_history_cleanup_at = loop_start
                quote_engine = QuoteEngine(upbit_pub, binance_pub, active_symbols)
                ws_market_data = None
                if cfg.use_websocket_market_data:
                    ws_market_data = WebSocketMarketData(
                        active_symbols, stale_quote_ms=cfg.stale_quote_ms,
                        rest_fallback_enabled=cfg.rest_fallback_enabled,
                    )
                    ws_market_data.start()
                print(f"[KARB] Active symbols refreshed: {len(active_symbols)} ({next_scanner_snapshot.get('source')})")
            scanner_snapshot = next_scanner_snapshot
            runtime_store.set_state('market_scanner', scanner_snapshot)
            market_scanner.save_cached_snapshot(scanner_path, scanner_snapshot)
        if (
            cfg.use_dynamic_symbols
            and loop_start - last_scanner_refresh >= cfg.dynamic_symbol_refresh_sec
            and not (scanner_refresh_thread and scanner_refresh_thread.is_alive())
        ):
            scanner_refresh_thread, scanner_refresh_results = _start_background_scanner_refresh(
                market_scanner, cfg.market_scanner_timeout_sec
            )
            last_scanner_refresh = loop_start

        # ── graceful stop 체크 ────────────────────────────────────────────
        if args.until_stop and control.is_stop_requested():
            print("[KARB] Stop requested detected. Finalizing...")
            break

        # ── FX 환율 ──────────────────────────────────────────────────────
        try:
            with rate_limiter.source('fx'):
                krw_usdt, fx_status = fx_oracle.get_krw_usdt_rate()
        except Exception as e:
            event_logger.log_error('fx_oracle', e)
            fx_status = "ERROR"
            krw_usdt  = None
            error_count += 1

        if (fx_status != "OK" and not (cfg.mode == "paper" and fx_status == "FX_STALE")) or not krw_usdt:
            if args.once:
                print(f"[FX] {fx_status} – 종료")
                break
            sleep_time = cfg.loop_interval_sec - (time.time() - loop_start)
            if sleep_time > 0:
                time.sleep(sleep_time)
            continue

        # ── 호가 수집 ────────────────────────────────────────────────────
        try:
            if ws_market_data:
                rest_fallback_cache.update_ws_fresh_symbols(
                    ws_market_data.fresh_symbols('upbit')
                )
            quotes = (
                ws_market_data.fetch_all(
                    quote_engine if cfg.rest_direct_fallback_enabled else None,
                    rest_fallback_cache=rest_fallback_cache if cfg.rest_fallback_cache_enabled else None,
                )
                if ws_market_data
                else rest_fallback_cache.get_snapshot()
                if cfg.rest_fallback_cache_enabled
                else quote_engine.fetch_all()
                if cfg.rest_direct_fallback_enabled
                else {}
            )
        except Exception as e:
            event_logger.log_error('quote_engine', e)
            quotes = {}
            error_count += 1
        if not cfg.bithumb_public_enabled:
            bithumb_quotes = {}
        elif cfg.bithumb_quote_cache_enabled:
            bithumb_quotes = bithumb_quote_cache.get_snapshot()
        else:
            bithumb_quotes = bithumb_pub.fetch_all_order_books(active_symbols)
        loop_opportunities = []
        skipped_bithumb_symbol_count_last_loop = 0
        skipped_bithumb_quote_reasons_last_loop: dict[str, int] = {}

        best_sym_this_loop    = ''
        best_dir_this_loop    = ''
        best_surplus_this_loop = -9999.0
        best_reason_this_loop  = ''

        for sym, q in quotes.items():
            upbit_q   = q['upbit']
            binance_q = q['binance']
            quote_count += 1
            quote_source = q.get('source', 'rest')
            quote_source_counts[quote_source if quote_source in quote_source_counts else 'rest'] += 1

            # latency 추적
            u_lat = upbit_q.get('latency_ms', 0)
            b_lat = binance_q.get('latency_ms', 0)
            max_q_lat = max(u_lat, b_lat)
            quote_lat_list.append(max_q_lat)
            last_quote_at = max(last_quote_at, upbit_q.get('ts', 0), binance_q.get('ts', 0))
            quote_age_list.append(max(
                0.0,
                time.time() - min(
                    float(upbit_q.get('ts', 0) or 0),
                    float(binance_q.get('ts', 0) or 0),
                ),
            ) * 1000)

            if cfg.bounded_collector_enabled:
                collector.push(sym, {'upbit': upbit_q, 'binance': binance_q, 'symbol': sym})

            # ── 차익 계산 ─────────────────────────────────────────────────
            try:
                calc_res = arb_calc.calculate(sym, upbit_q, binance_q, krw_usdt)
            except Exception as e:
                event_logger.log_error('arb_calc', e)
                error_count += 1
                continue

            calc_res['fx_status']   = fx_status
            calc_res['upbit_ts']    = upbit_q.get('ts')
            calc_res['binance_ts']  = binance_q.get('ts')
            freshness = RiskGuard.quote_freshness_status(calc_res)
            calc_res.update(freshness)
            if freshness['max_leg_quote_age_ms'] is not None:
                quote_age_cross_border_list.append(freshness['max_leg_quote_age_ms'])
                quote_age_tradable_list.append(freshness['max_leg_quote_age_ms'])
            live_fresh_candidate_count += int(freshness['live_freshness_ok'])
            tiny_live_fresh_candidate_count += int(freshness['tiny_live_freshness_ok'])
            live_blocked_quote_age_count += int('LIVE_QUOTE_TOO_OLD' in freshness['live_freshness_blockers'])
            tiny_live_blocked_quote_age_count += int('TINY_LIVE_QUOTE_TOO_OLD' in freshness['tiny_live_freshness_blockers'])
            symbol_not_in_live_watchlist_count += int(not freshness['live_watchlist_ok'])
            history = quote_history.setdefault(sym, deque(maxlen=cfg.quote_history_maxlen))
            history.append(_history_row(upbit=upbit_q, binance=binance_q))
            if cfg.paper_latency_sim_enabled:
                calc_res.update(simulate_paper_fill(calc_res, history, cfg))
            q['calc'] = calc_res
            q['quote_age_sec'] = round(max(
                0, time.time() - min(upbit_q.get('ts', 0), binance_q.get('ts', 0))
            ), 3)

            # surplus 통계 수집
            surplus = calc_res.get('best_net_surplus_bp', -9999)
            surplus_bp_list.append(surplus)
            dynamic_slippage_list.append(float(calc_res.get('dynamic_slippage_bp', 0) or 0))
            paper_latency_list.append(float(calc_res.get('latency_used_ms', 0) or 0))
            liquidity = calc_res.get('liquidity_class', 'NORMAL')
            liquidity_class_counts[liquidity] = liquidity_class_counts.get(liquidity, 0) + 1
            quality = calc_res.get('paper_edge_quality', 'PAPER_EDGE_FAIL')
            paper_edge_counts[quality] = paper_edge_counts.get(quality, 0) + 1

            if surplus > best_surplus_this_loop:
                best_surplus_this_loop = surplus
                best_sym_this_loop     = sym
                best_dir_this_loop     = calc_res.get('best_direction', '')

            # ── RiskGuard ─────────────────────────────────────────────────
            is_safe = risk_guard.check_trade(calc_res)
            reason  = calc_res.get('reason_no_trade', '')
            calc_res['quote_source'] = q.get('source', 'rest')
            recheck_update = _resolve_stale_recheck(
                calc_res, stale_recheck_pending, stale_recheck_counters, stale_recheck_recent
            )
            if recheck_update:
                calc_res.update(recheck_update)
            elif _stale_recheck_candidate(calc_res):
                calc_res.update(_request_stale_recheck(
                    calc_res, stale_recheck_pending, stale_recheck_request_times,
                    stale_recheck_counters, stale_recheck_recent,
                    rest_fallback_cache, bithumb_quote_cache,
                ))
            else:
                calc_res.setdefault('stale_recheck_status', 'NONE')
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            best_reason_this_loop = reason

            if is_safe:
                candidate_count += 1
                signal_counts[sym] = signal_counts.get(sym, 0) + 1

            symbol_surplus_max[sym] = max(symbol_surplus_max.get(sym, -9999.0), surplus)
            decision_time = time.time()
            latest_decision_at = decision_time
            decisions.append(_decision_record(
                calc_res, reason, is_safe, q.get('source', 'rest'),
                float(q.get('quote_age_sec', 0) or 0) * 1000,
            ))
            loop_opportunities.append({
                **calc_res, 'enabled': True, 'paper_only': False,
                'go_no_go': 'GO' if is_safe else 'NO-GO',
                'quote_source': q.get('source', 'rest'),
            })

            event_logger.log_decision(calc_res)

            # ── --once 콘솔 ──────────────────────────────────────────────
            if args.once:
                print(
                    f"  [{sym}] Kimp: {calc_res['kimchi_premium_pct']:+.2f}% | "
                    f"Dir: {calc_res['best_direction']} | "
                    f"Surplus: {surplus:.1f} bp | "
                    f"Net: {calc_res['net_expected_profit_krw']:,.0f} KRW | "
                    f"{'GO' if is_safe else f'NO [{reason}]'}"
                )

            # ── Paper 진입 ────────────────────────────────────────────────
            if is_safe and cfg.mode == 'paper':
                trade = paper_eng.try_entry(calc_res)
                if trade:
                    paper_entry_count += 1
                    if not args.once:
                        print(
                            f"  [{sym}] PAPER ENTRY | Dir: {trade['best_direction']} | "
                            f"Net: {trade['net_expected_profit_krw']:,.0f} KRW"
                        )

            elif is_safe and cfg.mode in ('tiny_live', 'live'):
                print(f"  [{sym}] [{cfg.mode.upper()}] Execution not yet implemented.")

        domestic_quotes = {'UPBIT_BITHUMB': {}}
        if cfg.upbit_bithumb_paper_enabled:
            for sym, q in quotes.items():
                bithumb_q = bithumb_quotes.get(sym, {})
                skip_reason = _bithumb_skip_reason(q.get('upbit', {}), bithumb_q)
                if cfg.skip_missing_bithumb_quotes and skip_reason:
                    skipped_bithumb_symbol_count += 1
                    skipped_bithumb_symbol_count_last_loop += 1
                    skipped_bithumb_timestamps.append(time.time())
                    skipped_bithumb_quote_reasons[skip_reason] = (
                        skipped_bithumb_quote_reasons.get(skip_reason, 0) + 1
                    )
                    skipped_bithumb_quote_reasons_last_loop[skip_reason] = (
                        skipped_bithumb_quote_reasons_last_loop.get(skip_reason, 0) + 1
                    )
                    continue
                if bithumb_q.get('stale_grace'):
                    bithumb_stale_grace_used_count += 1
                domestic_quotes['UPBIT_BITHUMB'][sym] = {
                    'upbit': q.get('upbit', {}),
                    'bithumb': bithumb_q,
                }
                domestic = arb_calc.calculate_domestic_krw(sym, q.get('upbit', {}), bithumb_q)
                domestic['upbit_ts'] = q.get('upbit', {}).get('ts')
                domestic['bithumb_ts'] = bithumb_q.get('ts')
                domestic['stale_grace'] = bool(bithumb_q.get('stale_grace'))
                domestic['stale'] = bool(bithumb_q.get('stale'))
                domestic_freshness = RiskGuard.quote_freshness_status(domestic)
                domestic.update(domestic_freshness)
                if domestic_freshness['max_leg_quote_age_ms'] is not None:
                    quote_age_domestic_list.append(domestic_freshness['max_leg_quote_age_ms'])
                    quote_age_tradable_list.append(domestic_freshness['max_leg_quote_age_ms'])
                live_fresh_candidate_count += int(domestic_freshness['live_freshness_ok'])
                tiny_live_fresh_candidate_count += int(domestic_freshness['tiny_live_freshness_ok'])
                live_blocked_quote_age_count += int('LIVE_QUOTE_TOO_OLD' in domestic_freshness['live_freshness_blockers'])
                tiny_live_blocked_quote_age_count += int('TINY_LIVE_QUOTE_TOO_OLD' in domestic_freshness['tiny_live_freshness_blockers'])
                live_blocked_stale_grace_count += int('LIVE_STALE_GRACE_BLOCKED' in domestic_freshness['live_freshness_blockers'])
                tiny_live_blocked_stale_grace_count += int('LIVE_STALE_GRACE_BLOCKED' in domestic_freshness['tiny_live_freshness_blockers'])
                symbol_not_in_live_watchlist_count += int(not domestic_freshness['live_watchlist_ok'])
                stale_grace_opportunity_count += int(domestic_freshness['uses_stale_grace_quote'])
                if bithumb_q.get('stale_grace'):
                    domestic['warnings'] = [
                        *domestic.get('warnings', []),
                        'BITHUMB_STALE_GRACE',
                    ]
                domestic_history = quote_history.setdefault(
                    f'UPBIT_BITHUMB:{sym}', deque(maxlen=cfg.quote_history_maxlen)
                )
                domestic_history.append(_history_row(upbit=q.get('upbit', {}), bithumb=bithumb_q))
                if cfg.paper_latency_sim_enabled:
                    domestic.update(simulate_paper_fill(domestic, domestic_history, cfg))
                domestic_reason = domestic.get('reason_no_trade', '')
                domestic_safe = domestic_reason == 'OK'
                domestic['quote_source'] = 'rest'
                recheck_update = _resolve_stale_recheck(
                    domestic, stale_recheck_pending, stale_recheck_counters, stale_recheck_recent
                )
                if recheck_update:
                    domestic.update(recheck_update)
                elif _stale_recheck_candidate(domestic):
                    domestic.update(_request_stale_recheck(
                        domestic, stale_recheck_pending, stale_recheck_request_times,
                        stale_recheck_counters, stale_recheck_recent,
                        rest_fallback_cache, bithumb_quote_cache,
                    ))
                else:
                    domestic.setdefault('stale_recheck_status', 'NONE')
                domestic_surplus = domestic.get('best_net_surplus_bp', -9999)
                bithumb_age_ms = max(0, time.time() - float(bithumb_q.get('ts', 0) or 0)) * 1000
                decisions.append(_decision_record(
                    domestic, domestic_reason, domestic_safe, 'rest', bithumb_age_ms
                ))
                latest_decision_at = time.time()
                reason_counts[domestic_reason] = reason_counts.get(domestic_reason, 0) + 1
                quote_count += 1
                quote_lat_list.append(float(bithumb_q.get('latency_ms', 0) or 0))
                last_quote_at = max(last_quote_at, float(bithumb_q.get('ts', 0) or 0))
                quote_age_list.append(max(
                    0.0, time.time() - float(bithumb_q.get('ts', 0) or 0)
                ) * 1000)
                surplus_bp_list.append(domestic_surplus)
                dynamic_slippage_list.append(float(domestic.get('dynamic_slippage_bp', 0) or 0))
                paper_latency_list.append(float(domestic.get('latency_used_ms', 0) or 0))
                liquidity = domestic.get('liquidity_class', 'NORMAL')
                liquidity_class_counts[liquidity] = liquidity_class_counts.get(liquidity, 0) + 1
                quality = domestic.get('paper_edge_quality', 'PAPER_EDGE_FAIL')
                paper_edge_counts[quality] = paper_edge_counts.get(quality, 0) + 1
                symbol_key = f'UPBIT_BITHUMB:{sym}'
                symbol_surplus_max[symbol_key] = max(
                    symbol_surplus_max.get(symbol_key, -9999.0), domestic_surplus
                )
                if domestic_safe:
                    candidate_count += 1
                    signal_counts[symbol_key] = signal_counts.get(symbol_key, 0) + 1
                if domestic_surplus > best_surplus_this_loop:
                    best_surplus_this_loop = domestic_surplus
                    best_sym_this_loop = symbol_key
                    best_dir_this_loop = domestic.get('best_direction', '')
                loop_opportunities.append({
                    **domestic, 'enabled': True, 'paper_only': True,
                    'go_no_go': 'GO' if domestic_safe else 'NO-GO',
                    'quote_source': 'rest',
                })
                if domestic_safe and cfg.mode == 'paper':
                    trade = paper_eng.try_entry(domestic)
                    if trade:
                        paper_entry_count += 1
                        if not args.once:
                            print(
                                f"  [UPBIT_BITHUMB:{sym}] PAPER ENTRY | "
                                f"Dir: {trade['best_direction']} | "
                                f"Net: {trade['net_expected_profit_krw']:,.0f} KRW"
                            )
                if args.once:
                    print(
                        f"  [UPBIT_BITHUMB:{sym}] Dir: {domestic.get('best_direction') or '--'} | "
                        f"Surplus: {domestic_surplus:.1f} bp | "
                        f"Net: {domestic.get('net_expected_profit_krw', 0):,.0f} KRW | "
                        f"{'GO' if domestic_safe else f'NO [{domestic_reason}]'} | PAPER ONLY"
                    )
        strategy_snapshot = strategy_selector.select(loop_opportunities)

        # ── Paper 청산 체크 ───────────────────────────────────────────────
        if cfg.mode == 'paper':
            closed = paper_eng.check_exits(quotes, krw_usdt, domestic_quotes=domestic_quotes)
            for ct in closed:
                paper_exit_count += 1
                perf_tracker.record_exit(ct)
                risk_guard.record_trade_result(ct['realized_pnl_krw'])
                if not args.once:
                    print(
                        f"  [{ct.get('pair_id', 'UPBIT_BINANCE')}:{ct['symbol']}] "
                        f"PAPER EXIT | {ct['exit_reason']} | "
                        f"PnL: {ct['realized_pnl_krw']:+,.0f} KRW | "
                        f"{'WIN' if ct['win'] else 'LOSS'}"
                    )

        perf_tracker.update_open_count(paper_eng.open_count())
        latest_reason = best_reason_this_loop

        # ── 루프 레이턴시 추적 ────────────────────────────────────────────
        loop_ms = (time.time() - loop_start) * 1000
        loop_lat_list.append(loop_ms)

        # ── --until-stop 콘솔 요약 (간격별) ───────────────────────────────
        now = time.time()
        if now - last_percentile_calc >= cfg.telemetry_percentile_interval_sec:
            cached_p95_loop_latency_ms = _percentile(loop_lat_list, 95)
            cached_p95_quote_latency_ms = _percentile(quote_lat_list, 95)
            cached_p95_quote_age_ms = _percentile(quote_age_list, 95)
            cached_p95_quote_age_tradable_ms = _percentile(quote_age_tradable_list, 95)
            cached_p95_quote_age_cross_border_ms = _percentile(quote_age_cross_border_list, 95)
            cached_p95_quote_age_domestic_ms = _percentile(quote_age_domestic_list, 95)
            last_percentile_calc = now
        ws_metrics = ws_market_data.metrics() if ws_market_data else {
            'ws_connected': False,
            'ws_symbols_ok': 0,
            'rest_fallback_count': 0,
            'quote_stale_count': 0,
            'last_msg_at': 0.0,
            'last_msg_age_ms': None,
            'last_error': '',
            'last_error_at': 0.0,
            'reconnect_count': 0,
            'error_count': 0,
            'out_of_order_drop_count': 0,
            'quote_source_summary': {'ws': 0, 'rest': len(quotes), 'stale': 0},
            'symbols': [{
                'symbol': sym,
                'quote_source': q.get('source', 'rest'),
                'quote_age_ms': round(float(q.get('quote_age_sec', 0) or 0) * 1000, 2),
                'upbit_source': q.get('upbit', {}).get('source', 'rest'),
                'binance_source': q.get('binance', {}).get('source', 'rest'),
                'stale': float(q.get('quote_age_sec', 0) or 0) * 1000 > cfg.stale_quote_ms,
            } for sym, q in quotes.items()],
        }
        rate_limit_status = ws_metrics.get('rate_limit_status') or rate_limiter.get_status()
        bithumb_quote_cache_status = bithumb_quote_cache.get_status()
        rest_fallback_cache_status = rest_fallback_cache.get_status()
        fx_cache_status = fx_oracle.get_status()
        quote_history_row_count = sum(len(rows) for rows in quote_history.values())
        memory_telemetry = _memory_telemetry(cfg.memory_telemetry_enabled)
        while skipped_bithumb_timestamps and now - skipped_bithumb_timestamps[0] > 60:
            skipped_bithumb_timestamps.popleft()
        stale_symbol_count = sum(
            1 for item in ws_metrics.get('symbols', []) if item.get('stale')
        )
        symbol_status_count = len(ws_metrics.get('symbols', []))
        top_symbol_by_signal = max(signal_counts, key=signal_counts.get) if signal_counts else ''
        top_symbol_by_surplus = max(symbol_surplus_max, key=symbol_surplus_max.get) if symbol_surplus_max else ''
        _expire_stale_rechecks(stale_recheck_pending, stale_recheck_counters, stale_recheck_recent)
        stale_recheck_elapsed = [
            float(item.get('elapsed_total_ms', item.get('elapsed_ms', 0)) or 0)
            for item in stale_recheck_recent
            if item.get('elapsed_total_ms', item.get('elapsed_ms')) is not None
        ]
        stale_recheck_queue_wait = [
            float(item.get('elapsed_queue_wait_ms', 0) or 0)
            for item in stale_recheck_recent if item.get('elapsed_queue_wait_ms') is not None
        ]
        stale_recheck_fetch = [
            float(item.get('elapsed_fetch_ms', 0) or 0)
            for item in stale_recheck_recent if item.get('elapsed_fetch_ms') is not None
        ]
        stale_recheck_decision_wait = [
            float(item.get('elapsed_decision_wait_ms', 0) or 0)
            for item in stale_recheck_recent if item.get('elapsed_decision_wait_ms') is not None
        ]
        stale_recheck_pass_count = (
            stale_recheck_counters['fast_pass'] + stale_recheck_counters['late_pass']
        )
        stale_recheck_done = (
            stale_recheck_pass_count + stale_recheck_counters['fail']
            + stale_recheck_counters['timeout']
        )
        stale_recheck_pass_ratio = round(
            stale_recheck_pass_count / max(1, stale_recheck_done), 4
        )
        stale_recheck_fast_pass_ratio = round(
            stale_recheck_counters['fast_pass'] / max(1, stale_recheck_done), 4
        )
        stale_recheck_late_pass_ratio = round(
            stale_recheck_counters['late_pass'] / max(1, stale_recheck_done), 4
        )
        stale_recheck_last = next(iter(stale_recheck_recent), {})
        runtime_metrics = {
            'started_at':           started_at,
            'loop_count':           total_loops,
            'quote_count':          quote_count,
            'last_loop_latency_ms': round(loop_ms, 2),
            'p95_loop_latency_ms':  cached_p95_loop_latency_ms,
            'p95_quote_latency_ms': cached_p95_quote_latency_ms,
            'p95_quote_fetch_latency_ms': cached_p95_quote_latency_ms,
            'p95_quote_age_ms':     cached_p95_quote_age_ms,
            'p95_quote_age_all_ms': cached_p95_quote_age_ms,
            'p95_quote_age_tradable_ms': cached_p95_quote_age_tradable_ms,
            'p95_quote_age_cross_border_ms': cached_p95_quote_age_cross_border_ms,
            'p95_quote_age_domestic_ms': cached_p95_quote_age_domestic_ms,
            'best_opportunity_quote_age_ms': next((
                item.get('max_leg_quote_age_ms') for item in loop_opportunities
                if f"{item.get('pair_id', 'UPBIT_BINANCE')}:{item.get('symbol', '')}" == best_sym_this_loop
                or item.get('symbol', '') == best_sym_this_loop
            ), None),
            'best_opportunity_pair_id': (
                'UPBIT_BITHUMB' if best_sym_this_loop.startswith('UPBIT_BITHUMB:') else 'UPBIT_BINANCE'
            ),
            'best_opportunity_symbol': best_sym_this_loop.split(':')[-1],
            'stale_grace_opportunity_count': stale_grace_opportunity_count,
            'live_fresh_candidate_count': live_fresh_candidate_count,
            'tiny_live_fresh_candidate_count': tiny_live_fresh_candidate_count,
            'live_blocked_quote_age_count': live_blocked_quote_age_count,
            'tiny_live_blocked_quote_age_count': tiny_live_blocked_quote_age_count,
            'live_blocked_stale_grace_count': live_blocked_stale_grace_count,
            'tiny_live_blocked_stale_grace_count': tiny_live_blocked_stale_grace_count,
            'symbol_not_in_live_watchlist_count': symbol_not_in_live_watchlist_count,
            'stale_recheck_enabled': cfg.stale_recheck_enabled,
            'stale_recheck_paper_only': cfg.stale_recheck_paper_only,
            'stale_recheck_request_count': stale_recheck_counters['request'],
            'stale_recheck_execute_count': (
                rest_fallback_cache.get_recheck_status().get('recheck_execute_count', 0)
                + bithumb_quote_cache.get_recheck_status().get('recheck_execute_count', 0)
            ),
            'stale_recheck_pass_count': stale_recheck_pass_count,
            'stale_recheck_fast_pass_count': stale_recheck_counters['fast_pass'],
            'stale_recheck_late_pass_count': stale_recheck_counters['late_pass'],
            'stale_recheck_fail_count': stale_recheck_counters['fail'],
            'stale_recheck_timeout_count': stale_recheck_counters['timeout'],
            'stale_recheck_skip_cooldown_count': stale_recheck_counters['skip_cooldown'],
            'stale_recheck_skip_rate_limit_count': stale_recheck_counters['skip_rate_limit'],
            'stale_recheck_queue_size': (
                rest_fallback_cache.get_recheck_status().get('recheck_queue_size', 0)
                + bithumb_quote_cache.get_recheck_status().get('recheck_queue_size', 0)
                + len(stale_recheck_pending)
            ),
            'stale_recheck_last_symbol': stale_recheck_last.get('symbol', ''),
            'stale_recheck_last_status': stale_recheck_last.get('status', 'NONE'),
            'stale_recheck_avg_elapsed_ms': round(
                sum(stale_recheck_elapsed) / len(stale_recheck_elapsed), 2
            ) if stale_recheck_elapsed else 0.0,
            'stale_recheck_avg_total_elapsed_ms': round(
                sum(stale_recheck_elapsed) / len(stale_recheck_elapsed), 2
            ) if stale_recheck_elapsed else 0.0,
            'stale_recheck_avg_queue_wait_ms': round(
                sum(stale_recheck_queue_wait) / len(stale_recheck_queue_wait), 2
            ) if stale_recheck_queue_wait else 0.0,
            'stale_recheck_avg_fetch_ms': round(
                sum(stale_recheck_fetch) / len(stale_recheck_fetch), 2
            ) if stale_recheck_fetch else 0.0,
            'stale_recheck_avg_decision_wait_ms': round(
                sum(stale_recheck_decision_wait) / len(stale_recheck_decision_wait), 2
            ) if stale_recheck_decision_wait else 0.0,
            'stale_recheck_pass_ratio': stale_recheck_pass_ratio,
            'stale_recheck_fast_pass_ratio': stale_recheck_fast_pass_ratio,
            'stale_recheck_late_pass_ratio': stale_recheck_late_pass_ratio,
            'stale_recheck_recent': list(stale_recheck_recent),
            'last_quote_age_sec':   round(max(0.0, now - last_quote_at), 2) if last_quote_at else None,
            'updated_at':           now,
            'last_update_at':       now,
            'quote_source':         (
                'ws' if quotes and all(q.get('source') == 'ws' for q in quotes.values())
                else 'rest'
            ),
            'quote_source_summary': ws_metrics.get('quote_source_summary', {}),
            'ws_ratio':             round(
                quote_source_counts['ws'] / max(1, sum(quote_source_counts.values())) * 100, 2),
            'ws_connected':         ws_metrics.get('ws_connected', False),
            'ws_symbols_ok':        ws_metrics.get('ws_symbols_ok', 0),
            'rest_fallback_count':  ws_metrics.get('rest_fallback_count', 0),
            'rest_fallback_skip_count': ws_metrics.get('rest_fallback_skip_count', 0),
            'rest_fallback_cache_status': rest_fallback_cache_status,
            'rest_fallback_cache_hit_count': ws_metrics.get('rest_fallback_cache_hit_count', 0),
            'rest_fallback_cache_miss_count': ws_metrics.get('rest_fallback_cache_miss_count', 0),
            'rest_fallback_cache_stale_count': ws_metrics.get('rest_fallback_cache_stale_count', 0),
            'rest_direct_call_count': ws_metrics.get('rest_direct_call_count', 0),
            'rest_fallback_older_than_ws_drop_count': ws_metrics.get('rest_fallback_older_than_ws_drop_count', 0),
            'upbit_ws_fresh_skip_count': rest_fallback_cache_status.get('upbit_ws_fresh_skip_count', 0),
            **fx_cache_status,
            'rate_limit_throttle_count': rate_limit_status.get('total_throttle_count', 0),
            'api_429_count':        rate_limit_status.get('total_api_429_count', 0),
            'rate_limit_status':    rate_limit_status,
            **_upbit_rest_metrics(rate_limit_status),
            'quote_stale_count':    ws_metrics.get('quote_stale_count', 0),
            'stale_symbol_count':   stale_symbol_count,
            'stale_symbol_ratio':   round(stale_symbol_count / max(1, symbol_status_count), 4),
            'symbol_quote_status':  ws_metrics.get('symbols', []),
            'last_msg_at':          ws_metrics.get('last_msg_at', 0.0),
            'last_msg_age_ms':      ws_metrics.get('last_msg_age_ms'),
            'upbit_last_msg_age_ms': ws_metrics.get('upbit_last_msg_age_ms'),
            'binance_last_msg_age_ms': ws_metrics.get('binance_last_msg_age_ms'),
            'ws_reconnect_count':   ws_metrics.get('reconnect_count', 0),
            'ws_error_count':       ws_metrics.get('error_count', 0),
            'ws_last_error':        ws_metrics.get('last_error', ''),
            'ws_last_error_at':     ws_metrics.get('last_error_at', 0.0),
            'out_of_order_drop_count': ws_metrics.get('out_of_order_drop_count', 0),
            'runtime_store_warning': 'RUNTIME_STORE_DISABLED_WARNING' if not cfg.runtime_store_enabled else '',
            'decision_count':       sum(reason_counts.values()),
            'total_decision_count': sum(reason_counts.values()),
            'candidate_count':      candidate_count,
            'ok_signal_count':      reason_counts.get('OK', 0),
            'no_go_reason_counts':  {k: v for k, v in reason_counts.items() if k != 'OK'},
            'max_best_net_surplus_bp': round(max(surplus_bp_list), 4) if surplus_bp_list else 0.0,
            'avg_best_net_surplus_bp': round(sum(surplus_bp_list) / len(surplus_bp_list), 4) if surplus_bp_list else 0.0,
            'top_symbol_by_signal': top_symbol_by_signal,
            'top_symbol_by_surplus': top_symbol_by_surplus,
            'last_decision_at':     latest_decision_at,
            'avg_dynamic_slippage_bp': round(sum(dynamic_slippage_list) / len(dynamic_slippage_list), 4) if dynamic_slippage_list else 0.0,
            'max_dynamic_slippage_bp': round(max(dynamic_slippage_list), 4) if dynamic_slippage_list else 0.0,
            'low_depth_count':      liquidity_class_counts.get('LOW_DEPTH', 0),
            'liquidity_class_counts': liquidity_class_counts,
            'paper_edge_pass_count': paper_edge_counts.get('PAPER_EDGE_PASS', 0),
            'paper_edge_fail_count': paper_edge_counts.get('PAPER_EDGE_FAIL', 0),
            'avg_latency_used_ms':  round(sum(paper_latency_list) / len(paper_latency_list), 2) if paper_latency_list else 0.0,
            'bithumb_quote_cache_status': bithumb_quote_cache_status,
            'bithumb_stale_grace_count': bithumb_quote_cache_status.get('stale_grace_count', 0),
            'bithumb_stale_hard_count': bithumb_quote_cache_status.get('stale_hard_count', 0),
            'bithumb_last_good_age_ms': bithumb_quote_cache_status.get('last_good_age_ms'),
            'skipped_bithumb_symbol_count': skipped_bithumb_symbol_count,
            'skipped_bithumb_quote_reasons': skipped_bithumb_quote_reasons,
            'skipped_bithumb_symbol_count_total': skipped_bithumb_symbol_count,
            'skipped_bithumb_symbol_count_window': len(skipped_bithumb_timestamps),
            'skipped_bithumb_symbol_count_last_loop': skipped_bithumb_symbol_count_last_loop,
            'skipped_bithumb_quote_reasons_total': skipped_bithumb_quote_reasons,
            'skipped_bithumb_quote_reasons_last_loop': skipped_bithumb_quote_reasons_last_loop,
            'bithumb_stale_grace_used_count': bithumb_stale_grace_used_count,
            'quote_history_key_count': len(quote_history),
            'quote_history_row_count': quote_history_row_count,
            'quote_history_max_rows_per_key': cfg.quote_history_maxlen,
            'quote_history_estimated_items': quote_history_row_count,
            'quote_history_lightweight_enabled': cfg.quote_history_lightweight_enabled,
            **memory_telemetry,
            'paper_fill_latency_model': 'per_leg',
            'quote_history_cleanup_count': quote_history_cleanup_count,
            'last_quote_history_cleanup_at': last_quote_history_cleanup_at,
            'scanner_source': scanner_snapshot.get('source', 'fallback'),
            'scanner_cache_used': scanner_snapshot.get('scanner_cache_used', False),
            'scanner_cache_age_sec': scanner_snapshot.get('scanner_cache_age_sec'),
            'scanner_last_refresh_status': scanner_snapshot.get('scanner_last_refresh_status', 'INIT'),
            'scanner_last_error': scanner_snapshot.get('scanner_last_error', ''),
            'scanner_refresh_count': scanner_snapshot.get('scanner_refresh_count', 0),
            'scanner_fail_count': scanner_snapshot.get('scanner_fail_count', 0),
            'scanner_startup_mode': scanner_snapshot.get('scanner_startup_mode', cfg.market_scanner_startup_mode),
        }
        perf_tracker.update_runtime_metrics(runtime_metrics)
        runtime_store.set_state('latest_quotes', quotes)
        runtime_store.set_state('telemetry', runtime_metrics)
        runtime_store.set_state('latest_decisions', {'updated_at': now, 'decisions': list(decisions)})
        runtime_store.set_state('market_scanner', scanner_snapshot)
        if args.until_stop and (now - last_console_print >= console_interval):
            elapsed_sec = now - start_time
            perf_s = perf_tracker.summary()
            print(
                f"[{elapsed_sec/60:.0f}m] "
                f"sym={best_sym_this_loop} dir={best_dir_this_loop} "
                f"surplus={best_surplus_this_loop:.1f}bp | "
                f"trades={perf_s.get('closed_trade_count', 0)} "
                f"win={perf_s.get('win_rate', 0):.0f}% "
                f"pnl={perf_s.get('net_pnl_krw', 0):+,.0f}₩ | "
                f"reason={latest_reason}"
            )
            last_console_print = now

        # ── runtime 파일 overwrite ────────────────────────────────────────
        if now - last_telemetry_write >= cfg.telemetry_write_interval_sec:
            if not cfg.runtime_store_enabled:
                _write_json(telemetry_path, runtime_metrics)
                _write_json(decisions_path, {'updated_at': now, 'decisions': list(decisions)})
                _write_json(scanner_path, scanner_snapshot)
            _write_json(opportunities_path, {'updated_at': now, **strategy_snapshot})
            last_telemetry_write = now

        if now - last_state_write >= cfg.state_write_interval_sec:
            perf_summary = perf_tracker.summary()
            runtime_store.set_state('performance_summary', perf_summary)
            if not cfg.runtime_store_enabled:
                _write_json(quotes_path, quotes)
            _write_json(state_path, {
                'mode':           cfg.mode,
                'run_id':         run_id,
                **runtime_metrics,
                'krw_usdt':       krw_usdt,
                'fx_status':      fx_status,
                'symbols':        list(quotes.keys()),
                'open_trades':    paper_eng.open_count(),
                'closed_trades':  paper_eng.closed_count(),
                'net_pnl_krw':    perf_summary.get('net_pnl_krw', 0),
                'win_rate':       perf_summary.get('win_rate', 0),
                'today_pnl_krw':  perf_summary.get('today_pnl_krw', 0),
                'pair_summary':   perf_summary.get('pair_summary', {}),
                'best_pair_by_pnl': perf_summary.get('best_pair_by_pnl', ''),
                'most_active_pair': perf_summary.get('most_active_pair', ''),
                'runtime_sec':    round(now - start_time, 1),
                'latest_reason':  latest_reason,
                'strategy':       strategy_snapshot,
            })
            last_state_write = now

        # ── 종료 조건 ────────────────────────────────────────────────────
        if args.once:
            perf_summary = perf_tracker.summary()
            print(
                f"\n[KARB] --once 완료 | "
                f"Closed: {paper_eng.closed_count()} | "
                f"Net PnL: {perf_summary.get('net_pnl_krw', 0):,.0f} KRW"
            )
            break

        elapsed = time.time() - start_time
        if args.duration_sec > 0 and elapsed >= args.duration_sec:
            break

        sleep_time = cfg.loop_interval_sec - (time.time() - loop_start)
        if sleep_time > 0:
            time.sleep(sleep_time)

    # ══════════════════════════════════════════════════════════════════════
    # 종료 후: 세션 분석 리포트 자동 생성
    # ══════════════════════════════════════════════════════════════════════
    ended_at = time.time()
    if ws_market_data:
        ws_market_data.stop()
    rest_fallback_cache.stop()
    bithumb_quote_cache.stop()
    final_runtime_metrics = runtime_store.get_state('telemetry', {})
    if isinstance(final_runtime_metrics, dict):
        final_runtime_metrics['bithumb_quote_cache_status'] = bithumb_quote_cache.get_status()
        final_runtime_metrics['rest_fallback_cache_status'] = rest_fallback_cache.get_status()
        runtime_store.set_state('telemetry', final_runtime_metrics)
        if not cfg.runtime_store_enabled:
            _write_json(telemetry_path, final_runtime_metrics)
    runtime_store.stop_background_writer(snapshot_paths, runtime_store_status_path)

    if args.until_stop:
        control.finish_run(ended_at)

    import session_analyzer
    report = session_analyzer.analyze_session(run_id or f'oneshot_{int(start_time)}')

    print(f"\n{'='*60}")
    print(f"[KARB] Session Report → Judgement: {report['judgement']}")
    print(f"  Net PnL:     {report['net_pnl_krw']:+,.0f} KRW")
    print(f"  Win Rate:    {report['win_rate']:.1f}%")
    print(f"  Trades:      {report['closed_trade_count']}")
    print(f"  Max DD:      {report['max_drawdown_krw']:,.0f} KRW")
    print(f"  P95 Latency: {report['p95_quote_latency_ms']:.0f} ms")
    print(f"  Quality:     {report['trading_quality']}")
    if report.get('run_id'):
        print(f"  Report:      reports/sessions/{report['run_id']}_summary.txt")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
