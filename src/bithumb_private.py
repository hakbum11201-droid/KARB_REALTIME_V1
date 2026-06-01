"""Guarded Bithumb KRW Spot private client. Deposit and withdrawal APIs are absent."""
import hashlib
import time
import uuid
from urllib.parse import urlencode

import jwt

from config import cfg
from exchange_clients import _json_request
from secrets_manager import get_bithumb_credentials, get_key_status


def _assert_bithumb_live_order_allowed(pair_id='UPBIT_BITHUMB'):
    blockers = []
    if pair_id != 'UPBIT_BITHUMB':
        blockers.append('PAIR_DISABLED')
    if cfg.mode not in ('tiny_live', 'live'):
        blockers.append('MODE_GUARD')
    if not cfg.enable_live_trading:
        blockers.append('ENABLE_LIVE_TRADING_FALSE')
    if not cfg.live_order_enabled:
        blockers.append('LIVE_ORDER_ENABLED_FALSE')
    if not cfg.bithumb_private_enabled:
        blockers.append('BITHUMB_PRIVATE_DISABLED')
    if not cfg.upbit_bithumb_live_enabled:
        blockers.append('UPBIT_BITHUMB_LIVE_DISABLED')
    if cfg.withdrawals_enabled:
        blockers.append('WITHDRAWALS_MUST_REMAIN_DISABLED')
    if cfg.futures_hedge_enabled:
        blockers.append('FUTURES_HEDGE_MUST_REMAIN_DISABLED')
    if blockers:
        raise RuntimeError('MODE_GUARD: ' + ', '.join(blockers))


class BithumbPrivateClient:
    BASE_URL = 'https://api.bithumb.com'

    @staticmethod
    def market(symbol: str) -> str:
        return f'KRW-{symbol}'

    def _token(self, params=None) -> str:
        access_key, secret_key = get_bithumb_credentials()
        payload = {
            'access_key': access_key,
            'nonce': str(uuid.uuid4()),
            'timestamp': round(time.time() * 1000),
        }
        if params:
            query = urlencode(params)
            payload['query_hash'] = hashlib.sha512(query.encode('utf-8')).hexdigest()
            payload['query_hash_alg'] = 'SHA512'
        return jwt.encode(payload, secret_key, algorithm='HS256')

    def _request(self, path: str, method='GET', params=None):
        status = get_key_status()
        if status['BITHUMB_ACCESS_KEY'] != 'Set' or status['BITHUMB_SECRET_KEY'] != 'Set':
            raise RuntimeError('BITHUMB_KEY_MISSING')
        params = params or {}
        headers = {'Authorization': f'Bearer {self._token(params)}'}
        url = f'{self.BASE_URL}{path}'
        if method == 'GET' and params:
            url += '?' + urlencode(params)
        if method == 'POST':
            headers['Content-Type'] = 'application/json; charset=utf-8'
        return _json_request(url, method=method, headers=headers, body=params if method == 'POST' else None)

    def get_balances(self) -> dict:
        status = get_key_status()
        if status['BITHUMB_ACCESS_KEY'] != 'Set' or status['BITHUMB_SECRET_KEY'] != 'Set':
            return {'ok': False, 'balances': {}, 'blockers': ['BITHUMB_KEY_MISSING'], 'warnings': []}
        try:
            rows = self._request('/v1/accounts')
            balances = {row['currency']: float(row.get('balance', 0) or 0) for row in rows}
            return {'ok': True, 'balances': balances, 'blockers': [], 'warnings': []}
        except Exception as exc:
            return {'ok': False, 'balances': {}, 'blockers': ['BITHUMB_BALANCE_LOOKUP_FAILED'],
                    'warnings': [type(exc).__name__]}

    def get_order_chance(self, market: str) -> dict:
        return self._request('/v1/orders/chance', params={'market': market})

    def place_market_buy_krw(self, symbol: str, krw_amount: float):
        _assert_bithumb_live_order_allowed()
        if float(krw_amount) < cfg.bithumb_min_order_krw:
            raise ValueError('BITHUMB_MIN_ORDER_KRW')
        return self._request('/v1/orders', 'POST', {
            'market': self.market(symbol), 'side': 'bid',
            'price': str(int(krw_amount)), 'ord_type': 'price',
        })

    def place_market_sell_qty(self, symbol: str, qty: float):
        _assert_bithumb_live_order_allowed()
        if float(qty) <= 0:
            raise ValueError('BITHUMB_INVALID_QTY')
        return self._request('/v1/orders', 'POST', {
            'market': self.market(symbol), 'side': 'ask',
            'volume': str(qty), 'ord_type': 'market',
        })

    def get_order(self, order_uuid: str):
        return self._request('/v1/order', params={'uuid': order_uuid})

    def wait_order_filled(self, order_uuid: str, ttl_sec=None) -> dict:
        ttl_sec = float(cfg.order_ttl_sec if ttl_sec is None else ttl_sec)
        deadline = time.time() + ttl_sec
        last, fill_ratio = {}, 0.0
        while True:
            last = self.get_order(order_uuid)
            volume = float(last.get('volume', 0) or 0)
            filled_qty = float(last.get('executed_volume', 0) or 0)
            fill_ratio = min(1.0, filled_qty / volume) if volume > 0 else (
                1.0 if last.get('state') == 'done' else 0.0
            )
            trades = last.get('trades') or []
            executed_funds = float(last.get('executed_funds', 0) or 0)
            if not executed_funds:
                executed_funds = sum(float(row.get('funds', 0) or 0) for row in trades)
            avg_price = executed_funds / filled_qty if filled_qty else 0
            parsed = {
                **last, 'filled_qty': filled_qty, 'avg_price': avg_price,
                'paid_fee': float(last.get('paid_fee', 0) or 0), 'state': last.get('state', ''),
            }
            if last.get('state') == 'done' or fill_ratio >= cfg.min_fill_ratio:
                return {'filled': True, 'fill_ratio': fill_ratio, 'order': parsed}
            if time.time() >= deadline:
                return {'filled': False, 'fill_ratio': fill_ratio, 'order': parsed,
                        'blockers': ['ORDER_TTL_EXPIRED']}
            time.sleep(0.1)
