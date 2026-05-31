import argparse
import json
import os
import sys
import time

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


def _write_json(path: str, data: dict) -> None:
    """overwrite 전용. append 금지."""
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="KARB_REALTIME_V1 Engine")
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--duration-sec', type=int, default=0, help='Run for X seconds')
    args = parser.parse_args()

    print(f"[KARB] Mode: {cfg.mode.upper()}")

    # ── 모드 가드 ─────────────────────────────────────────────────────────
    try:
        assert_live_credentials_available(cfg.mode)
    except RuntimeError as e:
        print(f"[STARTUP ERROR] {e}")
        sys.exit(1)

    # ── 컴포넌트 초기화 ───────────────────────────────────────────────────
    upbit_pub    = UpbitPublic()
    binance_pub  = BinancePublic()
    fx_oracle    = FxOracle(upbit_pub, binance_pub)
    quote_engine = QuoteEngine(upbit_pub, binance_pub, cfg.symbols)
    arb_calc     = ArbCalculator()
    inv_mgr      = InventoryManager()
    risk_guard   = RiskGuard()
    paper_eng    = PaperEngine(inventory_manager=inv_mgr)
    event_logger = EventLogger()
    perf_tracker = PerformanceTracker()
    collector    = BoundedCollector()

    # ── runtime 디렉터리 ──────────────────────────────────────────────────
    base_dir     = os.path.dirname(os.path.abspath(__file__))
    runtime_dir  = os.path.normpath(os.path.join(base_dir, '..', 'runtime'))
    os.makedirs(runtime_dir, exist_ok=True)
    state_path   = os.path.join(runtime_dir, 'latest_state.json')
    quotes_path  = os.path.join(runtime_dir, 'latest_quotes.json')

    start_time         = time.time()
    last_state_write   = 0.0
    krw_usdt           = None
    fx_status          = "INIT"

    while True:
        loop_start = time.time()

        # ── FX 환율 ───────────────────────────────────────────────────────
        try:
            krw_usdt, fx_status = fx_oracle.get_krw_usdt_rate()
        except Exception as e:
            event_logger.log_error('fx_oracle', e)
            fx_status = "ERROR"
            krw_usdt  = None

        if fx_status != "OK" or not krw_usdt:
            if args.once:
                print(f"[FX] {fx_status} – 종료")
                break
            sleep_time = cfg.loop_interval_sec - (time.time() - loop_start)
            if sleep_time > 0:
                time.sleep(sleep_time)
            continue

        # ── 호가 수집 ─────────────────────────────────────────────────────
        try:
            quotes = quote_engine.fetch_all()
        except Exception as e:
            event_logger.log_error('quote_engine', e)
            quotes = {}

        for sym, q in quotes.items():
            upbit_q   = q['upbit']
            binance_q = q['binance']

            # BoundedCollector tick 적재
            if cfg.bounded_collector_enabled:
                collector.push(sym, {'upbit': upbit_q, 'binance': binance_q, 'symbol': sym})

            # ── 차익 계산 ──────────────────────────────────────────────────
            try:
                calc_res = arb_calc.calculate(sym, upbit_q, binance_q, krw_usdt)
            except Exception as e:
                event_logger.log_error('arb_calc', e)
                continue

            # FX 상태를 calc_res에 주입 (RiskGuard FX 검사용)
            calc_res['fx_status'] = fx_status
            # 호가 타임스탬프 주입 (STALE_QUOTE 검사용)
            calc_res['upbit_ts']   = upbit_q.get('ts')
            calc_res['binance_ts'] = binance_q.get('ts')

            q['calc'] = calc_res

            # ── RiskGuard ─────────────────────────────────────────────────
            is_safe = risk_guard.check_trade(calc_res)

            # ── 조건부 이벤트 로그 ────────────────────────────────────────
            event_logger.log_decision(calc_res)

            # ── --once 콘솔 요약 ─────────────────────────────────────────
            if args.once:
                reason = calc_res.get('reason_no_trade', '')
                print(
                    f"  [{sym}] Kimp: {calc_res['kimchi_premium_pct']:+.2f}% | "
                    f"Dir: {calc_res['best_direction']} | "
                    f"Net Surplus: {calc_res['best_net_surplus_bp']:.1f} bp | "
                    f"Gross: {calc_res['gross_gap_krw']:,.0f} KRW | "
                    f"Net: {calc_res['net_expected_profit_krw']:,.0f} KRW | "
                    f"{'GO' if is_safe else f'NO-GO [{reason}]'}"
                )

            # ── Paper 진입 ────────────────────────────────────────────────
            if is_safe and cfg.mode == 'paper':
                trade = paper_eng.try_entry(calc_res)
                if trade and not args.once:
                    print(
                        f"[{sym}] PAPER ENTRY | Dir: {trade['best_direction']} | "
                        f"Net: {trade['net_expected_profit_krw']:,.0f} KRW"
                    )

            elif is_safe and cfg.mode in ('tiny_live', 'live'):
                print(f"[{sym}] [{cfg.mode.upper()}] Execution not yet implemented.")

        # ── Paper 청산 체크 (매 루프) ─────────────────────────────────────
        if cfg.mode == 'paper':
            closed = paper_eng.check_exits(quotes, krw_usdt)
            for ct in closed:
                perf_tracker.record_exit(ct)
                risk_guard.record_trade_result(ct['realized_pnl_krw'])
                if not args.once:
                    print(
                        f"[{ct['symbol']}] PAPER EXIT | {ct['exit_reason']} | "
                        f"PnL: {ct['realized_pnl_krw']:+,.0f} KRW | "
                        f"{'WIN' if ct['win'] else 'LOSS'}"
                    )

        # ── PerformanceTracker 갱신 ───────────────────────────────────────
        perf_tracker.update_open_count(paper_eng.open_count())

        # ── runtime 파일 overwrite (state_write_interval_sec 주기) ────────
        now = time.time()
        if now - last_state_write >= cfg.state_write_interval_sec:
            perf_summary = perf_tracker.summary()   # performance_summary.json도 내부에서 write
            _write_json(quotes_path, quotes)
            _write_json(state_path, {
                'mode':           cfg.mode,
                'krw_usdt':       krw_usdt,
                'fx_status':      fx_status,
                'symbols':        list(quotes.keys()),
                'open_trades':    paper_eng.open_count(),
                'closed_trades':  paper_eng.closed_count(),
                'net_pnl_krw':    perf_summary.get('net_pnl_krw', 0),
                'win_rate':       perf_summary.get('win_rate', 0),
                'today_pnl_krw':  perf_summary.get('today_pnl_krw', 0),
                'updated_at':     now,
            })
            last_state_write = now

        # ── 종료 조건 ────────────────────────────────────────────────────
        if args.once:
            perf_summary = perf_tracker.summary()
            print(
                f"\n[KARB] --once 완료 | Open: {paper_eng.open_count()} | "
                f"Closed: {paper_eng.closed_count()} | "
                f"Net PnL: {perf_summary.get('net_pnl_krw', 0):,.0f} KRW | "
                f"Win Rate: {perf_summary.get('win_rate', 0):.1f}%"
            )
            break

        elapsed = time.time() - start_time
        if args.duration_sec > 0 and elapsed >= args.duration_sec:
            break

        sleep_time = cfg.loop_interval_sec - (time.time() - loop_start)
        if sleep_time > 0:
            time.sleep(sleep_time)


if __name__ == '__main__':
    main()
