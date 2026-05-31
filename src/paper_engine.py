import json
import time
import os

class PaperEngine:
    def __init__(self):
        self.trades = []
        self.log_path = os.path.join(os.path.dirname(__file__), '..', 'logs', 'paper_trades.jsonl')
        
    def execute(self, calc_result):
        trade = {
            'timestamp': time.time(),
            'symbol': calc_result['symbol'],
            'profit_krw': calc_result['expected_profit_krw']
        }
        self.trades.append(trade)
        
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(trade) + '\n')
            
        return trade
