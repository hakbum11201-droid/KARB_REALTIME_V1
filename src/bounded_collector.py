"""
bounded_collector.py - 크기 제한 있는 실시간 데이터 수집기.
메모리에 최대 MAX_TICKS 건만 보관하며 디스크 대량 저장을 하지 않는다.
"""
from collections import deque
from typing import Optional
import time


class BoundedCollector:
    """
    심볼별 최신 호가/계산 결과를 제한된 크기의 deque로 보관한다.
    - 디스크 무한 저장 구조 없음
    - MAX_TICKS 초과 시 자동으로 오래된 데이터 제거 (FIFO)
    """

    MAX_TICKS = 500  # 심볼당 최대 보관 틱 수

    def __init__(self):
        # { symbol: deque[dict] }
        self._data: dict[str, deque] = {}

    def push(self, symbol: str, record: dict) -> None:
        """심볼에 대한 신규 데이터를 추가한다."""
        if symbol not in self._data:
            self._data[symbol] = deque(maxlen=self.MAX_TICKS)
        self._data[symbol].append({**record, '_ts': time.time()})

    def latest(self, symbol: str) -> Optional[dict]:
        """심볼의 가장 최근 데이터 1건 반환. 없으면 None."""
        if symbol not in self._data or not self._data[symbol]:
            return None
        return self._data[symbol][-1]

    def recent(self, symbol: str, n: int = 60) -> list[dict]:
        """심볼의 최근 n건 데이터 반환."""
        if symbol not in self._data:
            return []
        return list(self._data[symbol])[-n:]

    def symbols(self) -> list[str]:
        """수집 중인 심볼 목록 반환."""
        return list(self._data.keys())

    def stats(self) -> dict:
        """전체 수집 현황 요약."""
        return {
            sym: {'count': len(dq), 'maxlen': dq.maxlen}
            for sym, dq in self._data.items()
        }
