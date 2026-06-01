import requests
import time
from exchange_base import ExchangeBase
from rate_limiter import rate_limiter


class UpbitPublic(ExchangeBase):
    """Upbit 공개 API (인증 불필요)."""

    def __init__(self):
        super().__init__("Upbit")
        self.base_url = "https://api.upbit.com/v1"
        self._session = requests.Session()

    def fetch_order_book(self, symbol: str) -> dict | None:
        """KRW-{symbol} 최우선 호가 1단계 반환."""
        market = f"KRW-{symbol}"
        url = f"{self.base_url}/orderbook?markets={market}"
        try:
            if not rate_limiter.acquire('upbit'):
                return None
            t0 = time.time()
            resp = self._session.get(url, timeout=3)
            latency_ms = (time.time() - t0) * 1000
            if resp.status_code == 429:
                rate_limiter.record_429('upbit')
                return None
            resp.raise_for_status()
            data = resp.json()
            if data and isinstance(data, list):
                obu = data[0]['orderbook_units'][0]
                return {
                    'bid': float(obu['bid_price']),
                    'ask': float(obu['ask_price']),
                    'bid_size': float(obu['bid_size']),
                    'ask_size': float(obu['ask_size']),
                    'latency_ms': latency_ms,
                    'ts': time.time(),
                }
        except Exception:
            pass
        return None

    def fetch_balance(self) -> dict:
        raise NotImplementedError("Use UpbitPrivate for balance queries.")
