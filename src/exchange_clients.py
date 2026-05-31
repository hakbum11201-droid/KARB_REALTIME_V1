"""Minimal private Spot REST clients for guarded tiny-live execution only."""
import base64
import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal, ROUND_DOWN
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import cfg
from secrets_manager import get_binance_credentials, get_key_status, get_upbit_credentials


def _json_request(url: str, method='GET', headers=None, body=None, timeout=8):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = Request(url, data=data, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode('utf-8'))
        except Exception:
            detail = {'status': exc.code}
        raise RuntimeError(f'HTTP_{exc.code}: {detail}') from None
    except URLError as exc:
        raise RuntimeError(f'NETWORK_ERROR: {exc.reason}') from None


def _assert_tiny_live_order_allowed():
    blockers = []
    if cfg.mode != 'tiny_live':
        blockers.append('MODE_GUARD')
    if not cfg.enable_live_trading:
        blockers.append('ENABLE_LIVE_TRADING_FALSE')
    if not cfg.tiny_live_enabled:
        blockers.append('TINY_LIVE_DISABLED')
    if not cfg.live_order_enabled:
        blockers.append('LIVE_ORDER_ENABLED_FALSE')
    if cfg.withdrawals_enabled:
        blockers.append('WITHDRAWALS_MUST_REMAIN_DISABLED')
    if cfg.futures_hedge_enabled:
        blockers.append('FUTURES_HEDGE_MUST_REMAIN_DISABLED')
    if blockers:
        raise RuntimeError('ORDER_BLOCKED: ' + ', '.join(blockers))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


class UpbitPrivateClient:
    BASE_URL = 'https://api.upbit.com'

    def _token(self, params=None):
        access_key, secret_key = get_upbit_credentials()
        payload = {'access_key': access_key, 'nonce': str(uuid.uuid4())}
        if params:
            query = urlencode(params)
            payload['query_hash'] = hashlib.sha512(query.encode('utf-8')).hexdigest()
            payload['query_hash_alg'] = 'SHA512'
        header = {'alg': 'HS512', 'typ': 'JWT'}
        segments = [
            _b64url(json.dumps(header, separators=(',', ':')).encode('utf-8')),
            _b64url(json.dumps(payload, separators=(',', ':')).encode('utf-8')),
        ]
        signing_input = '.'.join(segments).encode('ascii')
        signature = hmac.new(secret_key.encode('utf-8'), signing_input, hashlib.sha512).digest()
        return '.'.join([*segments, _b64url(signature)])

    def _request(self, path: str, method='GET', params=None):
        params = params or {}
        headers = {'Authorization': f'Bearer {self._token(params)}'}
        url = f'{self.BASE_URL}{path}'
        if method == 'GET' and params:
            url += '?' + urlencode(params)
        if method == 'POST':
            headers['Content-Type'] = 'application/json'
        return _json_request(url, method=method, headers=headers, body=params if method == 'POST' else None)

    def get_balances(self) -> dict:
        status = get_key_status()
        if status['UPBIT_ACCESS_KEY'] != 'Set' or status['UPBIT_SECRET_KEY'] != 'Set':
            return {'ok': False, 'balances': {}, 'blockers': ['UPBIT_KEY_MISSING'], 'warnings': []}
        try:
            rows = self._request('/v1/accounts')
            balances = {row['currency']: float(row.get('balance', 0) or 0) for row in rows}
            return {'ok': True, 'balances': balances, 'blockers': [], 'warnings': []}
        except Exception as exc:
            return {'ok': False, 'balances': {}, 'blockers': ['UPBIT_BALANCE_LOOKUP_FAILED'],
                    'warnings': [str(exc)]}

    def place_market_buy_krw(self, symbol: str, krw_amount: float):
        _assert_tiny_live_order_allowed()
        return self._request('/v1/orders', 'POST', {
            'market': f'KRW-{symbol}', 'side': 'bid', 'price': str(int(krw_amount)), 'ord_type': 'price',
        })

    def place_market_sell_qty(self, symbol: str, qty: float):
        _assert_tiny_live_order_allowed()
        return self._request('/v1/orders', 'POST', {
            'market': f'KRW-{symbol}', 'side': 'ask', 'volume': str(qty), 'ord_type': 'market',
        })

    def get_order(self, order_id: str):
        return self._request('/v1/order', params={'uuid': order_id})


class BinanceSpotPrivateClient:
    BASE_URL = 'https://api.binance.com'

    def _signed_request(self, path: str, method='GET', params=None):
        api_key, api_secret = get_binance_credentials()
        values = dict(params or {})
        values['timestamp'] = int(time.time() * 1000)
        query = urlencode(values)
        signature = hmac.new(api_secret.encode('utf-8'), query.encode('utf-8'), hashlib.sha256).hexdigest()
        url = f'{self.BASE_URL}{path}?{query}&signature={signature}'
        return _json_request(url, method=method, headers={'X-MBX-APIKEY': api_key})

    def get_balances(self) -> dict:
        status = get_key_status()
        if status['BINANCE_API_KEY'] != 'Set' or status['BINANCE_API_SECRET'] != 'Set':
            return {'ok': False, 'balances': {}, 'blockers': ['BINANCE_KEY_MISSING'], 'warnings': []}
        try:
            account = self._signed_request('/api/v3/account')
            balances = {row['asset']: float(row.get('free', 0) or 0) for row in account.get('balances', [])}
            return {'ok': True, 'balances': balances, 'blockers': [], 'warnings': []}
        except Exception as exc:
            return {'ok': False, 'balances': {}, 'blockers': ['BINANCE_SPOT_BALANCE_LOOKUP_FAILED'],
                    'warnings': [str(exc)]}

    def get_account_info(self) -> dict:
        result = self.get_balances()
        return {**result, 'account_type': 'SPOT'}

    def get_symbol_filters(self, symbol: str) -> dict:
        try:
            info = _json_request(f'{self.BASE_URL}/api/v3/exchangeInfo?symbol={symbol}USDT')
            rows = info.get('symbols', [])
            if not rows:
                raise RuntimeError('symbol missing')
            filters = {row['filterType']: row for row in rows[0].get('filters', [])}
            lot = filters.get('LOT_SIZE', {})
            notional = filters.get('NOTIONAL') or filters.get('MIN_NOTIONAL', {})
            return {
                'ok': True,
                'min_qty': float(lot.get('minQty', 0) or 0),
                'step_size': float(lot.get('stepSize', 0) or 0),
                'min_notional': float(notional.get('minNotional', 0) or 0),
                'blockers': [],
            }
        except Exception as exc:
            return {'ok': False, 'blockers': ['BINANCE_FILTER_UNAVAILABLE'], 'warnings': [str(exc)]}

    @staticmethod
    def round_down_qty(qty: float, step_size: float) -> float:
        if step_size <= 0:
            return qty
        value, step = Decimal(str(qty)), Decimal(str(step_size))
        return float((value / step).to_integral_value(rounding=ROUND_DOWN) * step)

    def normalize_qty(self, symbol: str, qty: float) -> dict:
        filters = self.get_symbol_filters(symbol)
        if not filters.get('ok'):
            return filters
        return {**filters, 'qty': self.round_down_qty(qty, float(filters.get('step_size', 0) or 0))}

    def place_market_buy_quote(self, symbol: str, usdt_amount: float):
        _assert_tiny_live_order_allowed()
        return self._signed_request('/api/v3/order', 'POST', {
            'symbol': f'{symbol}USDT', 'side': 'BUY', 'type': 'MARKET',
            'quoteOrderQty': str(usdt_amount),
        })

    def place_market_sell_qty(self, symbol: str, qty: float):
        _assert_tiny_live_order_allowed()
        return self._signed_request('/api/v3/order', 'POST', {
            'symbol': f'{symbol}USDT', 'side': 'SELL', 'type': 'MARKET', 'quantity': str(qty),
        })

    def get_order(self, symbol: str, order_id: str):
        return self._signed_request('/api/v3/order', params={'symbol': f'{symbol}USDT', 'orderId': order_id})
