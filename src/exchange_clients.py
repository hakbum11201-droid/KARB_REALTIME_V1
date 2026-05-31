"""
Read-only private exchange client placeholders.

These clients intentionally expose balance lookup only. They do not expose
withdrawal, transfer, futures, margin, or order execution methods.
"""
from secrets_manager import get_key_status


class UpbitPrivateClient:
    def get_balances(self) -> dict:
        status = get_key_status()
        if status['UPBIT_ACCESS_KEY'] != 'Set' or status['UPBIT_SECRET_KEY'] != 'Set':
            return {'ok': False, 'balances': {}, 'blockers': ['UPBIT_KEY_MISSING']}
        return {
            'ok': False,
            'balances': {},
            'blockers': ['UPBIT_BALANCE_LOOKUP_NOT_IMPLEMENTED'],
        }


class BinanceSpotPrivateClient:
    def get_balances(self) -> dict:
        status = get_key_status()
        if status['BINANCE_API_KEY'] != 'Set' or status['BINANCE_API_SECRET'] != 'Set':
            return {'ok': False, 'balances': {}, 'blockers': ['BINANCE_KEY_MISSING']}
        return {
            'ok': False,
            'balances': {},
            'blockers': ['BINANCE_SPOT_BALANCE_LOOKUP_NOT_IMPLEMENTED'],
        }

    def get_account_info(self) -> dict:
        result = self.get_balances()
        return {
            'ok': result['ok'],
            'account_type': 'SPOT',
            'balances': result['balances'],
            'blockers': result['blockers'],
        }
