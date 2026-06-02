import time


class FxOracle:
    def __init__(
        self,
        upbit_public,
        binance_public,
        cache_enabled=True,
        cache_interval_sec=60,
        cache_max_age_sec=300,
    ):
        self.upbit = upbit_public
        self.binance = binance_public
        self.cache_enabled = bool(cache_enabled)
        self.cache_interval_sec = max(0.0, float(cache_interval_sec))
        self.cache_max_age_sec = max(0.0, float(cache_max_age_sec))
        self.last_fx_check = 0.0
        self.cached_krw_usdt = None
        self.cached_fx_status = "INIT"
        self.fx_last_update_at = 0.0
        self.fx_last_error = ""
        
    def get_krw_usdt_rate(self):
        now = time.time()
        if (
            self.cache_enabled
            and self.cached_krw_usdt
            and now - self.last_fx_check < self.cache_interval_sec
        ):
            return self._cached_result(now)
        self.last_fx_check = now
        upbit_btc = self.upbit.fetch_order_book("BTC")
        binance_btc = self.binance.fetch_order_book("BTC")
        
        if not upbit_btc or not binance_btc:
            return self._use_cached_or_unavailable("FX_UNAVAILABLE", now)
            
        upbit_mid = (upbit_btc['bid'] + upbit_btc['ask']) / 2
        binance_mid = (binance_btc['bid'] + binance_btc['ask']) / 2
        
        # simplified sanity check
        spread_upbit = (upbit_btc['ask'] - upbit_btc['bid']) / upbit_mid
        spread_binance = (binance_btc['ask'] - binance_btc['bid']) / binance_mid
        
        if spread_upbit > 0.01 or spread_binance > 0.01:
            return self._use_cached_or_unavailable("FX_UNTRUSTED", now)
            
        krw_usdt = upbit_mid / binance_mid
        self.cached_krw_usdt = krw_usdt
        self.cached_fx_status = "OK"
        self.fx_last_update_at = now
        self.fx_last_error = ""
        return krw_usdt, "OK"

    def get_status(self):
        now = time.time()
        age_sec = self._cache_age_sec(now)
        return {
            "fx_cache_enabled": self.cache_enabled,
            "fx_cache_age_sec": round(age_sec, 2) if age_sec is not None else None,
            "fx_last_update_at": self.fx_last_update_at,
            "fx_last_error": self.fx_last_error,
            "fx_status": self._cached_status(now),
        }

    def _use_cached_or_unavailable(self, error, now):
        self.fx_last_error = error
        if self.cached_krw_usdt:
            return self._cached_result(now)
        self.cached_fx_status = error
        return None, error

    def _cached_result(self, now):
        status = self._cached_status(now)
        self.cached_fx_status = status
        return self.cached_krw_usdt, status

    def _cached_status(self, now):
        age_sec = self._cache_age_sec(now)
        if self.cached_krw_usdt and age_sec is not None and age_sec > self.cache_max_age_sec:
            return "FX_STALE"
        return self.cached_fx_status

    def _cache_age_sec(self, now):
        return max(0.0, now - self.fx_last_update_at) if self.fx_last_update_at else None
