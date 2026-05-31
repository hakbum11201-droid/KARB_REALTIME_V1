import argparse
import json
import os
import sys
import time
from collections import deque

from config import cfg
from secrets_manager import assert_live_credentials_available
from upbit_public import UpbitPublic
from binance_public import BinancePublic
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

    # ── 컴포넌트 초기화 ──────────────────────────────────────────────────
    upbit_pub    = UpbitPublic()
    binance_pub  = BinancePublic()
    fx_oracle    = FxOracle(upbit_pub, binance_pub)
    quote_engine = QuoteEngine(upbit_pub, binance_pub, cfg.symbols)
    ws_market_data = None
    if cfg.use_websocket_market_data:
        ws_market_data = WebSocketMarketData(
            cfg.symbols, stale_quote_ms=cfg.stale_quote_ms,
            rest_fallback_enabled=cfg.rest_fallback_enabled,
        )
        ws_market_data.start()
    arb_calc     = ArbCalculator()
    inv_mgr      = InventoryManager()
    risk_guard   = RiskGuard()
    paper_eng    = PaperEngine(inventory_manager=inv_mgr)
    event_logger = EventLogger()
    perf_tracker = PerformanceTracker()
    collector    = BoundedCollector()

    # ── 경로 ─────────────────────────────────────────────────────────────
    base_dir     = os.path.dirname(os.path.abspath(__file__))
    runtime_dir  = os.path.normpath(os.path.join(base_dir, '..', 'runtime'))
    os.makedirs(runtime_dir, exist_ok=True)
    state_path   = os.path.join(runtime_dir, 'state.json')
    quotes_path  = os.path.join(runtime_dir, 'latest_quotes.json')

    # ── 세션 통계 누적기 ──────────────────────────────────────────────────
    start_time         = started_at
    last_state_write   = 0.0
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
    surplus_bp_list: list[float]    = []
    loop_lat_list:   deque[float]   = deque(maxlen=1000)
    quote_lat_list:  deque[float]   = deque(maxlen=1000)
    latest_reason    = ''
    last_quote_at    = 0.0

    # bounded: 최근 1000건만 보존 (무한 리스트 방지)
    MAX_SURPLUS_SAMPLES = 1000

    while True:
        loop_start = time.time()
        total_loops += 1

        # ── graceful stop 체크 ────────────────────────────────────────────
        if args.until_stop and control.is_stop_requested():
            print("[KARB] Stop requested detected. Finalizing...")
            break

        # ── FX 환율 ──────────────────────────────────────────────────────
        try:
            krw_usdt, fx_status = fx_oracle.get_krw_usdt_rate()
        except Exception as e:
            event_logger.log_error('fx_oracle', e)
            fx_status = "ERROR"
            krw_usdt  = None
            error_count += 1

        if fx_status != "OK" or not krw_usdt:
            if args.once:
                print(f"[FX] {fx_status} – 종료")
                break
            sleep_time = cfg.loop_interval_sec - (time.time() - loop_start)
            if sleep_time > 0:
                time.sleep(sleep_time)
            continue

        # ── 호가 수집 ────────────────────────────────────────────────────
        try:
            quotes = (
                ws_market_data.fetch_all(quote_engine)
                if ws_market_data else quote_engine.fetch_all()
            )
        except Exception as e:
            event_logger.log_error('quote_engine', e)
            quotes = {}
            error_count += 1

        best_sym_this_loop    = ''
        best_dir_this_loop    = ''
        best_surplus_this_loop = -9999.0
        best_reason_this_loop  = ''

        for sym, q in quotes.items():
            upbit_q   = q['upbit']
            binance_q = q['binance']
            quote_count += 1

            # latency 추적
            u_lat = upbit_q.get('latency_ms', 0)
            b_lat = binance_q.get('latency_ms', 0)
            max_q_lat = max(u_lat, b_lat)
            quote_lat_list.append(max_q_lat)
            last_quote_at = max(last_quote_at, upbit_q.get('ts', 0), binance_q.get('ts', 0))

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
            q['calc'] = calc_res
            q['quote_age_sec'] = round(max(
                0, time.time() - min(upbit_q.get('ts', 0), binance_q.get('ts', 0))
            ), 3)

            # surplus 통계 수집
            surplus = calc_res.get('best_net_surplus_bp', -9999)
            if len(surplus_bp_list) < MAX_SURPLUS_SAMPLES:
                surplus_bp_list.append(surplus)

            if surplus > best_surplus_this_loop:
                best_surplus_this_loop = surplus
                best_sym_this_loop     = sym
                best_dir_this_loop     = calc_res.get('best_direction', '')

            # ── RiskGuard ─────────────────────────────────────────────────
            is_safe = risk_guard.check_trade(calc_res)
            reason  = calc_res.get('reason_no_trade', '')
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            best_reason_this_loop = reason

            if is_safe:
                candidate_count += 1

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

        # ── Paper 청산 체크 ───────────────────────────────────────────────
        if cfg.mode == 'paper':
            closed = paper_eng.check_exits(quotes, krw_usdt)
            for ct in closed:
                paper_exit_count += 1
                perf_tracker.record_exit(ct)
                risk_guard.record_trade_result(ct['realized_pnl_krw'])
                if not args.once:
                    print(
                        f"  [{ct['symbol']}] PAPER EXIT | {ct['exit_reason']} | "
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
        runtime_metrics = {
            'started_at':           started_at,
            'loop_count':           total_loops,
            'quote_count':          quote_count,
            'last_loop_latency_ms': round(loop_ms, 2),
            'p95_loop_latency_ms':  _percentile(loop_lat_list, 95),
            'p95_quote_latency_ms': _percentile(quote_lat_list, 95),
            'last_quote_age_sec':   round(max(0.0, now - last_quote_at), 2) if last_quote_at else None,
            'updated_at':           now,
            'quote_source':         (
                'ws' if quotes and all(q.get('source') == 'ws' for q in quotes.values())
                else 'rest'
            ),
        }
        perf_tracker.update_runtime_metrics(runtime_metrics)
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
        if now - last_state_write >= cfg.state_write_interval_sec:
            perf_summary = perf_tracker.summary()
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
                'runtime_sec':    round(now - start_time, 1),
                'latest_reason':  latest_reason,
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
