import json
import time
import os

class PaperEngine:
    def __init__(self):
        self.trades = []
        self.log_path = os.path.join(os.path.dirname(__file__), '..', 'logs', 'paper_trades.jsonl')

    def execute(self, calc_result):
        """
        paper trade를 기록한다.
        net_expected_profit_krw만 사용한다. expected_profit_krw 참조 금지.
        """
        trade = {
            'timestamp': time.time(),
            'symbol': calc_result['symbol'],
            'best_direction': calc_result['best_direction'],
            'best_net_surplus_bp': calc_result['best_net_surplus_bp'],
            'net_expected_profit_krw': calc_result['net_expected_profit_krw'],
            'gross_gap_krw': calc_result['gross_gap_krw'],
            'max_fillable_qty': calc_result['max_fillable_qty'],
            'krw_usdt': calc_result['krw_usdt'],
        }
        self.trades.append(trade)

        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(trade) + '\n')

        return trade
