"""Background REST top-of-book cache used only when WebSocket quotes are missing."""
import copy
import threading
import time

from rate_limiter import rate_limiter


class RestFallbackQuoteCache:
    def __init__(
        self,
        upbit_public,
        binance_public,
        enabled=True,
        refresh_ms=1000,
        stale_ms=3000,
        skip_on_backoff=True,
    ):
        self.upbit = upbit_public
        self.binance = binance_public
        self.enabled = bool(enabled)
        self.refresh_sec = max(0.05, float(refresh_ms) / 1000)
        self.stale_ms = max(1.0, float(stale_ms))
        self.skip_on_backoff = bool(skip_on_backoff)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._symbols = []
        self._quotes = {}
        self._last_update_at = 0.0
        self._last_success_at = 0.0
        self._last_error = ''
        self._refresh_count = 0
        self._fail_count = 0
        self._skip_count = 0
        self._upbit_429_skip_count = 0
        self._binance_429_skip_count = 0
        self._quote_ts_fallback_count = 0
        self._quote_ts_normalized_count = 0

    def start(self, symbols):
        self.update_symbols(symbols)
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name='rest-fallback-quote-cache', daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=min(1.0, self.refresh_sec + 0.1))

    def update_symbols(self, symbols):
        items = list(dict.fromkeys(symbols))
        with self._lock:
            self._symbols = items
            self._quotes = {
                symbol: self._quotes.get(symbol, {})
                for symbol in items
            }

    def get_snapshot(self):
        now = time.time()
        with self._lock:
            quotes = copy.deepcopy(self._quotes)
        return {
            symbol: item
            for symbol, item in quotes.items()
            if self._is_fresh(item, now)
        }

    def get_symbol(self, symbol):
        now = time.time()
        with self._lock:
            item = copy.deepcopy(self._quotes.get(symbol))
        return item if item and self._is_fresh(item, now) else None

    def get_status(self):
        now = time.time()
        with self._lock:
            quotes = copy.deepcopy(self._quotes)
            last_success_at = self._last_success_at
            return {
                'enabled': self.enabled,
                'running': bool(self._thread and self._thread.is_alive()),
                'symbols': list(self._symbols),
                'last_update_at': self._last_update_at,
                'last_success_at': last_success_at,
                'last_error': self._last_error,
                'refresh_count': self._refresh_count,
                'fail_count': self._fail_count,
                'quote_count': len(quotes),
                'stale_count': sum(
                    1 for item in quotes.values() if not self._is_fresh(item, now)
                ),
                'skip_count': self._skip_count,
                'upbit_429_skip_count': self._upbit_429_skip_count,
                'binance_429_skip_count': self._binance_429_skip_count,
                'quote_ts_fallback_count': self._quote_ts_fallback_count,
                'quote_ts_normalized_count': self._quote_ts_normalized_count,
                'last_success_age_ms': (
                    round(max(0.0, now - last_success_at) * 1000, 2)
                    if last_success_at else None
                ),
                'lock_type': 'RLock',
            }

    def refresh_once(self):
        fetch_time = time.time()
        with self._lock:
            symbols = list(self._symbols)
            self._refresh_count += 1
            self._last_update_at = fetch_time
        updated = False
        try:
            for symbol in symbols:
                legs = {}
                if self._backoff_active('upbit'):
                    self._record_backoff_skip('upbit')
                else:
                    quote = self.upbit.fetch_order_book(symbol)
                    if quote:
                        legs['upbit'] = self._normalize_quote(quote, fetch_time)
                if self._backoff_active('binance'):
                    self._record_backoff_skip('binance')
                else:
                    quote = self.binance.fetch_order_book(symbol)
                    if quote:
                        legs['binance'] = self._normalize_quote(quote, fetch_time)
                if legs:
                    with self._lock:
                        previous = self._quotes.setdefault(symbol, {})
                        previous.update(legs)
                        if previous.get('upbit') and previous.get('binance'):
                            self._quotes[symbol] = self._build_symbol_quote(symbol, previous)
                    updated = True
            with self._lock:
                if updated:
                    self._last_success_at = time.time()
                    self._last_error = ''
                elif symbols:
                    self._fail_count += 1
                    self._last_error = 'REST_FALLBACK_CACHE_NO_QUOTES'
        except Exception as exc:
            with self._lock:
                self._fail_count += 1
                self._last_error = f'{type(exc).__name__}: {exc}'[:300]

    def _run(self):
        while not self._stop.is_set():
            self.refresh_once()
            self._stop.wait(self.refresh_sec)

    def _backoff_active(self, exchange):
        return self.skip_on_backoff and rate_limiter.should_backoff(exchange)

    def _record_backoff_skip(self, exchange):
        with self._lock:
            self._skip_count += 1
            if exchange == 'upbit':
                self._upbit_429_skip_count += 1
            else:
                self._binance_429_skip_count += 1

    def _normalize_quote(self, quote, fetch_time):
        item = dict(quote)
        item['ts'] = self._normalize_ts(item.get('ts', item.get('event_ts')), fetch_time)
        item['source'] = 'rest_cache'
        item['rest_cache_updated_at'] = fetch_time
        item['rest_cache_age_ms'] = 0.0
        return item

    def _normalize_ts(self, value, fetch_time):
        try:
            ts = float(value or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > 10_000_000_000:
            ts /= 1000
            with self._lock:
                self._quote_ts_normalized_count += 1
        if ts <= 0 or abs(fetch_time - ts) * 1000 > self.stale_ms:
            with self._lock:
                self._quote_ts_fallback_count += 1
            return fetch_time
        return ts

    def _build_symbol_quote(self, symbol, legs):
        upbit = dict(legs['upbit'])
        binance = dict(legs['binance'])
        updated_at = max(
            float(upbit.get('rest_cache_updated_at', 0) or 0),
            float(binance.get('rest_cache_updated_at', 0) or 0),
        )
        now = time.time()
        for quote in (upbit, binance):
            quote['rest_cache_age_ms'] = round(
                max(0.0, now - float(quote.get('ts', 0) or 0)) * 1000, 2
            )
        return {
            'symbol': symbol,
            'upbit': upbit,
            'binance': binance,
            'timestamp': max(upbit['ts'], binance['ts']),
            'ts': min(upbit['ts'], binance['ts']),
            'source': 'rest_cache',
            'rest_cache_updated_at': updated_at,
            'rest_cache_age_ms': round(
                max(0.0, now - min(upbit['ts'], binance['ts'])) * 1000, 2
            ),
        }

    def _is_fresh(self, item, now):
        if not item or not item.get('upbit') or not item.get('binance'):
            return False
        oldest_ts = min(
            float(item['upbit'].get('ts', 0) or 0),
            float(item['binance'].get('ts', 0) or 0),
        )
        return oldest_ts > 0 and (now - oldest_ts) * 1000 <= self.stale_ms
