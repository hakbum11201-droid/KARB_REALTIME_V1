import requests
import time
from exchange_base import ExchangeBase


class BinancePublic(ExchangeBase):
    """Binance 공개 API (인증 불필요)."""

    def __init__(self):
        super().__init__("Binance")
        self.base_url = "https://api.binance.com/api/v3"
        self._session = requests.Session()

    def fetch_order_book(self, symbol: str) -> dict | None:
        """{symbol}USDT 최우선 호가 반환."""
        market = f"{symbol}USDT"
        url = f"{self.base_url}/ticker/bookTicker?symbol={market}"
        try:
            t0 = time.time()
            resp = self._session.get(url, timeout=3)
            latency_ms = (time.time() - t0) * 1000
            data = resp.json()
            if 'bidPrice' in data:
                return {
                    'bid': float(data['bidPrice']),
                    'ask': float(data['askPrice']),
                    'bid_size': float(data['bidQty']),
                    'ask_size': float(data['askQty']),
                    'latency_ms': latency_ms,
                    'ts': time.time(),
                }
        except Exception:
            pass
        return None

    def fetch_balance(self) -> dict:
        raise NotImplementedError("Use BinancePrivate for balance queries.")
