"""Thread-safe top-of-book cache for WebSocket and REST quote snapshots."""
import copy
import threading
import time
from collections import deque


def _percentile(values, pct=95):
    if not values:
        return 0.0
    items = sorted(values)
    return round(items[min(int(len(items) * pct / 100), len(items) - 1)], 2)


class OrderbookCache:
    def __init__(self, symbols, stale_quote_ms=1500):
        self.symbols = list(symbols)
        self.stale_quote_ms = stale_quote_ms
        self._lock = threading.Lock()
        self._quotes = {symbol: {} for symbol in self.symbols}
        self._latencies = deque(maxlen=1000)
        self._quote_count = 0
        self._rest_fallback_count = 0
        self._stale_quote_count = 0
        self._stale_symbols = set()

    def record_rest_fallback(self, count=1):
        with self._lock:
            self._rest_fallback_count += int(count)

    def update(self, exchange: str, symbol: str, quote: dict, source='ws'):
        now = time.time()
        item = {
            'bid': float(quote['bid']),
            'ask': float(quote['ask']),
            'bid_size': float(quote.get('bid_size', 0) or 0),
            'ask_size': float(quote.get('ask_size', 0) or 0),
            'latency_ms': round(float(quote.get('latency_ms', 0) or 0), 2),
            'ts': float(quote.get('ts', now) or now),
            'source': source,
        }
        with self._lock:
            self._quotes.setdefault(symbol, {})[exchange] = item
            self._quote_count += 1
            self._latencies.append(item['latency_ms'])

    def snapshot(self, require_fresh=True) -> dict:
        now = time.time()
        output = {}
        with self._lock:
            quotes = copy.deepcopy(self._quotes)
        for symbol, exchanges in quotes.items():
            upbit, binance = exchanges.get('upbit'), exchanges.get('binance')
            if not upbit or not binance:
                continue
            age_sec = max(now - upbit['ts'], now - binance['ts'])
            if require_fresh and age_sec * 1000 > self.stale_quote_ms:
                continue
            output[symbol] = {
                'symbol': symbol,
                'upbit': upbit,
                'binance': binance,
                'timestamp': max(upbit['ts'], binance['ts']),
                'source': 'ws' if upbit.get('source') == binance.get('source') == 'ws' else 'rest',
                'quote_age_sec': round(max(0, age_sec), 3),
            }
        return output

    def metrics(self) -> dict:
        now = time.time()
        snap = self.snapshot(require_fresh=False)
        symbols = []
        stale_symbols = set()
        for symbol in self.symbols:
            item = snap.get(symbol, {})
            age_ms = float(item.get('quote_age_sec', 0) or 0) * 1000 if item else None
            stale = age_ms is None or age_ms > self.stale_quote_ms
            if stale:
                stale_symbols.add(symbol)
            upbit = item.get('upbit', {})
            binance = item.get('binance', {})
            symbols.append({
                'symbol': symbol,
                'quote_source': item.get('source', 'missing'),
                'quote_age_ms': round(age_ms, 2) if age_ms is not None else None,
                'upbit_source': upbit.get('source', 'missing'),
                'binance_source': binance.get('source', 'missing'),
                'stale': stale,
            })
        ages = [item['quote_age_ms'] / 1000 for item in symbols if item['quote_age_ms'] is not None]
        with self._lock:
            self._stale_quote_count += len(stale_symbols - self._stale_symbols)
            self._stale_symbols = stale_symbols
            source_summary = {
                'ws': sum(1 for item in symbols if item['quote_source'] == 'ws' and not item['stale']),
                'rest': sum(1 for item in symbols if item['quote_source'] == 'rest' and not item['stale']),
                'stale': len(stale_symbols),
            }
            return {
                'quote_count': self._quote_count,
                'p95_quote_latency_ms': _percentile(self._latencies),
                'last_quote_age_sec': round(max(ages), 3) if ages else None,
                'quote_source_summary': source_summary,
                'ws_symbols_ok': source_summary['ws'],
                'rest_fallback_count': self._rest_fallback_count,
                'quote_stale_count': self._stale_quote_count,
                'symbols': symbols,
            }
