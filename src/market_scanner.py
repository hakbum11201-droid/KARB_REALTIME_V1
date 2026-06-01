"""Dynamic Top-N scanner for common Upbit, Bithumb, and Binance Spot markets."""
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
            return {
                'active_symbols': symbols[:top_n],
                'standby_symbols': symbols[top_n:],
                'common_upbit_binance': common_upbit_binance,
                'common_upbit_bithumb': common_upbit_bithumb,
                'updated_at': time.time(),
                'source': 'dynamic',
                'blockers': [],
            }
        except Exception as exc:
            return self.fallback(f'{type(exc).__name__}: {exc}')

    def fallback(self, reason='SCANNER_FALLBACK'):
        return {
            'active_symbols': list(self.config.symbols),
            'standby_symbols': [],
            'common_upbit_binance': [],
            'common_upbit_bithumb': [],
            'updated_at': time.time(),
            'source': 'fallback',
            'blockers': [reason],
        }

    def _excluded(self, symbol, blacklist):
        symbol = symbol.upper()
        if symbol in blacklist:
            return True
        if self.config.exclude_stablecoin_symbols and symbol in STABLE_OR_ODD_SYMBOLS:
            return True
        return symbol.endswith(('UP', 'DOWN', 'BULL', 'BEAR'))

    def _get_json(self, url, params=None):
        response = self._session.get(url, params=params, timeout=4)
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
