"""Reconnectable public WebSocket top-of-book collector with REST fallback."""
import json
import threading
import time

import websocket

from orderbook_cache import OrderbookCache
from config import cfg
from rate_limiter import rate_limiter


class WebSocketMarketData:
    UPBIT_URL = 'wss://api.upbit.com/websocket/v1'
    BINANCE_URL = 'wss://stream.binance.com:9443/stream?streams={streams}'

    def __init__(self, symbols, stale_quote_ms=1500, rest_fallback_enabled=True):
        self.symbols = list(symbols)
        self.cache = OrderbookCache(self.symbols, stale_quote_ms)
        self.rest_fallback_enabled = rest_fallback_enabled
        self._stop = threading.Event()
        self._threads = []
        self._connected = {'upbit': False, 'binance': False}
        self._lock = threading.Lock()
        self._last_rest_fallback_at = 0.0
        self._rest_fallback_skip_count = 0
        self._errors = {
            'upbit': {'last_error': '', 'last_error_at': 0.0, 'reconnect_count': 0, 'error_count': 0, 'last_reconnect_at': 0.0},
            'binance': {'last_error': '', 'last_error_at': 0.0, 'reconnect_count': 0, 'error_count': 0, 'last_reconnect_at': 0.0},
        }

    def start(self):
        if self._threads:
            return
        self._threads = [
            threading.Thread(target=self._run_upbit, name='upbit-orderbook-ws', daemon=True),
            threading.Thread(target=self._run_binance, name='binance-bookticker-ws', daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self):
        self._stop.set()

    def fetch_all(self, rest_quote_engine=None) -> dict:
        quotes = self.cache.snapshot(require_fresh=True)
        if len(quotes) == len(self.symbols):
            return quotes
        if not self.rest_fallback_enabled or rest_quote_engine is None:
            return quotes
        now = time.time()
        min_interval_sec = float(cfg.rest_fallback_min_interval_ms) / 1000
        if now - self._last_rest_fallback_at < min_interval_sec:
            self._rest_fallback_skip_count += 1
            return quotes
        if rate_limiter.should_backoff('upbit') or rate_limiter.should_backoff('binance'):
            self._rest_fallback_skip_count += 1
            return quotes
        self._last_rest_fallback_at = now
        fallback = rest_quote_engine.fetch_all()
        self.cache.record_rest_fallback(1)
        for symbol, quote in fallback.items():
            self.cache.update('upbit', symbol, quote['upbit'], source='rest')
            self.cache.update('binance', symbol, quote['binance'], source='rest')
        return self.cache.snapshot(require_fresh=True)

    def metrics(self):
        metrics = self.cache.metrics()
        with self._lock:
            connected = dict(self._connected)
        metrics['ws_connected'] = all(connected.values())
        metrics['ws_connections'] = connected
        with self._lock:
            errors = {exchange: dict(status) for exchange, status in self._errors.items()}
        metrics.update({
            'upbit_connected': connected['upbit'],
            'binance_connected': connected['binance'],
            'last_error': self._latest_error(errors),
            'last_error_at': max(status['last_error_at'] for status in errors.values()),
            'reconnect_count': sum(status['reconnect_count'] for status in errors.values()),
            'error_count': sum(status['error_count'] for status in errors.values()),
            'last_reconnect_at': max(status['last_reconnect_at'] for status in errors.values()),
            'upbit_reconnect_count': errors['upbit']['reconnect_count'],
            'binance_reconnect_count': errors['binance']['reconnect_count'],
            'upbit_last_error': errors['upbit']['last_error'],
            'binance_last_error': errors['binance']['last_error'],
        })
        rate_status = rate_limiter.get_status()
        metrics['rest_fallback_skip_count'] = self._rest_fallback_skip_count
        metrics['rate_limit_throttle_count'] = rate_status['total_throttle_count']
        metrics['api_429_count'] = rate_status['total_api_429_count']
        metrics['rate_limit_status'] = rate_status
        return metrics

    def _run_forever(self, exchange, url, on_open, on_message):
        delay = 1
        while not self._stop.is_set():
            def handle_open(app):
                with self._lock:
                    self._connected[exchange] = True
                on_open(app)

            def handle_close(_app, _status_code, _message):
                with self._lock:
                    self._connected[exchange] = False

            def handle_error(_app, error):
                self._record_error(exchange, error)

            app = websocket.WebSocketApp(
                url, on_open=handle_open, on_message=on_message,
                on_close=handle_close, on_error=handle_error)
            try:
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                self._record_error(exchange, exc)
            with self._lock:
                self._connected[exchange] = False
            if self._stop.wait(delay):
                break
            self._record_reconnect(exchange)
            delay = min(delay * 2, 30)

    def _record_error(self, exchange, error):
        message = f'{type(error).__name__}: {error}'[:300]
        with self._lock:
            status = self._errors[exchange]
            status['last_error'] = message
            status['last_error_at'] = time.time()
            status['error_count'] += 1

    def _record_reconnect(self, exchange):
        with self._lock:
            status = self._errors[exchange]
            status['reconnect_count'] += 1
            status['last_reconnect_at'] = time.time()

    @staticmethod
    def _latest_error(errors):
        latest = max(errors.values(), key=lambda status: status['last_error_at'])
        return latest['last_error']

    def _run_upbit(self):
        codes = [f'KRW-{symbol}' for symbol in self.symbols]

        def on_open(app):
            app.send(json.dumps([
                {'ticket': 'karb-orderbook'},
                {'type': 'orderbook', 'codes': codes, 'is_only_realtime': True},
                {'format': 'DEFAULT'},
            ]))

        def on_message(_app, raw):
            try:
                data = json.loads(raw)
                unit = data['orderbook_units'][0]
                symbol = data['code'].split('-', 1)[1]
                event_ts = float(data.get('timestamp', 0) or 0) / 1000
                now = time.time()
                self.cache.update('upbit', symbol, {
                    'bid': unit['bid_price'], 'ask': unit['ask_price'],
                    'bid_size': unit['bid_size'], 'ask_size': unit['ask_size'],
                    'latency_ms': max(0, (now - event_ts) * 1000) if event_ts else 0,
                    'event_ts': event_ts or now,
                })
            except Exception as exc:
                self._record_error('upbit', exc)

        self._run_forever('upbit', self.UPBIT_URL, on_open, on_message)

    def _run_binance(self):
        streams = '/'.join(f'{symbol.lower()}usdt@bookTicker' for symbol in self.symbols)

        def on_message(_app, raw):
            try:
                data = json.loads(raw).get('data', {})
                symbol = data['s'][:-4]
                self.cache.update('binance', symbol, {
                    'bid': data['b'], 'ask': data['a'],
                    'bid_size': data['B'], 'ask_size': data['A'],
                    'latency_ms': 0, 'event_ts': data.get('E') or time.time(),
                })
            except Exception as exc:
                self._record_error('binance', exc)

        self._run_forever('binance', self.BINANCE_URL.format(streams=streams), lambda _app: None, on_message)
