import time

from config import cfg
from slippage_model import estimate_slippage_bp

class ArbCalculator:
    def calculate(self, symbol, upbit_quote, binance_quote, krw_usdt):
        u_bid = upbit_quote['bid']
        u_ask = upbit_quote['ask']
        b_bid = binance_quote['bid']
        b_ask = binance_quote['ask']

        # ----------------------------------------------------------------
        # 비용 총합 (bp) - 양방향 공통
        # ----------------------------------------------------------------
        slippage_a = self._combined_slippage(
            (upbit_quote, 'SELL'), (binance_quote, 'BUY'))
        slippage_b = self._combined_slippage(
            (upbit_quote, 'BUY'), (binance_quote, 'SELL'))
        total_cost_a_bp = (
            cfg.upbit_fee_bp
            + cfg.binance_fee_bp
            + slippage_a['dynamic_slippage_bp']
            + cfg.fx_error_bp
            + cfg.risk_buffer_bp
        )
        total_cost_b_bp = (
            cfg.upbit_fee_bp + cfg.binance_fee_bp + slippage_b['dynamic_slippage_bp']
            + cfg.fx_error_bp + cfg.risk_buffer_bp
        )

        # ----------------------------------------------------------------
        # Direction A: Upbit SELL (bid), Binance BUY (ask)
        #   김프 기준: Upbit가 고평가 → Upbit에서 팔고 Binance에서 산다
        # ----------------------------------------------------------------
        surplus_a_raw_bp = (u_bid - b_ask * krw_usdt) / (b_ask * krw_usdt) * 10000
        direction_a_net_surplus_bp = surplus_a_raw_bp - total_cost_a_bp

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
        direction_b_net_surplus_bp = surplus_b_raw_bp - total_cost_b_bp

        max_qty_b = min(upbit_quote['ask_size'], binance_quote['bid_size'])
        direction_b_gross_gap_krw = max_qty_b * (b_bid * krw_usdt - u_ask)
        direction_b_net_expected_profit_krw = max(
            0.0,
            max_qty_b * u_ask * (direction_b_net_surplus_bp / 10000)
        )
        direction_a_required_assets = {
            'upbit_coin_qty': max_qty_a,
            'binance_usdt': max_qty_a * b_ask,
            'upbit_krw': 0.0,
            'binance_coin_qty': 0.0,
            'notional_krw': max_qty_a * b_ask * krw_usdt,
        }
        direction_b_required_assets = {
            'upbit_krw': max_qty_b * u_ask,
            'binance_coin_qty': max_qty_b,
            'upbit_coin_qty': 0.0,
            'binance_usdt': 0.0,
            'notional_krw': max_qty_b * u_ask,
        }

        # ----------------------------------------------------------------
        # 최적 방향 선택
        # ----------------------------------------------------------------
        if direction_a_net_surplus_bp >= direction_b_net_surplus_bp:
            best_direction = 'A'
            best_net_surplus_bp = direction_a_net_surplus_bp
            net_expected_profit_krw = direction_a_net_expected_profit_krw
            gross_gap_krw = direction_a_gross_gap_krw
            max_fillable_qty = max_qty_a
            slippage = slippage_a
            selected_required_assets = direction_a_required_assets
            selected_buy_price_krw = b_ask * krw_usdt
            selected_sell_price_krw = u_bid
            notional_basis = 'BINANCE_BUY_KRW_VALUE'
        else:
            best_direction = 'B'
            best_net_surplus_bp = direction_b_net_surplus_bp
            net_expected_profit_krw = direction_b_net_expected_profit_krw
            gross_gap_krw = direction_b_gross_gap_krw
            max_fillable_qty = max_qty_b
            slippage = slippage_b
            selected_required_assets = direction_b_required_assets
            selected_buy_price_krw = u_ask
            selected_sell_price_krw = b_bid * krw_usdt
            notional_basis = 'UPBIT_BUY_KRW_VALUE'

        # kimchi_premium_pct: Direction A 기준 (Upbit bid vs Binance ask mid)
        kimchi_premium_pct = surplus_a_raw_bp / 100.0

        return {
            'pair_id': 'UPBIT_BINANCE',
            'strategy_type': 'CROSS_BORDER_SPOT',
            'domestic_only': False,
            'fx_required': True,
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
            'direction_a_required_assets': direction_a_required_assets,
            'direction_b_required_assets': direction_b_required_assets,
            'selected_required_assets': selected_required_assets,
            'selected_notional_krw': selected_required_assets['notional_krw'],
            'selected_qty': max_fillable_qty,
            'selected_buy_price_krw': selected_buy_price_krw,
            'selected_sell_price_krw': selected_sell_price_krw,
            'notional_basis': notional_basis,
            **slippage,
            'slippage_model_used': slippage['model_used'],
            'paper_latency_sim_enabled': cfg.paper_latency_sim_enabled,
            'paper_edge_quality': 'PENDING',
            # Backward-compatible fields now follow the selected direction.
            'required_upbit_balance_krw': selected_required_assets['upbit_krw'],
            'required_binance_balance_usdt': selected_required_assets['binance_usdt'],
            'deprecated_required_fields': True,
            # 판단
            'reason_no_trade': ''
        }

    def calculate_domestic_krw(self, symbol, upbit_quote, bithumb_quote):
        """Compare Upbit and Bithumb KRW top-of-book quotes for paper display only."""
        if not bithumb_quote.get('ok'):
            return self._domestic_unavailable(symbol, 'BITHUMB_QUOTE_UNAVAILABLE')
        if not upbit_quote or not bithumb_quote:
            return self._domestic_unavailable(symbol, 'QUOTE_UNAVAILABLE')

        u_bid, u_ask = float(upbit_quote['bid']), float(upbit_quote['ask'])
        h_bid, h_ask = float(bithumb_quote['bid']), float(bithumb_quote['ask'])
        slippage_a = self._combined_slippage(
            (upbit_quote, 'SELL'), (bithumb_quote, 'BUY'))
        slippage_b = self._combined_slippage(
            (upbit_quote, 'BUY'), (bithumb_quote, 'SELL'))
        total_cost_a_bp = cfg.upbit_fee_bp + cfg.bithumb_fee_bp + slippage_a['dynamic_slippage_bp'] + cfg.risk_buffer_bp
        total_cost_b_bp = cfg.upbit_fee_bp + cfg.bithumb_fee_bp + slippage_b['dynamic_slippage_bp'] + cfg.risk_buffer_bp

        surplus_a_raw_bp = (u_bid - h_ask) / h_ask * 10000
        surplus_b_raw_bp = (h_bid - u_ask) / u_ask * 10000
        direction_a_net_surplus_bp = surplus_a_raw_bp - total_cost_a_bp
        direction_b_net_surplus_bp = surplus_b_raw_bp - total_cost_b_bp
        max_qty_a = min(float(upbit_quote['bid_size']), float(bithumb_quote['ask_size']))
        max_qty_b = min(float(bithumb_quote['bid_size']), float(upbit_quote['ask_size']))
        direction_a_required_assets = {
            'upbit_coin_qty': max_qty_a,
            'bithumb_krw': max_qty_a * h_ask,
            'bithumb_coin_qty': 0.0,
            'upbit_krw': 0.0,
            'notional_krw': max_qty_a * h_ask,
        }
        direction_b_required_assets = {
            'bithumb_coin_qty': max_qty_b,
            'upbit_krw': max_qty_b * u_ask,
            'upbit_coin_qty': 0.0,
            'bithumb_krw': 0.0,
            'notional_krw': max_qty_b * u_ask,
        }

        if direction_a_net_surplus_bp >= direction_b_net_surplus_bp:
            best_direction = 'UPBIT_BITHUMB_A'
            best_net_surplus_bp = direction_a_net_surplus_bp
            max_fillable_qty = max_qty_a
            reference_price = h_ask
            slippage = slippage_a
            selected_required_assets = direction_a_required_assets
            selected_buy_price_krw = h_ask
            selected_sell_price_krw = u_bid
            notional_basis = 'BITHUMB_BUY_KRW_VALUE'
        else:
            best_direction = 'UPBIT_BITHUMB_B'
            best_net_surplus_bp = direction_b_net_surplus_bp
            max_fillable_qty = max_qty_b
            reference_price = u_ask
            slippage = slippage_b
            selected_required_assets = direction_b_required_assets
            selected_buy_price_krw = u_ask
            selected_sell_price_krw = h_bid
            notional_basis = 'UPBIT_BUY_KRW_VALUE'

        expected_profit_krw = max(
            0.0, max_fillable_qty * reference_price * best_net_surplus_bp / 10000
        )
        result = {
            'pair_id': 'UPBIT_BITHUMB',
            'strategy_type': 'DOMESTIC_KRW',
            'domestic_only': True,
            'fx_required': False,
            'paper_only': True,
            'symbol': symbol,
            'upbit_bid': u_bid,
            'upbit_ask': u_ask,
            'bithumb_bid': h_bid,
            'bithumb_ask': h_ask,
            'upbit_ts': upbit_quote.get('ts'),
            'bithumb_ts': bithumb_quote.get('ts'),
            'direction_a_net_surplus_bp': direction_a_net_surplus_bp,
            'direction_b_net_surplus_bp': direction_b_net_surplus_bp,
            'best_direction': best_direction,
            'best_net_surplus_bp': best_net_surplus_bp,
            'net_expected_profit_krw': expected_profit_krw,
            'max_fillable_qty': max_fillable_qty,
            'direction_a_required_assets': direction_a_required_assets,
            'direction_b_required_assets': direction_b_required_assets,
            'selected_required_assets': selected_required_assets,
            'selected_notional_krw': selected_required_assets['notional_krw'],
            'selected_qty': max_fillable_qty,
            'selected_buy_price_krw': selected_buy_price_krw,
            'selected_sell_price_krw': selected_sell_price_krw,
            'notional_basis': notional_basis,
            **slippage,
            'slippage_model_used': slippage['model_used'],
            'paper_latency_sim_enabled': cfg.paper_latency_sim_enabled,
            'paper_edge_quality': 'PENDING',
            'reason_no_trade': '',
        }
        result['reason_no_trade'] = self._domestic_reason(result)
        return result

    @staticmethod
    def _combined_slippage(*legs):
        if not cfg.use_dynamic_slippage:
            return {
                'dynamic_slippage_bp': float(cfg.slippage_bp),
                'depth_available_krw': 0.0,
                'fill_price_estimate': 0.0,
                'liquidity_class': 'NORMAL',
                'model_used': 'fixed',
            }
        estimates = [
            estimate_slippage_bp(book, side, cfg.max_one_trade_krw, cfg.base_slippage_bp)
            for book, side in legs
        ]
        worst = max(estimates, key=lambda item: item['dynamic_slippage_bp'])
        return {
            'dynamic_slippage_bp': max(float(cfg.slippage_bp), worst['dynamic_slippage_bp']),
            'depth_available_krw': min(item['depth_available_krw'] for item in estimates),
            'fill_price_estimate': worst['fill_price_estimate'],
            'liquidity_class': (
                'LOW_DEPTH' if any(item['liquidity_class'] == 'LOW_DEPTH' for item in estimates)
                else worst['liquidity_class']
            ),
            'model_used': 'depth' if any(item['model_used'] == 'depth' for item in estimates) else worst['model_used'],
        }

    @staticmethod
    def _domestic_reason(result):
        if result.get('liquidity_class') == 'LOW_DEPTH':
            return 'LOW_DEPTH'
        now_ms = time.time() * 1000
        for key in ('upbit_ts', 'bithumb_ts'):
            timestamp = result.get(key)
            if not timestamp or now_ms - float(timestamp) * 1000 > cfg.stale_quote_ms:
                return 'STALE_QUOTE'
        for bid_key, ask_key in (('upbit_bid', 'upbit_ask'), ('bithumb_bid', 'bithumb_ask')):
            bid, ask = result[bid_key], result[ask_key]
            if bid <= 0 or ask <= 0:
                return 'QUOTE_UNAVAILABLE'
            if (ask - bid) / bid * 10000 > cfg.max_spread_bp:
                return 'WIDE_SPREAD'
        target_qty = cfg.max_one_trade_krw / result['upbit_ask']
        if result['max_fillable_qty'] < target_qty / cfg.min_depth_multiplier:
            return 'LOW_DEPTH'
        if result['best_net_surplus_bp'] < cfg.min_net_surplus_bp:
            return 'LOW_SURPLUS'
        if result['net_expected_profit_krw'] < cfg.min_expected_profit_krw:
            return 'LOW_EXPECTED_PROFIT'
        return 'OK'

    @staticmethod
    def _domestic_unavailable(symbol, reason):
        return {
            'pair_id': 'UPBIT_BITHUMB',
            'strategy_type': 'DOMESTIC_KRW',
            'domestic_only': True,
            'fx_required': False,
            'paper_only': True,
            'symbol': symbol,
            'best_direction': '',
            'best_net_surplus_bp': -9999.0,
            'net_expected_profit_krw': 0.0,
            'max_fillable_qty': 0.0,
            'reason_no_trade': reason,
        }
