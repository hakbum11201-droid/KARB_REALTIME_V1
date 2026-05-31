from config import cfg

class ArbCalculator:
    def calculate(self, symbol, upbit_quote, binance_quote, krw_usdt):
        u_bid = upbit_quote['bid']
        u_ask = upbit_quote['ask']
        b_bid = binance_quote['bid']
        b_ask = binance_quote['ask']
        
        kimp_pct = (u_bid - b_ask * krw_usdt) / (b_ask * krw_usdt) * 100
        
        # Direction A: Upbit SELL, Binance BUY
        surplus_a_raw = (u_bid - b_ask * krw_usdt) / (b_ask * krw_usdt) * 10000
        net_surplus_a = surplus_a_raw - cfg.upbit_fee_bp - cfg.binance_fee_bp - cfg.slippage_bp - cfg.fx_error_bp - cfg.risk_buffer_bp
        
        # Direction B: Upbit BUY, Binance SELL
        surplus_b_raw = (b_bid * krw_usdt - u_ask) / u_ask * 10000
        net_surplus_b = surplus_b_raw - cfg.upbit_fee_bp - cfg.binance_fee_bp - cfg.slippage_bp - cfg.fx_error_bp - cfg.risk_buffer_bp
        
        max_qty_a = min(upbit_quote['bid_size'], binance_quote['ask_size'])
        profit_a_krw = max_qty_a * (u_bid - b_ask * krw_usdt) * (net_surplus_a / 10000)
        
        return {
            'symbol': symbol,
            'upbit_bid': u_bid,
            'upbit_ask': u_ask,
            'binance_bid': b_bid,
            'binance_ask': b_ask,
            'krw_usdt': krw_usdt,
            'kimchi_premium_pct': kimp_pct,
            'direction_a_net_surplus_bp': net_surplus_a,
            'direction_b_net_surplus_bp': net_surplus_b,
            'max_fillable_qty': max_qty_a,
            'expected_profit_krw': max(0, profit_a_krw),
            'required_upbit_balance': max_qty_a * u_ask,
            'required_binance_balance': max_qty_a * b_ask,
            'reason_no_trade': ''
        }
