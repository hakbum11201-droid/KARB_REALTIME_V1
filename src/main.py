import argparse
import time
import json
import os
from config import cfg
from upbit_public import UpbitPublic
from binance_public import BinancePublic
from fx_oracle import FxOracle
from quote_engine import QuoteEngine
from arb_calculator import ArbCalculator
from inventory_manager import InventoryManager
from risk_guard import RiskGuard
from paper_engine import PaperEngine
from execution_engine import ExecutionEngine
from event_logger import EventLogger

def main():
    parser = argparse.ArgumentParser(description="KARB_REALTIME_V1 Engine")
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--duration-sec', type=int, default=0, help='Run for X seconds')
    args = parser.parse_args()

    print(f"Starting KARB_REALTIME_V1 in mode: {cfg.mode}")
    
    upbit_pub = UpbitPublic()
    binance_pub = BinancePublic()
    
    fx_oracle = FxOracle(upbit_pub, binance_pub)
    quote_engine = QuoteEngine(upbit_pub, binance_pub, cfg.symbols)
    arb_calc = ArbCalculator()
    inv_mgr = InventoryManager()
    risk_guard = RiskGuard()
    paper_eng = PaperEngine()
    exec_eng = ExecutionEngine()
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
            
            with open(quotes_path, 'w', encoding='utf-8') as f:
                json.dump(quotes, f)
            
            for sym, q in quotes.items():
                calc_res = arb_calc.calculate(sym, q['upbit'], q['binance'], krw_usdt)
                
                is_safe = risk_guard.check_trade(calc_res)
                event_logger.log_decision(calc_res)
                
                if is_safe:
                    if cfg.mode == 'paper':
                        paper_eng.execute(calc_res)
                        print(f"[{sym}] PAPER TRADE executed! Profit: {calc_res['expected_profit_krw']:.0f} KRW")
                    else:
                        exec_eng.execute(calc_res)
                        
            state = {
                'mode': cfg.mode,
                'krw_usdt': krw_usdt,
                'paper_trade_count': len(paper_eng.trades),
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
