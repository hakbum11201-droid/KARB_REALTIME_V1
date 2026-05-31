"""
paper_engine.py - Paper 모드 가상 거래 실행기.
- net_expected_profit_krw 기준으로만 기록
- paper_trades.jsonl 최대 MAX_LINES 행으로 회전
"""
import json
import time
import os


class PaperEngine:
    MAX_LINES = 2000  # 최대 행 수

    def __init__(self):
        self.trades: list[dict] = []
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logs_dir = os.path.normpath(os.path.join(base_dir, '..', 'logs'))
        os.makedirs(logs_dir, exist_ok=True)
        self.log_path = os.path.join(logs_dir, 'paper_trades.jsonl')
        self._line_count = self._count_existing_lines()

    def _count_existing_lines(self) -> int:
        if not os.path.exists(self.log_path):
            return 0
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def _rotate_if_needed(self) -> None:
        if self._line_count < self.MAX_LINES:
            return
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            keep = lines[len(lines) // 2:]
            with open(self.log_path, 'w', encoding='utf-8') as f:
                f.writelines(keep)
            self._line_count = len(keep)
        except Exception:
            pass

    def execute(self, calc_result: dict) -> dict:
        """
        paper trade를 기록한다.
        net_expected_profit_krw만 사용한다. expected_profit_krw 참조 금지.
        """
        trade = {
            'timestamp': time.time(),
            'symbol': calc_result['symbol'],
            'best_direction': calc_result['best_direction'],
            'best_net_surplus_bp': calc_result['best_net_surplus_bp'],
            'gross_gap_krw': calc_result['gross_gap_krw'],
            'net_expected_profit_krw': calc_result['net_expected_profit_krw'],
            'max_fillable_qty': calc_result['max_fillable_qty'],
            'kimchi_premium_pct': calc_result['kimchi_premium_pct'],
            'krw_usdt': calc_result['krw_usdt'],
        }
        self.trades.append(trade)
        self._rotate_if_needed()

        try:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(trade, ensure_ascii=False) + '\n')
            self._line_count += 1
        except Exception:
            pass

        return trade
