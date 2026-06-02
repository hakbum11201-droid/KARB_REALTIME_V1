"""Dynamic Top-N scanner for common Upbit, Bithumb, and Binance Spot markets."""
import json
import os
import queue
import tempfile
import threading
import time

import requests

from config import cfg


STABLE_OR_ODD_SYMBOLS = {
    'BUSD', 'DAI', 'FDUSD', 'PAX', 'TUSD', 'USDC', 'USDD', 'USDP', 'USDS', 'USDT',
    'KRW', 'EUR', 'JPY', 'GBP', 'BRL', 'AUD', 'TRY', 'BIDR', 'IDRT',
}


class MarketScanner:
    def __init__(self, config=cfg):
        self.config = config
        self._session = requests.Session()
        self._lock = threading.RLock()
        self._refresh_count = 0
        self._fail_count = 0
        self._last_refresh_status = 'INIT'
        self._last_error = ''

    def scan(self):
        try:
            upbit = self._upbit_krw_markets()
            bithumb = self._bithumb_krw_markets()
            binance = self._binance_usdt_spot_markets()
            common_upbit_binance = sorted(set(upbit) & set(binance))
            common_upbit_bithumb = sorted(set(upbit) & set(bithumb))
            common_all = set(common_upbit_binance) & set(common_upbit_bithumb)
            blacklist = {str(item).upper() for item in self.config.symbol_blacklist}
            min_volume = float(self.config.min_24h_quote_volume_krw)
            ranked = []
            for symbol in common_all:
                if self._excluded(symbol, blacklist):
                    continue
                krw_volume = min(upbit[symbol], bithumb[symbol])
                if krw_volume < min_volume or binance[symbol] <= 0:
                    continue
                ranked.append((krw_volume, symbol))
            ranked.sort(reverse=True)
            symbols = [symbol for _, symbol in ranked]
            if not symbols:
                raise RuntimeError('NO_DYNAMIC_SYMBOLS')
            top_n = int(self.config.dynamic_symbol_top_n)
            snapshot = {
                'active_symbols': symbols[:top_n],
                'standby_symbols': symbols[top_n:],
                'common_upbit_binance': common_upbit_binance,
                'common_upbit_bithumb': common_upbit_bithumb,
                'updated_at': time.time(),
                'source': 'dynamic',
                'blockers': [],
            }
            return self._record_result(snapshot)
        except Exception as exc:
            return self._record_result(self.fallback(f'{type(exc).__name__}: {exc}'))

    def fallback(self, reason='SCANNER_FALLBACK'):
        return {
            'active_symbols': list(self.config.symbols),
            'standby_symbols': [],
            'common_upbit_binance': list(self.config.symbols),
            'common_upbit_bithumb': list(self.config.symbols),
            'updated_at': time.time(),
            'source': 'fallback',
            'blockers': [reason],
        }

    def load_cached_snapshot(self, path, max_age_sec):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                snapshot = json.load(f)
            updated_at = float(snapshot.get('updated_at', 0) or 0)
            age_sec = max(0.0, time.time() - updated_at) if updated_at else None
            if (
                not snapshot.get('active_symbols')
                or not updated_at
                or age_sec is None
                or age_sec > float(max_age_sec)
                or snapshot.get('source') not in ('dynamic', 'cache')
            ):
                return None
            snapshot['source'] = 'cache'
            snapshot['scanner_cache_used'] = True
            snapshot['scanner_cache_age_sec'] = round(age_sec, 2)
            snapshot['scanner_startup_mode'] = self.config.market_scanner_startup_mode
            snapshot.setdefault('blockers', [])
            return snapshot
        except Exception:
            return None

    def save_cached_snapshot(self, path, snapshot):
        if not snapshot or snapshot.get('source') != 'dynamic':
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(path), prefix=os.path.basename(path) + '.', suffix='.tmp'
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, path)
            return True
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def get_startup_snapshot(self, cache_path, fallback_symbols, max_age_sec):
        if self.config.market_scanner_cache_enabled:
            cached = self.load_cached_snapshot(cache_path, max_age_sec)
            if cached:
                return self._with_status(cached)
        snapshot = self.fallback('MARKET_SCANNER_CACHE_MISS')
        snapshot['active_symbols'] = list(fallback_symbols)
        snapshot['common_upbit_binance'] = list(fallback_symbols)
        snapshot['common_upbit_bithumb'] = list(fallback_symbols)
        snapshot['scanner_cache_used'] = False
        snapshot['scanner_cache_age_sec'] = None
        snapshot['scanner_startup_mode'] = self.config.market_scanner_startup_mode
        return self._with_status(snapshot)

    def scan_with_timeout(self, timeout_sec=None):
        timeout_sec = float(timeout_sec or self.config.market_scanner_timeout_sec)
        results = queue.Queue(maxsize=1)

        def run_scan():
            try:
                results.put(self.scan(), block=False)
            except Exception:
                pass

        thread = threading.Thread(target=run_scan, name='market-scanner-timeout', daemon=True)
        thread.start()
        thread.join(timeout=max(0.1, timeout_sec))
        if thread.is_alive():
            return self._record_result(self.fallback('MARKET_SCANNER_TIMEOUT'))
        try:
            return results.get_nowait()
        except queue.Empty:
            return self._record_result(self.fallback('MARKET_SCANNER_NO_RESULT'))

    def _record_result(self, snapshot):
        failed = snapshot.get('source') == 'fallback'
        with self._lock:
            self._refresh_count += 1
            if failed:
                self._fail_count += 1
            self._last_refresh_status = 'FAILED' if failed else 'OK'
            self._last_error = snapshot.get('blockers', [''])[0] if failed else ''
        return self._with_status(snapshot)

    def _with_status(self, snapshot):
        with self._lock:
            status = {
                'scanner_last_refresh_status': self._last_refresh_status,
                'scanner_last_error': self._last_error,
                'scanner_refresh_count': self._refresh_count,
                'scanner_fail_count': self._fail_count,
            }
        return {
            **snapshot,
            'scanner_cache_used': bool(snapshot.get('scanner_cache_used', False)),
            'scanner_cache_age_sec': snapshot.get('scanner_cache_age_sec'),
            'scanner_startup_mode': self.config.market_scanner_startup_mode,
            **status,
        }

    def _excluded(self, symbol, blacklist):
        symbol = symbol.upper()
        if symbol in blacklist:
            return True
        if self.config.exclude_stablecoin_symbols and symbol in STABLE_OR_ODD_SYMBOLS:
            return True
        return symbol.endswith(('UP', 'DOWN', 'BULL', 'BEAR'))

    def _get_json(self, url, params=None):
        response = self._session.get(
            url, params=params, timeout=float(self.config.market_scanner_timeout_sec)
        )
        response.raise_for_status()
        return response.json()

    def _upbit_krw_markets(self):
        markets = self._get_json('https://api.upbit.com/v1/market/all')
        symbols = [row['market'][4:] for row in markets if str(row.get('market', '')).startswith('KRW-')]
        tickers = self._ticker_chunks('https://api.upbit.com/v1/ticker', symbols)
        return {
            row['market'][4:]: float(row.get('acc_trade_price_24h', 0) or 0)
            for row in tickers if str(row.get('market', '')).startswith('KRW-')
        }

    def _bithumb_krw_markets(self):
        markets = self._get_json('https://api.bithumb.com/v1/market/all')
        symbols = [row['market'][4:] for row in markets if str(row.get('market', '')).startswith('KRW-')]
        tickers = self._ticker_chunks('https://api.bithumb.com/v1/ticker', symbols)
        return {
            row['market'][4:]: float(row.get('acc_trade_price_24h', 0) or 0)
            for row in tickers if str(row.get('market', '')).startswith('KRW-')
        }

    def _ticker_chunks(self, url, symbols):
        rows = []
        for idx in range(0, len(symbols), 100):
            markets = ','.join(f'KRW-{symbol}' for symbol in symbols[idx:idx + 100])
            rows.extend(self._get_json(url, {'markets': markets}))
        return rows

    def _binance_usdt_spot_markets(self):
        info = self._get_json('https://api.binance.com/api/v3/exchangeInfo')
        tickers = self._get_json('https://api.binance.com/api/v3/ticker/24hr')
        volumes = {row.get('symbol'): float(row.get('quoteVolume', 0) or 0) for row in tickers}
        return {
            row['baseAsset']: volumes.get(row['symbol'], 0.0)
            for row in info.get('symbols', [])
            if row.get('quoteAsset') == 'USDT'
            and row.get('status') == 'TRADING'
            and row.get('isSpotTradingAllowed', True)
        }
