"""Bithumb public KRW orderbook client. No private API or order methods."""
import time

import requests

from exchange_base import ExchangeBase


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
            started_at = time.time()
            response = self._session.get(
                f'{self.BASE_URL}/orderbook', params={'markets': markets}, timeout=3
            )
            latency_ms = (time.time() - started_at) * 1000
            response.raise_for_status()
            rows = response.json()
            quotes = {}
            for row in rows if isinstance(rows, list) else []:
                symbol = str(row.get('market', '')).replace('KRW-', '')
                units = row.get('orderbook_units') or []
                if symbol not in symbols or not units:
                    continue
                top = units[0]
                quotes[symbol] = {
                    'symbol': symbol,
                    'venue': 'BITHUMB',
                    'bid': float(top['bid_price']),
                    'ask': float(top['ask_price']),
                    'bid_size': float(top['bid_size']),
                    'ask_size': float(top['ask_size']),
                    'timestamp': float(row.get('timestamp', 0) or 0) / 1000 or time.time(),
                    'ts': float(row.get('timestamp', 0) or 0) / 1000 or time.time(),
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
    def _unavailable(symbol: str) -> dict:
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
            'blockers': ['BITHUMB_QUOTE_UNAVAILABLE'],
        }
