"""Reconnectable public WebSocket top-of-book collector with REST fallback."""
import json
import threading
import time

import websocket

from orderbook_cache import OrderbookCache


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

            app = websocket.WebSocketApp(
                url, on_open=handle_open, on_message=on_message, on_close=handle_close)
            try:
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                pass
            with self._lock:
                self._connected[exchange] = False
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 30)

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
                    'ts': now,
                })
            except Exception:
                pass

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
                    'latency_ms': 0, 'ts': time.time(),
                })
            except Exception:
                pass

        self._run_forever('binance', self.BINANCE_URL.format(streams=streams), lambda _app: None, on_message)
