"""Bithumb public KRW orderbook client. No private API or order methods."""
import time

import requests

from exchange_base import ExchangeBase
from rate_limiter import rate_limiter


class BithumbPublic(ExchangeBase):
    BASE_URL = 'https://api.bithumb.com/v1'

    def __init__(self):
        super().__init__('Bithumb')
        self._session = requests.Session()

    def fetch_order_book(self, symbol: str) -> dict:
        return self.fetch_all_order_books([symbol]).get(symbol, self._unavailable(symbol))

    def fetch_all_order_books(self, symbols) -> dict[str, dict]:
        symbols = list(symbols)
        markets = ','.join(f'KRW-{symbol}' for symbol in symbols)
        try:
            if not rate_limiter.acquire('bithumb'):
                return {
                    symbol: self._unavailable(symbol, 'BITHUMB_RATE_LIMITED')
                    for symbol in symbols
                }
            started_at = time.time()
            response = self._session.get(
                f'{self.BASE_URL}/orderbook', params={'markets': markets}, timeout=3
            )
            latency_ms = (time.time() - started_at) * 1000
            if response.status_code == 429:
                rate_limiter.record_429('bithumb')
                return {
                    symbol: self._unavailable(symbol, 'BITHUMB_HTTP_429_BACKOFF')
                    for symbol in symbols
                }
            response.raise_for_status()
            rows = response.json()
            fetch_time = time.time()
            quotes = {}
            for row in rows if isinstance(rows, list) else []:
                symbol = str(row.get('market', '')).replace('KRW-', '')
                units = row.get('orderbook_units') or []
                if symbol not in symbols or not units:
                    continue
                top = units[0]
                quote_ts, ts_fallback, ts_normalized = self._normalize_quote_ts(
                    row.get('timestamp'), fetch_time
                )
                quotes[symbol] = {
                    'symbol': symbol,
                    'venue': 'BITHUMB',
                    'bid': float(top['bid_price']),
                    'ask': float(top['ask_price']),
                    'bid_size': float(top['bid_size']),
                    'ask_size': float(top['ask_size']),
                    'timestamp': quote_ts,
                    'ts': quote_ts,
                    'quote_ts_fallback': ts_fallback,
                    'quote_ts_normalized': ts_normalized,
                    'latency_ms': latency_ms,
                    'source': 'rest',
                    'ok': True,
                    'blockers': [],
                }
            return {
                symbol: quotes.get(symbol, self._unavailable(symbol))
                for symbol in symbols
            }
        except Exception:
            return {symbol: self._unavailable(symbol) for symbol in symbols}

    @staticmethod
    def _normalize_quote_ts(value, fetch_time):
        normalized = False
        try:
            ts = float(value or 0)
            while ts > 10_000_000_000:
                ts /= 1000
                normalized = True
            if ts <= 0 or abs(fetch_time - ts) > 60:
                return fetch_time, True, normalized
            return ts, False, normalized
        except (TypeError, ValueError):
            return fetch_time, True, normalized

    @staticmethod
    def _unavailable(symbol: str, blocker='BITHUMB_QUOTE_UNAVAILABLE') -> dict:
        return {
            'symbol': symbol,
            'venue': 'BITHUMB',
            'bid': 0.0,
            'ask': 0.0,
            'bid_size': 0.0,
            'ask_size': 0.0,
            'timestamp': 0.0,
            'ts': 0.0,
            'latency_ms': 0.0,
            'source': 'rest',
            'ok': False,
            'blockers': [blocker],
        }
