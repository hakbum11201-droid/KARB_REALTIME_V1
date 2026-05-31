"""
event_logger.py - 결정 이벤트 로거.
- decisions.jsonl 최대 MAX_LINES 행으로 회전 (초과 시 절반 삭제)
- 키 값은 절대 기록하지 않는다.
"""
import json
import os
import time


class EventLogger:
    MAX_LINES = 5000  # 최대 행 수: 초과 시 앞 절반 삭제

    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logs_dir = os.path.join(base_dir, '..', 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        self.decisions_path = os.path.normpath(os.path.join(logs_dir, 'decisions.jsonl'))
        self._line_count = self._count_existing_lines()

    def _count_existing_lines(self) -> int:
        if not os.path.exists(self.decisions_path):
            return 0
        try:
            with open(self.decisions_path, 'r', encoding='utf-8') as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def _rotate_if_needed(self) -> None:
        """MAX_LINES 초과 시 앞 절반 제거."""
        if self._line_count < self.MAX_LINES:
            return
        try:
            with open(self.decisions_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            keep = lines[len(lines) // 2:]
            with open(self.decisions_path, 'w', encoding='utf-8') as f:
                f.writelines(keep)
            self._line_count = len(keep)
        except Exception:
            pass

    def log_decision(self, calc_result: dict) -> None:
        """calc_result에서 안전한 필드만 기록한다. API 키 필드 없음."""
        record = {
            'ts': time.time(),
            'symbol': calc_result.get('symbol'),
            'best_direction': calc_result.get('best_direction'),
            'kimchi_premium_pct': calc_result.get('kimchi_premium_pct'),
            'best_net_surplus_bp': calc_result.get('best_net_surplus_bp'),
            'gross_gap_krw': calc_result.get('gross_gap_krw'),
            'net_expected_profit_krw': calc_result.get('net_expected_profit_krw'),
            'reason_no_trade': calc_result.get('reason_no_trade', ''),
        }
        self._rotate_if_needed()
        try:
            with open(self.decisions_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
            self._line_count += 1
        except Exception:
            pass
