import requests
from exchange_base import ExchangeBase

class BinancePublic(ExchangeBase):
    def __init__(self):
        super().__init__("Binance")
        self.base_url = "https://api.binance.com/api/v3"

    def fetch_order_book(self, symbol):
        market = f"{symbol}USDT"
        url = f"{self.base_url}/ticker/bookTicker?symbol={market}"
        try:
            resp = requests.get(url, timeout=3)
            data = resp.json()
            if 'bidPrice' in data:
                return {
                    'bid': float(data['bidPrice']),
                    'ask': float(data['askPrice']),
                    'bid_size': float(data['bidQty']),
                    'ask_size': float(data['askQty'])
                }
        except Exception:
            pass
        return None
