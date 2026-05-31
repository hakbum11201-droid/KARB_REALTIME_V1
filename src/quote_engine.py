import time

class QuoteEngine:
    def __init__(self, upbit_public, binance_public, symbols):
        self.upbit = upbit_public
        self.binance = binance_public
        self.symbols = symbols
        
    def fetch_all(self):
        quotes = {}
        for sym in self.symbols:
            u_quote = self.upbit.fetch_order_book(sym)
            b_quote = self.binance.fetch_order_book(sym)
            if u_quote and b_quote:
                quotes[sym] = {
                    'symbol': sym,
                    'upbit': u_quote,
                    'binance': b_quote,
                    'timestamp': time.time()
                }
        return quotes
