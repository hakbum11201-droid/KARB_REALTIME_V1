import requests
import time
from exchange_base import ExchangeBase
from rate_limiter import rate_limiter


class BinancePublic(ExchangeBase):
    """Binance 공개 API (인증 불필요)."""

    def __init__(self):
        super().__init__("Binance")
        self.base_url = "https://api.binance.com/api/v3"
        self._session = requests.Session()

    def fetch_order_book(self, symbol: str) -> dict | None:
        """{symbol}USDT 최우선 호가 반환."""
        market = f"{symbol}USDT"
        url = f"{self.base_url}/depth?symbol={market}&limit=20"
        try:
            if not rate_limiter.acquire('binance'):
                return None
            t0 = time.time()
            resp = self._session.get(url, timeout=3)
            latency_ms = (time.time() - t0) * 1000
            if resp.status_code == 429:
                rate_limiter.record_429('binance')
                return None
            resp.raise_for_status()
            data = resp.json()
            bids = data.get('bids') or []
            asks = data.get('asks') or []
            if bids and asks:
                bid = bids[0]
                ask = asks[0]
                return {
                    'bid': float(bid[0]),
                    'ask': float(ask[0]),
                    'bid_size': float(bid[1]),
                    'ask_size': float(ask[1]),
                    'bids': [{'price': float(price), 'qty': float(qty)} for price, qty in bids[:15]],
                    'asks': [{'price': float(price), 'qty': float(qty)} for price, qty in asks[:15]],
                    'latency_ms': latency_ms,
                    'ts': time.time(),
                }
        except Exception:
            pass
        return None

    def fetch_balance(self) -> dict:
        raise NotImplementedError("Use BinancePrivate for balance queries.")
