import yaml
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.yaml')


class Config:
    def __init__(self):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            self._cfg = yaml.safe_load(f)
        self._mode_override = None

    def get(self, name, default=None):
        return self._cfg.get(name, default)

    # ── 운용 모드 ──────────────────────────────────────────────
    @property
    def mode(self): return self._mode_override or self.get('mode', 'paper')

    def set_mode(self, mode: str):
        if mode not in ('paper', 'tiny_live', 'live'):
            raise ValueError(f"Invalid mode: {mode}. Must be paper/tiny_live/live")
        self._mode_override = mode
    @property
    def enable_live_trading(self): return self.get('enable_live_trading', False)
    @property
    def tiny_live_enabled(self): return self.get('tiny_live_enabled', False)
    @property
    def live_order_enabled(self): return self.get('live_order_enabled', False)
    @property
    def live_mode_enabled(self): return self.get('live_mode_enabled', False)
    @property
    def withdrawals_enabled(self): return self.get('withdrawals_enabled', False)
    @property
    def futures_hedge_enabled(self): return self.get('futures_hedge_enabled', False)
    @property
    def manual_rebalance_only(self): return self.get('manual_rebalance_only', True)
    @property
    def require_paper_pass_for_tiny_live(self): return self.get('tiny_live_require_paper_pass', self.get('require_paper_pass_for_tiny_live', True))
    @property
    def min_paper_closed_trades_for_tiny_live(self): return self.get('min_paper_closed_trades_for_tiny_live', 10)
    @property
    def min_paper_win_rate_for_tiny_live(self): return self.get('min_paper_win_rate_for_tiny_live', 0.65)
    @property
    def min_paper_net_pnl_krw_for_tiny_live(self): return self.get('min_paper_net_pnl_krw_for_tiny_live', 0)
    @property
    def tiny_live_order_krw(self): return self.get('tiny_live_order_krw', 10000)
    @property
    def tiny_live_max_order_krw(self): return self.get('tiny_live_max_order_krw', 20000)
    @property
    def tiny_live_daily_loss_limit_krw(self): return self.get('tiny_live_daily_loss_limit_krw', 10000)
    @property
    def tiny_live_max_trades_per_day(self): return self.get('tiny_live_max_trades_per_day', 5)
    @property
    def tiny_live_require_preflight(self): return self.get('tiny_live_require_preflight', True)
    @property
    def tiny_live_require_inventory_ok(self): return self.get('tiny_live_require_inventory_ok', True)
    @property
    def use_websocket_market_data(self): return self.get('use_websocket_market_data', True)
    @property
    def rest_fallback_enabled(self): return self.get('rest_fallback_enabled', True)
    @property
    def block_new_entries_on_partial_risk(self): return self.get('block_new_entries_on_partial_risk', True)
    @property
    def order_ttl_sec(self): return self.get('order_ttl_sec', 1.5)
    @property
    def min_fill_ratio(self): return self.get('min_fill_ratio', 0.8)
    @property
    def emergency_liquidation_enabled(self): return self.get('emergency_liquidation_enabled', False)
    @property
    def emergency_auto_execute(self): return self.get('emergency_auto_execute', False)
    @property
    def emergency_strategy(self): return self.get('emergency_strategy', 'COMPLETE_MISSING_LEG')
    @property
    def emergency_max_order_krw(self): return self.get('emergency_max_order_krw', 20000)
    @property
    def emergency_max_slippage_bp(self): return self.get('emergency_max_slippage_bp', 20)
    @property
    def emergency_max_attempts_per_day(self): return self.get('emergency_max_attempts_per_day', 2)
    @property
    def order_tracker_enabled(self): return self.get('order_tracker_enabled', True)
    @property
    def order_tracker_recent_max_items(self): return self.get('order_tracker_recent_max_items', 100)
    @property
    def enabled_strategy_pairs(self): return self.get('enabled_strategy_pairs', {})
    @property
    def bithumb_fee_bp(self): return self.get('bithumb_fee_bp', 4)
    @property
    def bithumb_public_enabled(self): return self.get('bithumb_public_enabled', True)
    @property
    def bithumb_private_enabled(self): return self.get('bithumb_private_enabled', False)
    @property
    def upbit_bithumb_paper_enabled(self): return self.get('upbit_bithumb_paper_enabled', True)
    @property
    def upbit_bithumb_tiny_live_enabled(self): return self.get('upbit_bithumb_tiny_live_enabled', False)
    @property
    def upbit_bithumb_live_enabled(self): return self.get('upbit_bithumb_live_enabled', False)
    @property
    def upbit_bithumb_order_krw(self): return self.get('upbit_bithumb_order_krw', 10000)
    @property
    def upbit_bithumb_max_order_krw(self): return self.get('upbit_bithumb_max_order_krw', 20000)
    @property
    def upbit_bithumb_daily_loss_limit_krw(self): return self.get('upbit_bithumb_daily_loss_limit_krw', 10000)
    @property
    def upbit_bithumb_max_trades_per_day(self): return self.get('upbit_bithumb_max_trades_per_day', 5)
    @property
    def bithumb_min_order_krw(self): return self.get('bithumb_min_order_krw', 5000)

    # ── 심볼 / 루프 ────────────────────────────────────────────
    @property
    def symbols(self): return self.get('symbols', [])
    @property
    def loop_interval_sec(self): return self.get('loop_interval_sec', 1)

    # ── 비용 (bp) ──────────────────────────────────────────────
    @property
    def upbit_fee_bp(self): return self.get('upbit_fee_bp', 5)
    @property
    def binance_fee_bp(self): return self.get('binance_fee_bp', 10)
    @property
    def slippage_bp(self): return self.get('slippage_bp', 5)
    @property
    def fx_error_bp(self): return self.get('fx_error_bp', 5)
    @property
    def risk_buffer_bp(self): return self.get('risk_buffer_bp', 10)
    @property
    def total_cost_bp(self):
        return (self.upbit_fee_bp + self.binance_fee_bp
                + self.slippage_bp + self.fx_error_bp + self.risk_buffer_bp)

    # ── Risk Guard ─────────────────────────────────────────────
    @property
    def min_net_surplus_bp(self): return self.get('min_net_surplus_bp', 35)
    @property
    def min_expected_profit_krw(self): return self.get('min_expected_profit_krw', 1000)
    @property
    def max_position_krw(self): return self.get('max_position_krw', 100000)
    @property
    def max_one_trade_krw(self): return self.get('max_one_trade_krw', 50000)
    @property
    def min_depth_multiplier(self): return self.get('min_depth_multiplier', 2.0)
    @property
    def max_spread_bp(self): return self.get('max_spread_bp', 20)
    @property
    def stale_quote_ms(self): return self.get('stale_quote_ms', 1500)
    @property
    def max_latency_ms(self): return self.get('max_latency_ms', 800)
    @property
    def cooldown_sec(self): return self.get('cooldown_sec', 10)
    @property
    def daily_loss_limit_krw(self): return self.get('daily_loss_limit_krw', 30000)
    @property
    def consecutive_fail_limit(self): return self.get('consecutive_fail_limit', 3)

    # ── Paper ──────────────────────────────────────────────────
    @property
    def paper_timeout_sec(self): return self.get('paper_timeout_sec', 300)
    @property
    def paper_take_profit_bp(self): return self.get('paper_take_profit_bp', 20)
    @property
    def paper_stop_loss_bp(self): return self.get('paper_stop_loss_bp', 20)
    @property
    def paper_initial_upbit_krw(self): return self.get('paper_initial_upbit_krw', 1000000)
    @property
    def paper_initial_binance_usdt(self): return self.get('paper_initial_binance_usdt', 700)
    @property
    def paper_initial_coin_qty(self): return self.get('paper_initial_coin_qty', {})

    # ── 저장 / 로그 ────────────────────────────────────────────
    @property
    def raw_save_enabled(self): return self.get('raw_save_enabled', False)
    @property
    def bounded_collector_enabled(self): return self.get('bounded_collector_enabled', False)
    @property
    def log_decisions_only_when_candidate(self): return self.get('log_decisions_only_when_candidate', True)
    @property
    def decision_log_min_surplus_bp(self): return self.get('decision_log_min_surplus_bp', 0)
    @property
    def state_write_interval_sec(self): return self.get('state_write_interval_sec', 5)
    @property
    def decision_log_max_items(self): return self.get('decision_log_max_items', 100)
    @property
    def telemetry_write_interval_sec(self): return self.get('telemetry_write_interval_sec', 5)
    @property
    def session_summary_interval_sec(self): return self.get('session_summary_interval_sec', 3600)
    @property
    def max_log_file_mb(self): return self.get('max_log_file_mb', 20)
    @property
    def log_retention_days(self): return self.get('log_retention_days', 14)


cfg = Config()
