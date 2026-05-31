from config import cfg

class ArbCalculator:
    def calculate(self, symbol, upbit_quote, binance_quote, krw_usdt):
        u_bid = upbit_quote['bid']
        u_ask = upbit_quote['ask']
        b_bid = binance_quote['bid']
        b_ask = binance_quote['ask']

        # ----------------------------------------------------------------
        # 비용 총합 (bp) - 양방향 공통
        # ----------------------------------------------------------------
        total_cost_bp = (
            cfg.upbit_fee_bp
            + cfg.binance_fee_bp
            + cfg.slippage_bp
            + cfg.fx_error_bp
            + cfg.risk_buffer_bp
        )

        # ----------------------------------------------------------------
        # Direction A: Upbit SELL (bid), Binance BUY (ask)
        #   김프 기준: Upbit가 고평가 → Upbit에서 팔고 Binance에서 산다
        # ----------------------------------------------------------------
        surplus_a_raw_bp = (u_bid - b_ask * krw_usdt) / (b_ask * krw_usdt) * 10000
        direction_a_net_surplus_bp = surplus_a_raw_bp - total_cost_bp

        max_qty_a = min(upbit_quote['bid_size'], binance_quote['ask_size'])
        direction_a_gross_gap_krw = max_qty_a * (u_bid - b_ask * krw_usdt)
        direction_a_net_expected_profit_krw = max(
            0.0,
            max_qty_a * (b_ask * krw_usdt) * (direction_a_net_surplus_bp / 10000)
        )

        # ----------------------------------------------------------------
        # Direction B: Upbit BUY (ask), Binance SELL (bid)
        #   김프 기준: Upbit가 저평가 → Upbit에서 사고 Binance에서 판다
        #   (Binance에 해당 코인 재고가 있을 때만 유효)
        # ----------------------------------------------------------------
        surplus_b_raw_bp = (b_bid * krw_usdt - u_ask) / u_ask * 10000
        direction_b_net_surplus_bp = surplus_b_raw_bp - total_cost_bp

        max_qty_b = min(upbit_quote['ask_size'], binance_quote['bid_size'])
        direction_b_gross_gap_krw = max_qty_b * (b_bid * krw_usdt - u_ask)
        direction_b_net_expected_profit_krw = max(
            0.0,
            max_qty_b * u_ask * (direction_b_net_surplus_bp / 10000)
        )

        # ----------------------------------------------------------------
        # 최적 방향 선택
        # ----------------------------------------------------------------
        if direction_a_net_surplus_bp >= direction_b_net_surplus_bp:
            best_direction = 'A'
            best_net_surplus_bp = direction_a_net_surplus_bp
            net_expected_profit_krw = direction_a_net_expected_profit_krw
            gross_gap_krw = direction_a_gross_gap_krw
            max_fillable_qty = max_qty_a
        else:
            best_direction = 'B'
            best_net_surplus_bp = direction_b_net_surplus_bp
            net_expected_profit_krw = direction_b_net_expected_profit_krw
            gross_gap_krw = direction_b_gross_gap_krw
            max_fillable_qty = max_qty_b

        # kimchi_premium_pct: Direction A 기준 (Upbit bid vs Binance ask mid)
        kimchi_premium_pct = surplus_a_raw_bp / 100.0

        return {
            'symbol': symbol,
            'upbit_bid': u_bid,
            'upbit_ask': u_ask,
            'binance_bid': b_bid,
            'binance_ask': b_ask,
            'krw_usdt': krw_usdt,
            'kimchi_premium_pct': kimchi_premium_pct,
            # Direction A
            'direction_a_net_surplus_bp': direction_a_net_surplus_bp,
            'direction_a_gross_gap_krw': direction_a_gross_gap_krw,
            'direction_a_net_expected_profit_krw': direction_a_net_expected_profit_krw,
            # Direction B
            'direction_b_net_surplus_bp': direction_b_net_surplus_bp,
            'direction_b_gross_gap_krw': direction_b_gross_gap_krw,
            'direction_b_net_expected_profit_krw': direction_b_net_expected_profit_krw,
            # 최적 방향
            'best_direction': best_direction,
            'best_net_surplus_bp': best_net_surplus_bp,
            'net_expected_profit_krw': net_expected_profit_krw,
            'gross_gap_krw': gross_gap_krw,
            'max_fillable_qty': max_fillable_qty,
            # 잔고 요구량 (Direction A 기준)
            'required_upbit_balance_krw': max_qty_a * u_ask,
            'required_binance_balance_usdt': max_qty_a * b_ask,
            # 판단
            'reason_no_trade': ''
        }
