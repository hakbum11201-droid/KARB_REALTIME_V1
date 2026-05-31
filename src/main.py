import argparse
import time
import json
import os
import sys

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


def main():
    parser = argparse.ArgumentParser(description="KARB_REALTIME_V1 Engine")
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--duration-sec', type=int, default=0, help='Run for X seconds')
    args = parser.parse_args()

    print(f"[KARB] Starting in mode: {cfg.mode.upper()}")

    # ----------------------------------------------------------------
    # Startup guard: tiny_live / live 모드에서 자격증명 없으면 즉시 중단
    # paper 모드에서는 키 없어도 통과
    # ----------------------------------------------------------------
    try:
        assert_live_credentials_available(cfg.mode)
    except RuntimeError as e:
        print(f"[STARTUP ERROR] {e}")
        sys.exit(1)

    # ----------------------------------------------------------------
    # 컴포넌트 초기화
    # ----------------------------------------------------------------
    upbit_pub    = UpbitPublic()
    binance_pub  = BinancePublic()
    fx_oracle    = FxOracle(upbit_pub, binance_pub)
    quote_engine = QuoteEngine(upbit_pub, binance_pub, cfg.symbols)
    arb_calc     = ArbCalculator()
    inv_mgr      = InventoryManager()
    risk_guard   = RiskGuard()
    paper_eng    = PaperEngine()
    event_logger = EventLogger()
    perf_tracker = PerformanceTracker()
    collector    = BoundedCollector()

    # runtime 디렉터리 보장
    base_dir     = os.path.dirname(os.path.abspath(__file__))
    runtime_dir  = os.path.normpath(os.path.join(base_dir, '..', 'runtime'))
    os.makedirs(runtime_dir, exist_ok=True)
    state_path   = os.path.join(runtime_dir, 'state.json')
    quotes_path  = os.path.join(runtime_dir, 'latest_quotes.json')

    start_time = time.time()

    while True:
        loop_start = time.time()

        # ---- FX 환율 조회 ----
        krw_usdt, fx_status = fx_oracle.get_krw_usdt_rate()
        if fx_status != "OK" or not krw_usdt:
            print(f"[FX] Error: {fx_status}. Skipping loop.")
        else:
            quotes = quote_engine.fetch_all()

            for sym, q in quotes.items():
                upbit_q   = q['upbit']
                binance_q = q['binance']

                # BoundedCollector에 tick 적재 (메모리 바운드)
                collector.push(sym, {'upbit': upbit_q, 'binance': binance_q, 'symbol': sym})

                # ---- 차익 계산 ----
                calc_res = arb_calc.calculate(sym, upbit_q, binance_q, krw_usdt)
                q['calc'] = calc_res

                # ---- 리스크 가드 ----
                is_safe = risk_guard.check_trade(calc_res)

                # ---- 이벤트 기록 (바운드 로거) ----
                event_logger.log_decision(calc_res)

                # ---- --once 콘솔 요약 ----
                if args.once:
                    kimp  = calc_res['kimchi_premium_pct']
                    surp  = calc_res['best_net_surplus_bp']
                    net   = calc_res['net_expected_profit_krw']
                    gross = calc_res['gross_gap_krw']
                    dirn  = calc_res['best_direction']
                    reason = calc_res['reason_no_trade']
                    go_str = "GO" if is_safe else f"NO-GO [{reason}]"
                    print(
                        f"  [{sym}] Kimp: {kimp:+.2f}% | Dir: {dirn} | "
                        f"Net Surplus: {surp:.1f} bp | "
                        f"Gross: {gross:,.0f} KRW | Net: {net:,.0f} KRW | {go_str}"
                    )

                # ----------------------------------------------------------------
                # 진입 분기
                # paper 모드: execution_engine 경로 없음
                # tiny_live / live: 미구현 (execution_engine에 추후 추가)
                # ----------------------------------------------------------------
                if is_safe:
                    if cfg.mode == 'paper':
                        trade = paper_eng.execute(calc_res)
                        perf_tracker.record(trade)
                        if not args.once:
                            print(
                                f"[{sym}] PAPER | Dir: {trade['best_direction']} | "
                                f"Net: {trade['net_expected_profit_krw']:,.0f} KRW"
                            )
                    elif cfg.mode in ('tiny_live', 'live'):
                        # 실제 주문 로직 미구현 – execution_engine 통합 후 활성화
                        print(f"[{sym}] [{cfg.mode.upper()}] Execution not yet implemented.")
                    else:
                        print(f"[WARN] Unknown mode: {cfg.mode}. No action taken.")

            # ---- runtime 파일 업데이트 ----
            perf_summary = perf_tracker.summary()
            try:
                with open(quotes_path, 'w', encoding='utf-8') as f:
                    json.dump(quotes, f, ensure_ascii=False)
            except Exception:
                pass

            state = {
                'mode': cfg.mode,
                'krw_usdt': krw_usdt,
                'paper_trade_count': perf_summary['trade_count'],
                'latest_paper_pnl': perf_summary.get('recent10_avg_net_krw', 0),
                'total_net_profit_krw': perf_summary['total_net_profit_krw'],
                'win_rate_pct': perf_summary['win_rate_pct'],
                'collector_stats': collector.stats(),
                'latest_update': time.time(),
            }
            try:
                with open(state_path, 'w', encoding='utf-8') as f:
                    json.dump(state, f, ensure_ascii=False)
            except Exception:
                pass

        # ---- 종료 조건 ----
        if args.once:
            perf_summary = perf_tracker.summary()
            print(f"\n[KARB] --once 완료: {perf_summary['trade_count']} paper trades | "
                  f"Total Net: {perf_summary['total_net_profit_krw']:,.0f} KRW")
            break

        elapsed = time.time() - start_time
        if args.duration_sec > 0 and elapsed >= args.duration_sec:
            break

        sleep_time = cfg.loop_interval_sec - (time.time() - loop_start)
        if sleep_time > 0:
            time.sleep(sleep_time)


if __name__ == '__main__':
    main()
