"""Shared token-bucket guards for public REST market-data calls."""
from contextlib import contextmanager
import threading
import time

from config import cfg


class TokenBucketRateLimiter:
    EXCHANGES = ('upbit', 'bithumb', 'binance')
    SOURCES = ('rest_cache', 'scanner', 'fx', 'other')

    def __init__(self, config=None):
        self._cfg = config or cfg
        self._enabled = bool(self._cfg.rate_limiter_enabled)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._states = {}
        now = time.time()
        for exchange in self.EXCHANGES:
            allowed = float(getattr(self._cfg, f'rate_limit_{exchange}_per_sec'))
            burst = float(self._cfg.rate_limit_burst)
            self._states[exchange] = {
                'allowed_per_sec': allowed,
                'burst': burst,
                'tokens': burst,
                'last_refill': now,
                'throttle_count': 0,
                'api_429_count': 0,
                'backoff_until': 0.0,
                'last_error': '',
                'rest_call_counts': {source: 0 for source in self.SOURCES},
                'api_429_counts': {source: 0 for source in self.SOURCES},
            }

    def acquire(self, exchange, weight=1) -> bool:
        """Return false during backoff; otherwise wait briefly for one token."""
        if not self._enabled:
            return True
        exchange = self._normalize_exchange(exchange)
        weight = max(0.0, float(weight))
        wait_sec = 0.0
        with self._lock:
            state = self._states[exchange]
            now = time.time()
            self._refill(state, now)
            if now < state['backoff_until']:
                state['throttle_count'] += 1
                state['last_error'] = 'HTTP_429_BACKOFF'
                return False
            if state['tokens'] >= weight:
                state['tokens'] -= weight
                state['rest_call_counts'][self._current_source()] += 1
                return True
            state['throttle_count'] += 1
            state['last_error'] = 'RATE_LIMIT_THROTTLED'
            wait_sec = min(max((weight - state['tokens']) / state['allowed_per_sec'], 0.01), 0.25)
        time.sleep(wait_sec)
        with self._lock:
            state = self._states[exchange]
            now = time.time()
            self._refill(state, now)
            if now < state['backoff_until'] or state['tokens'] < weight:
                return False
            state['tokens'] -= weight
            state['rest_call_counts'][self._current_source()] += 1
            return True

    def record_429(self, exchange, source=None) -> None:
        exchange = self._normalize_exchange(exchange)
        source = self._normalize_source(source or self._current_source())
        with self._lock:
            state = self._states[exchange]
            state['api_429_count'] += 1
            state['api_429_counts'][source] += 1
            state['backoff_until'] = max(
                state['backoff_until'],
                time.time() + float(
                    self._cfg.upbit_429_backoff_sec
                    if exchange == 'upbit' else self._cfg.rate_limit_429_backoff_sec
                ),
            )
            state['last_error'] = 'HTTP_429'

    def should_backoff(self, exchange) -> bool:
        exchange = self._normalize_exchange(exchange)
        with self._lock:
            return time.time() < self._states[exchange]['backoff_until']

    @contextmanager
    def source(self, source):
        previous = getattr(self._local, 'source', 'other')
        self._local.source = self._normalize_source(source)
        try:
            yield
        finally:
            self._local.source = previous

    def get_status(self) -> dict:
        with self._lock:
            now = time.time()
            exchanges = {}
            for exchange, state in self._states.items():
                self._refill(state, now)
                exchanges[exchange] = {
                    **state,
                    'rest_call_counts': dict(state['rest_call_counts']),
                    'api_429_counts': dict(state['api_429_counts']),
                    'tokens': round(state['tokens'], 3),
                    'backoff_active': now < state['backoff_until'],
                }
        return {
            'enabled': self._enabled,
            'exchanges': exchanges,
            'total_throttle_count': sum(item['throttle_count'] for item in exchanges.values()),
            'total_api_429_count': sum(item['api_429_count'] for item in exchanges.values()),
            'backoff_active': any(item['backoff_active'] for item in exchanges.values()),
        }

    def _normalize_exchange(self, exchange) -> str:
        exchange = str(exchange).lower()
        if exchange not in self._states:
            raise ValueError(f'Unsupported exchange: {exchange}')
        return exchange

    def _normalize_source(self, source) -> str:
        source = str(source or 'other').lower()
        return source if source in self.SOURCES else 'other'

    def _current_source(self) -> str:
        return self._normalize_source(getattr(self._local, 'source', 'other'))

    @staticmethod
    def _refill(state, now) -> None:
        elapsed = max(0.0, now - state['last_refill'])
        state['tokens'] = min(state['burst'], state['tokens'] + elapsed * state['allowed_per_sec'])
        state['last_refill'] = now


rate_limiter = TokenBucketRateLimiter()
