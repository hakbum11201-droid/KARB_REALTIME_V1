"""Background Bithumb public quote cache for domestic KRW paper calculations."""
import copy
from collections import deque
import threading
import time


class BithumbQuoteCache:
    def __init__(
        self, client, enabled=True, refresh_ms=700, stale_ms=5000,
        grace_ms=3000, allow_last_good_on_stale=True, max_failures=10,
        recheck_cooldown_sec=5, recheck_max_queue_size=50,
        priority_worker_enabled=True, symbol_only_refresh=True,
        inflight_dedupe=True, priority_fetch_timeout_ms=700,
        priority_max_workers=1, completed_recheck_ttl_sec=30,
    ):
        self.client = client
        self.enabled = bool(enabled)
        self.refresh_ms = max(50, int(refresh_ms))
        self.stale_ms = max(1, int(stale_ms))
        self.grace_ms = max(0, int(grace_ms))
        self.allow_last_good_on_stale = bool(allow_last_good_on_stale)
        self.max_failures = max(1, int(max_failures))
        self.recheck_cooldown_sec = max(0.0, float(recheck_cooldown_sec))
        self.recheck_max_queue_size = max(1, int(recheck_max_queue_size))
        self.priority_worker_enabled = bool(priority_worker_enabled)
        self.symbol_only_refresh = bool(symbol_only_refresh)
        self.inflight_dedupe = bool(inflight_dedupe)
        self.priority_fetch_timeout_ms = max(1, int(priority_fetch_timeout_ms))
        self.priority_max_workers = max(1, int(priority_max_workers))
        self.completed_recheck_ttl_sec = max(1.0, float(completed_recheck_ttl_sec))
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._priority_event = threading.Event()
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
        self._priority_event.set()
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
                **self.get_recheck_status(),
            }

    def request_priority_refresh(
        self, symbol, reason=None, pair_id=None,
        original_surplus_bp=None, original_net_krw=None,
    ):
        now = time.time()
        symbol = str(symbol or '').upper()
        if not self.enabled or not symbol:
            return {'ok': False, 'queued': False, 'reason': 'DISABLED_OR_EMPTY_SYMBOL'}
        pair_id = str(pair_id or 'UPBIT_BITHUMB')
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
                return {'ok': False, 'queued': False, 'reason': 'RECHECK_COOLDOWN'}
            if len(self._recheck_queue) >= self.recheck_max_queue_size:
                return {'ok': False, 'queued': False, 'reason': 'RECHECK_QUEUE_FULL'}
            self._recheck_queue.append({
                'pair_id': pair_id, 'symbol': symbol,
                'reason': reason or '', 'requested_at': now,
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
            'pair_id': request.get('pair_id', 'UPBIT_BITHUMB'),
            'symbol': request.get('symbol', ''),
            'reason': request.get('reason', ''),
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
        self._process_priority_queue()
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

    def _pop_priority_request(self):
        with self._lock:
            if not self._recheck_queue:
                return None
            request = self._recheck_queue.popleft()
            key = (request['pair_id'], request['symbol'])
            self._recheck_enqueued.discard(key)
            self._recheck_inflight.add(key)
            return request

    def _process_priority_queue(self):
        while True:
            request = self._pop_priority_request()
            if not request:
                return
            now = time.time()
            started = time.time()
            key = (request['pair_id'], request['symbol'])
            source = 'bithumb_symbol_only'
            ok = False
            error = ''
            quote_ts = None
            try:
                if self.symbol_only_refresh and hasattr(self.client, 'fetch_order_book'):
                    quote = self.client.fetch_order_book(request['symbol'])
                    fetched = {request['symbol']: quote}
                    symbol_fetch = True
                else:
                    with self._lock:
                        symbols = list(self._symbols) or [request['symbol']]
                    fetched = self.client.fetch_all_order_books(symbols)
                    symbol_fetch = False
                    source = 'bithumb_full_refresh_fallback'
                quote = (fetched or {}).get(request['symbol'], {})
                refreshed_at = time.time()
                elapsed_ms = (time.time() - started) * 1000
                with self._lock:
                    self._recheck_execute_count += 1
                    self._recheck_last_symbol = request['symbol']
                    self._recheck_last_pair_id = request['pair_id']
                    self._recheck_last_at = time.time()
                    self._priority_fetch_last_ms = elapsed_ms
                    self._priority_fetch_last_symbol = request['symbol']
                    self._priority_fetch_total_ms += elapsed_ms
                    self._priority_fetch_samples += 1
                    if symbol_fetch:
                        self._priority_symbol_fetch_count += 1
                    else:
                        self._priority_full_refresh_fallback_count += 1
                    if isinstance(quote, dict) and quote.get('ok'):
                        normalized_quote = self._normalize_quote_ts(quote, now)
                        self._quotes[request['symbol']] = normalized_quote
                        self._last_success_at = time.time()
                        self._last_error = ''
                        self._priority_fetch_last_error = ''
                        ok = True
                        quote_ts = normalized_quote.get('ts')
                    else:
                        self._recheck_fail_count += 1
                        self._recheck_last_error = self._failure_reason(fetched)
                        self._priority_fetch_last_error = self._recheck_last_error
                        error = self._recheck_last_error
            except Exception as exc:
                refreshed_at = time.time()
                elapsed_ms = (time.time() - started) * 1000
                with self._lock:
                    self._recheck_execute_count += 1
                    self._recheck_fail_count += 1
                    self._recheck_last_symbol = request['symbol']
                    self._recheck_last_pair_id = request['pair_id']
                    self._recheck_last_at = time.time()
                    self._recheck_last_error = f'{type(exc).__name__}: {exc}'
                    self._priority_fetch_last_ms = elapsed_ms
                    self._priority_fetch_last_symbol = request['symbol']
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
            if self.priority_worker_enabled and self._priority_event.is_set():
                self._priority_event.clear()
                self._process_priority_queue()
                continue
            self.refresh_once()
            if self.priority_worker_enabled:
                self._priority_event.wait(self.refresh_ms / 1000)
                self._priority_event.clear()
            else:
                self._stop_event.wait(self.refresh_ms / 1000)

    @staticmethod
    def _failure_reason(fetched):
        blockers = []
        for quote in (fetched or {}).values():
            blockers.extend(quote.get('blockers', []))
        return blockers[0] if blockers else 'BITHUMB_QUOTE_REFRESH_FAILED'
