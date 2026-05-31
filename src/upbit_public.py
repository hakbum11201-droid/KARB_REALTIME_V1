import requests
from exchange_base import ExchangeBase

class UpbitPublic(ExchangeBase):
    def __init__(self):
        super().__init__("Upbit")
        self.base_url = "https://api.upbit.com/v1"

    def fetch_order_book(self, symbol):
        market = f"KRW-{symbol}"
        url = f"{self.base_url}/orderbook?markets={market}"
        try:
            resp = requests.get(url, timeout=3)
            data = resp.json()
            if data and isinstance(data, list):
                obu = data[0]['orderbook_units'][0]
                return {
                    'bid': float(obu['bid_price']),
                    'ask': float(obu['ask_price']),
                    'bid_size': float(obu['bid_size']),
                    'ask_size': float(obu['ask_size'])
                }
        except Exception:
            pass
        return None
