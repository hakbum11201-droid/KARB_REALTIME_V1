class InventoryManager:
    def __init__(self):
        self.upbit_balances = {'KRW': 10000000, 'BTC': 0.5, 'ETH': 5.0, 'XRP': 10000}
        self.binance_balances = {'USDT': 10000, 'BTC': 0.5, 'ETH': 5.0, 'XRP': 10000}
        
    def check_balance(self, exchange, asset, required_amount):
        if exchange == 'upbit':
            return self.upbit_balances.get(asset, 0) >= required_amount
        elif exchange == 'binance':
            return self.binance_balances.get(asset, 0) >= required_amount
        return False
