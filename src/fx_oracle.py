class FxOracle:
    def __init__(self, upbit_public, binance_public):
        self.upbit = upbit_public
        self.binance = binance_public
        
    def get_krw_usdt_rate(self):
        upbit_btc = self.upbit.fetch_order_book("BTC")
        binance_btc = self.binance.fetch_order_book("BTC")
        
        if not upbit_btc or not binance_btc:
            return None, "FX_UNAVAILABLE"
            
        upbit_mid = (upbit_btc['bid'] + upbit_btc['ask']) / 2
        binance_mid = (binance_btc['bid'] + binance_btc['ask']) / 2
        
        # simplified sanity check
        spread_upbit = (upbit_btc['ask'] - upbit_btc['bid']) / upbit_mid
        spread_binance = (binance_btc['ask'] - binance_btc['bid']) / binance_mid
        
        if spread_upbit > 0.01 or spread_binance > 0.01:
            return None, "FX_UNTRUSTED"
            
        krw_usdt = upbit_mid / binance_mid
        return krw_usdt, "OK"
