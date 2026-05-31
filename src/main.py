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


def main():
    parser = argparse.ArgumentParser(description="KARB_REALTIME_V1 Engine")
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--duration-sec', type=int, default=0, help='Run for X seconds')
    args = parser.parse_args()

    print(f"Starting KARB_REALTIME_V1 in mode: {cfg.mode}")

    # ----------------------------------------------------------------
    # Startup: tiny_live / live 모드에서 자격증명 미설정 시 즉시 중단
    # paper 모드에서는 키 없어도 통과
    # ----------------------------------------------------------------
    try:
        assert_live_credentials_available(cfg.mode)
    except RuntimeError as e:
        print(f"[STARTUP ERROR] {e}")
        sys.exit(1)

    upbit_pub = UpbitPublic()
    binance_pub = BinancePublic()

    fx_oracle = FxOracle(upbit_pub, binance_pub)
    quote_engine = QuoteEngine(upbit_pub, binance_pub, cfg.symbols)
    arb_calc = ArbCalculator()
    inv_mgr = InventoryManager()
    risk_guard = RiskGuard()
    paper_eng = PaperEngine()
    event_logger = EventLogger()

    runtime_dir = os.path.join(os.path.dirname(__file__), '..', 'runtime')
    state_path = os.path.join(runtime_dir, 'state.json')
    quotes_path = os.path.join(runtime_dir, 'latest_quotes.json')

    start_time = time.time()

    while True:
        loop_start = time.time()

        krw_usdt, fx_status = fx_oracle.get_krw_usdt_rate()

        if fx_status != "OK" or not krw_usdt:
            print(f"FX Error: {fx_status}. Skipping loop.")
        else:
            quotes = quote_engine.fetch_all()

            for sym, q in quotes.items():
                calc_res = arb_calc.calculate(sym, q['upbit'], q['binance'], krw_usdt)
                q['calc'] = calc_res

                is_safe = risk_guard.check_trade(calc_res)
                event_logger.log_decision(calc_res)

                # --once 콘솔 요약 출력
                if args.once:
                    print(
                        f"[{sym}] "
                        f"Kimp: {calc_res['kimchi_premium_pct']:.2f}% | "
                        f"Dir: {calc_res['best_direction']} | "
                        f"Net Surplus: {calc_res['best_net_surplus_bp']:.1f} bp | "
                        f"Net Profit: {calc_res['net_expected_profit_krw']:.0f} KRW | "
                        f"Go: {is_safe}"
                        + (f" [{calc_res['reason_no_trade']}]" if calc_res['reason_no_trade'] else "")
                    )

                # ----------------------------------------------------------------
                # 진입 분기: paper vs tiny_live/live
                # paper 모드에서는 execution_engine 호출 경로 자체가 없다.
                # ----------------------------------------------------------------
                if is_safe:
                    if cfg.mode == 'paper':
                        trade = paper_eng.execute(calc_res)
                        print(
                            f"[{sym}] PAPER TRADE | Dir: {trade['best_direction']} | "
                            f"Net Profit: {trade['net_expected_profit_krw']:.0f} KRW"
                        )
                    elif cfg.mode in ('tiny_live', 'live'):
                        # 실제 주문 로직은 미구현 상태로 유지 (추후 execution_engine에 추가)
                        print(f"[{sym}] [{cfg.mode.upper()}] Execution not yet implemented.")
                    else:
                        print(f"[WARN] Unknown mode: {cfg.mode}. No action taken.")

            with open(quotes_path, 'w', encoding='utf-8') as f:
                json.dump(quotes, f)

            state = {
                'mode': cfg.mode,
                'krw_usdt': krw_usdt,
                'paper_trade_count': len(paper_eng.trades),
                'latest_paper_pnl': paper_eng.trades[-1]['net_expected_profit_krw'] if paper_eng.trades else 0,
                'latest_update': time.time()
            }
            with open(state_path, 'w', encoding='utf-8') as f:
                json.dump(state, f)

        if args.once:
            break

        elapsed = time.time() - start_time
        if args.duration_sec > 0 and elapsed >= args.duration_sec:
            break

        sleep_time = cfg.loop_interval_sec - (time.time() - loop_start)
        if sleep_time > 0:
            time.sleep(sleep_time)


if __name__ == '__main__':
    main()
