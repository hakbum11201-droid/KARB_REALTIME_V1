from exchange_base import ExchangeBase

class BinancePrivate(ExchangeBase):
    def __init__(self):
        super().__init__("BinancePrivate")
        
    def create_order(self, symbol, side, amount, price=None, type='limit'):
        raise NotImplementedError("Private API not implemented / guarded for paper mode")
