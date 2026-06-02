"""Background Bithumb public quote cache for domestic KRW paper calculations."""
import copy
import threading
import time


class BithumbQuoteCache:
    def __init__(
        self, client, enabled=True, refresh_ms=700, stale_ms=5000,
        grace_ms=3000, allow_last_good_on_stale=True, max_failures=10,
    ):
        self.client = client
        self.enabled = bool(enabled)
        self.refresh_ms = max(50, int(refresh_ms))
        self.stale_ms = max(1, int(stale_ms))
        self.grace_ms = max(0, int(grace_ms))
        self.allow_last_good_on_stale = bool(allow_last_good_on_stale)
        self.max_failures = max(1, int(max_failures))
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._symbols = []
        self._quotes = {}
        self._last_update_at = 0.0
        self._last_success_at = 0.0
        self._last_error = ''
        self._refresh_count = 0
        self._fail_count = 0
        self._quote_ts_fallback_count = 0
        self._quote_ts_normalized_count = 0

    def start(self, symbols):
        self.update_symbols(symbols)
        if not self.enabled:
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name='bithumb-quote-cache', daemon=True
            )
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(1.0, self.refresh_ms / 1000 + 0.5))

    def update_symbols(self, symbols):
        normalized = list(dict.fromkeys(str(symbol) for symbol in symbols if symbol))
        with self._lock:
            self._symbols = normalized
            self._quotes = {
                symbol: quote for symbol, quote in self._quotes.items()
                if symbol in normalized
            }

    def get_snapshot(self):
        now = time.time()
        with self._lock:
            snapshot = copy.deepcopy(self._quotes)
            last_success_at = self._last_success_at
        last_good_age_ms = max(0.0, now - last_success_at) * 1000 if last_success_at else None
        for quote in snapshot.values():
            ts = float(quote.get('ts', quote.get('timestamp', 0)) or 0)
            quote_age_ms = max(0.0, now - ts) * 1000 if ts else None
            soft_stale = quote_age_ms is None or quote_age_ms > self.stale_ms
            stale_grace = bool(
                soft_stale
                and self.allow_last_good_on_stale
                and last_good_age_ms is not None
                and last_good_age_ms <= self.stale_ms + self.grace_ms
            )
            quote['stale'] = bool(soft_stale and not stale_grace)
            quote['stale_grace'] = stale_grace
        return snapshot

    def get_status(self):
        snapshot = self.get_snapshot()
        with self._lock:
            last_success_age_ms = round(max(0.0, time.time() - self._last_success_at) * 1000, 2) if self._last_success_at else None
            stale_grace_count = sum(1 for quote in snapshot.values() if quote.get('stale_grace'))
            stale_hard_count = sum(1 for quote in snapshot.values() if quote.get('stale'))
            return {
                'enabled': self.enabled,
                'running': bool(self._thread and self._thread.is_alive()),
                'symbols': list(self._symbols),
                'last_update_at': self._last_update_at,
                'last_success_at': self._last_success_at,
                'last_success_age_ms': last_success_age_ms,
                'last_good_age_ms': last_success_age_ms,
                'last_error': self._last_error,
                'refresh_count': self._refresh_count,
                'fail_count': self._fail_count,
                'quote_ts_fallback_count': self._quote_ts_fallback_count,
                'quote_ts_normalized_count': self._quote_ts_normalized_count,
                'stale_count': stale_hard_count,
                'stale_grace_count': stale_grace_count,
                'stale_hard_count': stale_hard_count,
                'stale_soft_count': stale_grace_count + stale_hard_count,
                'quote_count': len(snapshot),
            }

    def refresh_once(self):
        with self._lock:
            symbols = list(self._symbols)
        if not symbols:
            return
        now = time.time()
        try:
            fetched = self.client.fetch_all_order_books(symbols)
            valid = {
                symbol: self._normalize_quote_ts(quote, now) for symbol, quote in fetched.items()
                if symbol in symbols and isinstance(quote, dict) and quote.get('ok')
            }
            invalid = [symbol for symbol in symbols if symbol not in valid]
            with self._lock:
                self._last_update_at = now
                self._refresh_count += 1
                if valid:
                    self._quotes.update(valid)
                    self._last_success_at = now
                    self._last_error = (
                        f'BITHUMB_PARTIAL_QUOTES:{",".join(invalid)}' if invalid else ''
                    )
                else:
                    self._fail_count += 1
                    self._last_error = self._failure_reason(fetched)
        except Exception as exc:
            with self._lock:
                self._last_update_at = now
                self._refresh_count += 1
                self._fail_count += 1
                self._last_error = f'{type(exc).__name__}: {exc}'

    def _normalize_quote_ts(self, quote, fetch_time):
        quote = dict(quote)
        raw_ts = quote.get('ts', quote.get('timestamp'))
        normalized = False
        fallback = False
        try:
            ts = float(raw_ts or 0)
            while ts > 10_000_000_000:
                ts /= 1000
                normalized = True
            if ts <= 0 or abs(fetch_time - ts) * 1000 > self.stale_ms:
                ts = fetch_time
                fallback = True
        except (TypeError, ValueError):
            ts = fetch_time
            fallback = True
        normalized = normalized or bool(quote.get('quote_ts_normalized'))
        fallback = fallback or bool(quote.get('quote_ts_fallback'))
        quote['timestamp'] = ts
        quote['ts'] = ts
        quote['quote_ts_normalized'] = normalized
        quote['quote_ts_fallback'] = fallback
        if fallback:
            self._quote_ts_fallback_count += 1
        if normalized:
            self._quote_ts_normalized_count += 1
        return quote

    def _run(self):
        while not self._stop_event.is_set():
            self.refresh_once()
            self._stop_event.wait(self.refresh_ms / 1000)

    @staticmethod
    def _failure_reason(fetched):
        blockers = []
        for quote in (fetched or {}).values():
            blockers.extend(quote.get('blockers', []))
        return blockers[0] if blockers else 'BITHUMB_QUOTE_REFRESH_FAILED'
