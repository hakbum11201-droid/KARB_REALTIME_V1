"""
event_logger.py - 결정 이벤트 로거 (로그 폭증 방지).

기록 조건:
  1. reason_no_trade == 'OK'
  2. best_net_surplus_bp >= decision_log_min_surplus_bp
  3. 이전 reason과 달라진 경우 (상태 변화)
  4. 에러 또는 비정상

LOW_SURPLUS 반복은 매초 저장하지 않는다.
파일 크기가 max_log_file_mb 초과 시 rotate한다.
"""
import json
import os
import time
import shutil
import glob
import datetime

from config import cfg


class EventLogger:

    def __init__(self):
        base_dir  = os.path.dirname(os.path.abspath(__file__))
        logs_dir  = os.path.normpath(os.path.join(base_dir, '..', 'logs'))
        os.makedirs(logs_dir, exist_ok=True)

        self.decisions_path = os.path.join(logs_dir, 'decisions.jsonl')
        self.errors_path    = os.path.join(logs_dir, 'errors.log')
        self._logs_dir      = logs_dir

        # 심볼별 이전 reason 추적 (상태 변화 감지)
        self._prev_reason: dict[str, str] = {}

    # ──────────────────────────────────────────────────────────────────────
    # 주 인터페이스
    # ──────────────────────────────────────────────────────────────────────

    def log_decision(self, calc_result: dict) -> None:
        """
        조건부 기록:
          1. reason == 'OK'
          2. surplus >= decision_log_min_surplus_bp
          3. reason이 이전과 달라진 경우
        """
        reason  = calc_result.get('reason_no_trade', '')
        sym     = calc_result.get('symbol', '')
        surplus = calc_result.get('best_net_surplus_bp', -9999)

        should_log = False
        if reason == 'OK':
            should_log = True
        elif surplus >= cfg.decision_log_min_surplus_bp and surplus >= 0:
            should_log = True
        elif self._prev_reason.get(sym) != reason:
            should_log = True   # 상태 변화 시 기록

        self._prev_reason[sym] = reason

        if not should_log:
            return

        record = {
            'ts':                     time.time(),
            'symbol':                 sym,
            'best_direction':         calc_result.get('best_direction'),
            'kimchi_premium_pct':     calc_result.get('kimchi_premium_pct'),
            'best_net_surplus_bp':    surplus,
            'gross_gap_krw':          calc_result.get('gross_gap_krw'),
            'net_expected_profit_krw': calc_result.get('net_expected_profit_krw'),
            'reason_no_trade':        reason,
        }
        self._append(self.decisions_path, record)
        self._rotate_by_size(self.decisions_path)

    def log_error(self, context: str, error: Exception) -> None:
        """오류만 errors.log에 기록한다."""
        line = f"{datetime.datetime.now().isoformat()} [{context}] {type(error).__name__}: {error}\n"
        try:
            with open(self.errors_path, 'a', encoding='utf-8') as f:
                f.write(line)
            self._rotate_by_size(self.errors_path)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────
    # 내부
    # ──────────────────────────────────────────────────────────────────────

    def _append(self, path: str, record: dict) -> None:
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception:
            pass

    def _rotate_by_size(self, path: str) -> None:
        """파일 크기가 max_log_file_mb 초과 시 .1 로 rename하고 새 파일 시작."""
        limit_bytes = cfg.max_log_file_mb * 1024 * 1024
        try:
            if os.path.getsize(path) < limit_bytes:
                return
            rotated = path + '.1'
            os.replace(path, rotated)
        except Exception:
            pass
        self._cleanup_old_rotations()

    def _cleanup_old_rotations(self) -> None:
        """log_retention_days 기준 오래된 rotate 파일 삭제."""
        cutoff = time.time() - cfg.log_retention_days * 86400
        for f in glob.glob(os.path.join(self._logs_dir, '*.1')):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
            except Exception:
                pass
