class ExchangeBase:
    def __init__(self, name):
        self.name = name

    def fetch_order_book(self, symbol):
        raise NotImplementedError
        
    def fetch_balance(self):
        raise NotImplementedError

    def create_order(self, symbol, side, amount, price=None, type='limit'):
        raise NotImplementedError
