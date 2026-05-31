"""
performance_tracker.py - 누적 paper/live 성과 추적기.
bounded_collector 위에서 동작하며, 최대 N건의 거래 기록만 메모리에 보관한다.
"""
import time
from collections import deque
from typing import Optional


class PerformanceTracker:
    """
    Paper 거래 결과를 누적 집계한다.
    - 최대 MAX_TRADES 건을 deque로 보관 (FIFO, 메모리 안전)
    - 윈도우 성과 통계 제공
    """

    MAX_TRADES = 200  # 메모리에 보관할 최대 거래 수

    def __init__(self):
        self._trades: deque[dict] = deque(maxlen=self.MAX_TRADES)
        self._total_net_profit_krw: float = 0.0
        self._total_gross_gap_krw: float = 0.0
        self._trade_count: int = 0
        self._win_count: int = 0
        self._started_at: float = time.time()

    def record(self, trade: dict) -> None:
        """trade 딕셔너리를 기록한다. net_expected_profit_krw 키 필수."""
        net = trade.get('net_expected_profit_krw', 0.0)
        gross = trade.get('gross_gap_krw', 0.0)
        self._trades.append({**trade, '_recorded_at': time.time()})
        self._total_net_profit_krw += net
        self._total_gross_gap_krw += gross
        self._trade_count += 1
        if net > 0:
            self._win_count += 1

    def summary(self) -> dict:
        """현재 누적 성과 요약 딕셔너리 반환."""
        win_rate = (self._win_count / self._trade_count * 100) if self._trade_count else 0.0
        avg_net = (self._total_net_profit_krw / self._trade_count) if self._trade_count else 0.0
        elapsed_h = (time.time() - self._started_at) / 3600

        # 최근 10건 평균
        recent = list(self._trades)[-10:]
        recent_avg = (
            sum(t.get('net_expected_profit_krw', 0) for t in recent) / len(recent)
            if recent else 0.0
        )

        return {
            'trade_count': self._trade_count,
            'win_count': self._win_count,
            'win_rate_pct': round(win_rate, 2),
            'total_net_profit_krw': round(self._total_net_profit_krw, 2),
            'total_gross_gap_krw': round(self._total_gross_gap_krw, 2),
            'avg_net_profit_krw': round(avg_net, 2),
            'recent10_avg_net_krw': round(recent_avg, 2),
            'elapsed_hours': round(elapsed_h, 3),
            'buffered_trades': len(self._trades),
        }

    def last_trades(self, n: int = 20) -> list[dict]:
        """최근 n건 거래 목록 반환."""
        return list(self._trades)[-n:]
