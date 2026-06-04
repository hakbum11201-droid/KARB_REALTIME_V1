"""Background REST top-of-book cache used only when WebSocket quotes are missing."""
import copy
from collections import deque
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
        rest_cache_upbit_refresh_ms=3000,
        rest_cache_binance_refresh_ms=1000,
        rest_cache_skip_upbit_when_ws_ok=True,
        rest_cache_ws_fresh_threshold_ms=1500,
        recheck_cooldown_sec=5,
        recheck_max_queue_size=50,
        priority_worker_enabled=True,
        inflight_dedupe=True,
        priority_fetch_timeout_ms=700,
        priority_max_workers=1,
        completed_recheck_ttl_sec=30,
    ):
        self.upbit = upbit_public
        self.binance = binance_public
        self.enabled = bool(enabled)
        self.refresh_sec = max(0.05, float(refresh_ms) / 1000)
        self.stale_ms = max(1.0, float(stale_ms))
        self.skip_on_backoff = bool(skip_on_backoff)
        self.upbit_refresh_sec = max(0.05, float(rest_cache_upbit_refresh_ms) / 1000)
        self.binance_refresh_sec = max(0.05, float(rest_cache_binance_refresh_ms) / 1000)
        self.skip_upbit_when_ws_ok = bool(rest_cache_skip_upbit_when_ws_ok)
        self.ws_fresh_threshold_ms = max(1.0, float(rest_cache_ws_fresh_threshold_ms))
        self.recheck_cooldown_sec = max(0.0, float(recheck_cooldown_sec))
        self.recheck_max_queue_size = max(1, int(recheck_max_queue_size))
        self.priority_worker_enabled = bool(priority_worker_enabled)
        self.inflight_dedupe = bool(inflight_dedupe)
        self.priority_fetch_timeout_ms = max(1, int(priority_fetch_timeout_ms))
        self.priority_max_workers = max(1, int(priority_max_workers))
        self.completed_recheck_ttl_sec = max(1.0, float(completed_recheck_ttl_sec))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._priority_event = threading.Event()
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
        self._last_exchange_refresh_at = {'upbit': 0.0, 'binance': 0.0}
        self._upbit_ws_fresh_at = {}
        self._upbit_ws_fresh_skip_count = 0
        self._recheck_queue = deque()
        self._recheck_enqueued = set()
        self._recheck_inflight = set()
        self._recheck_meta = {}
        self._recheck_last_requested_at = {}
        self._recheck_request_count = 0
        self._recheck_execute_count = 0
        self._recheck_skip_cooldown_count = 0
        self._recheck_deduped_count = 0
        self._recheck_fail_count = 0
        self._recheck_last_symbol = ''
        self._recheck_last_pair_id = ''
        self._recheck_last_at = 0.0
        self._recheck_last_error = ''
        self._priority_worker_wake_count = 0
        self._priority_symbol_fetch_count = 0
        self._priority_full_refresh_fallback_count = 0
        self._priority_fetch_total_ms = 0.0
        self._priority_fetch_samples = 0
        self._priority_fetch_last_ms = 0.0
        self._priority_fetch_last_symbol = ''
        self._priority_fetch_last_error = ''
        self._completed_rechecks = deque(maxlen=100)
        self._completed_recheck_count = 0

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
        self._priority_event.set()
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
            self._upbit_ws_fresh_at = {
                symbol: ts for symbol, ts in self._upbit_ws_fresh_at.items()
                if symbol in items
            }

    def update_ws_fresh_symbols(self, symbols):
        now = time.time()
        with self._lock:
            self._upbit_ws_fresh_at = {
                symbol: now for symbol in symbols if symbol in self._symbols
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
        upbit_rate = rate_limiter.get_status().get('exchanges', {}).get('upbit', {})
        upbit_calls = upbit_rate.get('rest_call_counts', {})
        upbit_429 = upbit_rate.get('api_429_counts', {})
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
                'upbit_ws_fresh_skip_count': self._upbit_ws_fresh_skip_count,
                'upbit_rest_call_count_rest_cache': upbit_calls.get('rest_cache', 0),
                'upbit_429_count_rest_cache': upbit_429.get('rest_cache', 0),
                'last_success_age_ms': (
                    round(max(0.0, now - last_success_at) * 1000, 2)
                    if last_success_at else None
                ),
                'lock_type': 'RLock',
                **self.get_recheck_status(),
            }

    def request_priority_refresh(
        self, pair_id, symbol, reason=None,
        original_surplus_bp=None, original_net_krw=None,
    ):
        now = time.time()
        symbol = str(symbol or '').upper()
        pair_id = str(pair_id or 'UPBIT_BINANCE')
        if not self.enabled or not symbol:
            return {'ok': False, 'queued': False, 'reason': 'DISABLED_OR_EMPTY_SYMBOL'}
        key = (pair_id, symbol)
        with self._lock:
            if self.inflight_dedupe and (key in self._recheck_enqueued or key in self._recheck_inflight):
                self._recheck_deduped_count += 1
                self._recheck_meta[key] = {
                    'pair_id': pair_id, 'symbol': symbol, 'reason': reason or '',
                    'requested_at': now,
                    'original_surplus_bp': original_surplus_bp,
                    'original_net_krw': original_net_krw,
                }
                reason_code = 'SKIP_INFLIGHT' if key in self._recheck_inflight else 'RECHECK_ALREADY_QUEUED'
                return {'ok': False, 'queued': False, 'reason': reason_code}
            last = self._recheck_last_requested_at.get(key, 0.0)
            if now - last < self.recheck_cooldown_sec:
                self._recheck_skip_cooldown_count += 1
                return {'ok': False, 'queued': False, 'reason': 'SKIP_COOLDOWN'}
            if len(self._recheck_queue) >= self.recheck_max_queue_size:
                return {'ok': False, 'queued': False, 'reason': 'RECHECK_QUEUE_FULL'}
            self._recheck_queue.append({
                'pair_id': pair_id, 'symbol': symbol, 'reason': reason or '',
                'requested_at': now,
                'original_surplus_bp': original_surplus_bp,
                'original_net_krw': original_net_krw,
            })
            self._recheck_enqueued.add(key)
            self._recheck_meta[key] = {
                'pair_id': pair_id, 'symbol': symbol, 'reason': reason or '',
                'requested_at': now,
                'original_surplus_bp': original_surplus_bp,
                'original_net_krw': original_net_krw,
            }
            self._recheck_last_requested_at[key] = now
            self._recheck_request_count += 1
            self._recheck_last_symbol = symbol
            self._recheck_last_pair_id = pair_id
            if self.priority_worker_enabled:
                self._priority_worker_wake_count += 1
                self._priority_event.set()
            return {'ok': True, 'queued': True, 'reason': 'RECHECK_REQUESTED'}

    def get_recheck_status(self):
        with self._lock:
            return {
                'recheck_queue_size': len(self._recheck_queue),
                'recheck_request_count': self._recheck_request_count,
                'recheck_execute_count': self._recheck_execute_count,
                'recheck_skip_cooldown_count': self._recheck_skip_cooldown_count,
                'recheck_inflight_count': len(self._recheck_inflight),
                'recheck_deduped_count': self._recheck_deduped_count,
                'recheck_inflight_symbols': [f'{pair}:{symbol}' for pair, symbol in sorted(self._recheck_inflight)],
                'recheck_fail_count': self._recheck_fail_count,
                'recheck_last_symbol': self._recheck_last_symbol,
                'recheck_last_pair_id': self._recheck_last_pair_id,
                'recheck_last_at': self._recheck_last_at,
                'recheck_last_error': self._recheck_last_error,
                'priority_worker_wake_count': self._priority_worker_wake_count,
                'priority_symbol_fetch_count': self._priority_symbol_fetch_count,
                'priority_full_refresh_fallback_count': self._priority_full_refresh_fallback_count,
                'priority_fetch_avg_ms': round(
                    self._priority_fetch_total_ms / self._priority_fetch_samples, 2
                ) if self._priority_fetch_samples else 0.0,
                'priority_fetch_last_ms': round(self._priority_fetch_last_ms, 2),
                'priority_fetch_last_symbol': self._priority_fetch_last_symbol,
                'priority_fetch_last_error': self._priority_fetch_last_error,
                'completed_recheck_count': self._completed_recheck_count,
                'completed_recheck_queue_size': len(self._completed_rechecks),
            }

    def get_completed_rechecks(self):
        now = time.time()
        with self._lock:
            return [
                copy.deepcopy(item) for item in self._completed_rechecks
                if now - float(item.get('received_at', 0) or 0) <= self.completed_recheck_ttl_sec
            ]

    def pop_completed_rechecks(self, since_ts=None):
        now = time.time()
        results = []
        keep = deque(maxlen=100)
        with self._lock:
            while self._completed_rechecks:
                item = self._completed_rechecks.popleft()
                received_at = float(item.get('received_at', 0) or 0)
                if now - received_at > self.completed_recheck_ttl_sec:
                    continue
                if since_ts is not None and received_at <= float(since_ts or 0):
                    keep.append(item)
                    continue
                results.append(copy.deepcopy(item))
            self._completed_rechecks = keep
        return results

    def _append_completed_recheck(self, request, refresh_started_at, refreshed_at, fetch_ms, source, ok, error='', quote_ts=None):
        completed = {
            'id': (
                f"{request.get('pair_id', '')}:{request.get('symbol', '')}:"
                f"{float(request.get('requested_at', 0) or 0):.6f}:{float(refreshed_at or 0):.6f}"
            ),
            'pair_id': request.get('pair_id', 'UPBIT_BINANCE'),
            'symbol': request.get('symbol', ''),
            'requested_at': request.get('requested_at'),
            'refresh_started_at': refresh_started_at,
            'refreshed_at': refreshed_at,
            'fetch_ms': round(float(fetch_ms or 0), 2),
            'source': source,
            'ok': bool(ok),
            'error': error or '',
            'quote_ts': quote_ts,
            'received_at': time.time(),
        }
        with self._lock:
            self._completed_rechecks.append(completed)
            self._completed_recheck_count += 1

    def refresh_once(self):
        fetch_time = time.time()
        self._process_priority_queue(fetch_time)
        with self._lock:
            symbols = list(self._symbols)
            self._refresh_count += 1
            self._last_update_at = fetch_time
        updated = False
        try:
            refresh_upbit = self._exchange_refresh_due('upbit', fetch_time)
            refresh_binance = self._exchange_refresh_due('binance', fetch_time)
            for symbol in symbols:
                legs = {}
                if not refresh_upbit:
                    pass
                elif self._upbit_ws_is_fresh(symbol, fetch_time):
                    with self._lock:
                        self._upbit_ws_fresh_skip_count += 1
                elif self._backoff_active('upbit'):
                    self._record_backoff_skip('upbit')
                else:
                    with rate_limiter.source('rest_cache'):
                        quote = self.upbit.fetch_order_book(symbol)
                    if quote:
                        legs['upbit'] = self._normalize_quote(quote, fetch_time)
                if not refresh_binance:
                    pass
                elif self._backoff_active('binance'):
                    self._record_backoff_skip('binance')
                else:
                    with rate_limiter.source('rest_cache'):
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
                if refresh_upbit:
                    self._last_exchange_refresh_at['upbit'] = fetch_time
                if refresh_binance:
                    self._last_exchange_refresh_at['binance'] = fetch_time
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

    def _pop_priority_request(self):
        with self._lock:
            if not self._recheck_queue:
                return None
            request = self._recheck_queue.popleft()
            key = (request['pair_id'], request['symbol'])
            self._recheck_enqueued.discard(key)
            self._recheck_inflight.add(key)
            return request

    def _process_priority_queue(self, fetch_time):
        while True:
            request = self._pop_priority_request()
            if not request:
                return
            symbol = request['symbol']
            pair_id = request['pair_id']
            key = (pair_id, symbol)
            started = time.time()
            source = 'rest_cache_priority'
            ok = False
            error = ''
            quote_ts = None
            try:
                legs = {}
                skip_reasons = []
                needs_binance = pair_id != 'UPBIT_BITHUMB'
                if self._upbit_ws_is_fresh(symbol, fetch_time):
                    skip_reasons.append('SKIP_UPBIT_WS_FRESH')
                    with self._lock:
                        self._upbit_ws_fresh_skip_count += 1
                elif self._backoff_active('upbit'):
                    skip_reasons.append('SKIP_BACKOFF')
                    self._record_backoff_skip('upbit')
                else:
                    with rate_limiter.source('rest_cache'):
                        quote = self.upbit.fetch_order_book(symbol)
                    if quote:
                        legs['upbit'] = self._normalize_quote(quote, fetch_time)
                if not needs_binance:
                    pass
                elif self._backoff_active('binance'):
                    skip_reasons.append('SKIP_BACKOFF')
                    self._record_backoff_skip('binance')
                else:
                    with rate_limiter.source('rest_cache'):
                        quote = self.binance.fetch_order_book(symbol)
                    if quote:
                        legs['binance'] = self._normalize_quote(quote, fetch_time)
                refreshed_at = time.time()
                elapsed_ms = (time.time() - started) * 1000
                with self._lock:
                    self._recheck_execute_count += 1
                    self._recheck_last_symbol = symbol
                    self._recheck_last_pair_id = pair_id
                    self._recheck_last_at = time.time()
                    self._priority_fetch_last_ms = elapsed_ms
                    self._priority_fetch_last_symbol = symbol
                    self._priority_fetch_total_ms += elapsed_ms
                    self._priority_fetch_samples += 1
                    self._priority_symbol_fetch_count += len(legs)
                    if legs:
                        previous = self._quotes.setdefault(symbol, {})
                        previous.update(legs)
                        if needs_binance and previous.get('upbit') and previous.get('binance'):
                            self._quotes[symbol] = self._build_symbol_quote(symbol, previous)
                            self._last_success_at = time.time()
                            self._last_error = ''
                            quote_ts = self._quotes[symbol].get('ts')
                        elif not needs_binance and previous.get('upbit'):
                            self._quotes[symbol] = previous
                            self._last_success_at = time.time()
                            self._last_error = ''
                            quote_ts = previous.get('upbit', {}).get('ts')
                        self._priority_fetch_last_error = ''
                        ok = True
                    elif 'SKIP_UPBIT_WS_FRESH' in skip_reasons:
                        ok = True
                        source = 'ws_fresh_skip'
                        quote_ts = fetch_time
                        self._priority_fetch_last_error = ''
                    else:
                        self._recheck_fail_count += 1
                        self._recheck_last_error = skip_reasons[0] if skip_reasons else 'RECHECK_NO_QUOTES'
                        self._priority_fetch_last_error = self._recheck_last_error
                        error = self._recheck_last_error
            except Exception as exc:
                refreshed_at = time.time()
                elapsed_ms = (time.time() - started) * 1000
                with self._lock:
                    self._recheck_execute_count += 1
                    self._recheck_fail_count += 1
                    self._recheck_last_symbol = symbol
                    self._recheck_last_pair_id = pair_id
                    self._recheck_last_at = time.time()
                    self._recheck_last_error = f'{type(exc).__name__}: {exc}'[:300]
                    self._priority_fetch_last_ms = elapsed_ms
                    self._priority_fetch_last_symbol = symbol
                    self._priority_fetch_total_ms += elapsed_ms
                    self._priority_fetch_samples += 1
                    self._priority_fetch_last_error = self._recheck_last_error
                    error = self._recheck_last_error
            finally:
                self._append_completed_recheck(
                    request, started, refreshed_at, elapsed_ms, source, ok, error, quote_ts
                )
                with self._lock:
                    self._recheck_inflight.discard(key)
                    self._recheck_meta.pop(key, None)

    def _run(self):
        while not self._stop.is_set():
            if self.priority_worker_enabled and self._priority_event.is_set():
                self._priority_event.clear()
                self._process_priority_queue(time.time())
                continue
            self.refresh_once()
            if self.priority_worker_enabled:
                self._priority_event.wait(self.refresh_sec)
                self._priority_event.clear()
            else:
                self._stop.wait(self.refresh_sec)

    def _backoff_active(self, exchange):
        return self.skip_on_backoff and rate_limiter.should_backoff(exchange)

    def _exchange_refresh_due(self, exchange, now):
        with self._lock:
            last = self._last_exchange_refresh_at[exchange]
        interval = self.upbit_refresh_sec if exchange == 'upbit' else self.binance_refresh_sec
        return now - last >= interval

    def _upbit_ws_is_fresh(self, symbol, now):
        if not self.skip_upbit_when_ws_ok:
            return False
        with self._lock:
            updated_at = self._upbit_ws_fresh_at.get(symbol, 0.0)
        return updated_at and (now - updated_at) * 1000 <= self.ws_fresh_threshold_ms

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
