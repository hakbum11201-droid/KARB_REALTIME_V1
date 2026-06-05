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
    def tiny_live_calibration(self): return self.get('tiny_live_calibration', {})
    @property
    def tiny_live_daily_loss_limit_krw(self): return self.get('tiny_live_daily_loss_limit_krw', 10000)
    @property
    def tiny_live_max_trades_per_day(self): return self.get('tiny_live_max_trades_per_day', 5)
    @property
    def tiny_live_require_preflight(self): return self.get('tiny_live_require_preflight', True)
    @property
    def tiny_live_require_inventory_ok(self): return self.get('tiny_live_require_inventory_ok', True)
    @property
    def live_quote_max_age_ms(self): return self.get('live_quote_max_age_ms', 1000)
    @property
    def tiny_live_quote_max_age_ms(self): return self.get('tiny_live_quote_max_age_ms', 1500)
    @property
    def live_domestic_quote_max_age_ms(self): return self.get('live_domestic_quote_max_age_ms', 1200)
    @property
    def tiny_live_domestic_quote_max_age_ms(self): return self.get('tiny_live_domestic_quote_max_age_ms', 1800)
    @property
    def live_cross_border_quote_max_age_ms(self): return self.get('live_cross_border_quote_max_age_ms', 1000)
    @property
    def tiny_live_cross_border_quote_max_age_ms(self): return self.get('tiny_live_cross_border_quote_max_age_ms', 1500)
    @property
    def live_allow_stale_grace_quotes(self): return self.get('live_allow_stale_grace_quotes', False)
    @property
    def tiny_live_allow_stale_grace_quotes(self): return self.get('tiny_live_allow_stale_grace_quotes', False)
    @property
    def live_require_both_legs_fresh(self): return self.get('live_require_both_legs_fresh', True)
    @property
    def live_freshness_observe_only(self): return self.get('live_freshness_observe_only', False)
    @property
    def tiny_live_freshness_observe_only(self): return self.get('tiny_live_freshness_observe_only', True)
    @property
    def paper_active_symbol_count(self): return self.get('paper_active_symbol_count', 20)
    @property
    def live_active_symbols(self): return self.get('live_active_symbols', [])
    @property
    def live_use_dynamic_symbols(self): return self.get('live_use_dynamic_symbols', False)
    @property
    def live_allow_recheck_actionable_entries(self): return self.get('live_allow_recheck_actionable_entries', True)
    @property
    def live_allow_wide_spread_recheck_entries(self): return self.get('live_allow_wide_spread_recheck_entries', True)
    @property
    def tiny_live_allow_recheck_actionable_entries(self): return self.get('tiny_live_allow_recheck_actionable_entries', True)
    @property
    def tiny_live_allow_wide_spread_recheck_entries(self): return self.get('tiny_live_allow_wide_spread_recheck_entries', True)
    @property
    def paper_entry_max_quote_age_ms(self): return self.get('paper_entry_max_quote_age_ms', 2500)
    @property
    def paper_entry_max_quote_age_by_reason(self): return self.get('paper_entry_max_quote_age_by_reason', {})
    @property
    def live_entry_max_quote_age_by_reason(self): return self.get('live_entry_max_quote_age_by_reason', {})
    @property
    def quote_freshness(self): return self.get('quote_freshness', {})
    @property
    def notional_sweep(self): return self.get('notional_sweep', {})
    @property
    def notional_sweep_enabled(self): return self.notional_sweep.get('enabled', True)
    @property
    def notional_sweep_notionals_krw(self): return self.notional_sweep.get('notionals_krw', [10000, 50000, 100000])
    @property
    def notional_sweep_max_symbols(self): return self.notional_sweep.get('max_symbols', 20)
    @property
    def notional_sweep_cache_ttl_sec(self): return self.notional_sweep.get('cache_ttl_sec', 3)
    @property
    def notional_sweep_include_only_actionable(self): return self.notional_sweep.get('include_only_actionable', False)
    @property
    def paper_max_leg_quote_age_ms(self): return self.quote_freshness.get('paper_max_leg_quote_age_ms', 1200)
    @property
    def tiny_live_max_leg_quote_age_ms(self): return self.quote_freshness.get('tiny_live_max_leg_quote_age_ms', 700)
    @property
    def live_max_leg_quote_age_ms(self): return self.quote_freshness.get('live_max_leg_quote_age_ms', 500)
    @property
    def entry_decision_wait_warn_ms(self): return self.quote_freshness.get('entry_decision_wait_warn_ms', 700)
    @property
    def tiny_live_entry_decision_wait_max_ms(self): return self.quote_freshness.get('tiny_live_entry_decision_wait_max_ms', 500)
    @property
    def live_entry_decision_wait_max_ms(self): return self.quote_freshness.get('live_entry_decision_wait_max_ms', 400)
    @property
    def paper_entry_domestic_max_quote_age_ms(self): return self.get('paper_entry_domestic_max_quote_age_ms', 3000)
    @property
    def paper_entry_cross_border_max_quote_age_ms(self): return self.get('paper_entry_cross_border_max_quote_age_ms', 2000)
    @property
    def paper_entry_require_positive_net(self): return self.get('paper_entry_require_positive_net', True)
    @property
    def paper_entry_allow_live_blocked(self): return self.get('paper_entry_allow_live_blocked', True)
    @property
    def completed_handoff_entry_ttl_ms(self): return self.get('completed_handoff_entry_ttl_ms', 2000)
    @property
    def completed_handoff_domestic_entry_ttl_ms(self): return self.get('completed_handoff_domestic_entry_ttl_ms', 2500)
    @property
    def completed_handoff_cross_border_entry_ttl_ms(self): return self.get('completed_handoff_cross_border_entry_ttl_ms', 1800)
    @property
    def stale_recheck_enabled(self): return self.get('stale_recheck_enabled', True)
    @property
    def stale_recheck_paper_only(self): return self.get('stale_recheck_paper_only', True)
    @property
    def stale_recheck_pair_ids(self): return self.get('stale_recheck_pair_ids', ['UPBIT_BITHUMB'])
    @property
    def stale_recheck_min_surplus_bp_extra(self): return self.get('stale_recheck_min_surplus_bp_extra', 10)
    @property
    def stale_recheck_min_net_profit_krw(self): return self.get('stale_recheck_min_net_profit_krw', 10)
    @property
    def stale_recheck_allowed_liquidity(self): return self.get('stale_recheck_allowed_liquidity', ['GOOD', 'NORMAL'])
    @property
    def stale_recheck_cooldown_sec(self): return self.get('stale_recheck_cooldown_sec', 5)
    @property
    def stale_recheck_fast_pass_ms(self): return self.get('stale_recheck_fast_pass_ms', 1000)
    @property
    def stale_recheck_late_pass_ms(self): return self.get('stale_recheck_late_pass_ms', 3000)
    @property
    def stale_recheck_max_per_minute(self): return self.get('stale_recheck_max_per_minute', 20)
    @property
    def stale_recheck_max_queue_size(self): return self.get('stale_recheck_max_queue_size', 50)
    @property
    def stale_recheck_result_ttl_sec(self): return self.get('stale_recheck_result_ttl_sec', 10)
    @property
    def stale_recheck_priority_worker_enabled(self): return self.get('stale_recheck_priority_worker_enabled', True)
    @property
    def stale_recheck_symbol_only_refresh(self): return self.get('stale_recheck_symbol_only_refresh', True)
    @property
    def stale_recheck_inflight_dedupe(self): return self.get('stale_recheck_inflight_dedupe', True)
    @property
    def stale_recheck_priority_fetch_timeout_ms(self): return self.get('stale_recheck_priority_fetch_timeout_ms', 700)
    @property
    def stale_recheck_priority_max_workers(self): return self.get('stale_recheck_priority_max_workers', 1)
    @property
    def wide_spread_recheck_enabled(self): return self.get('wide_spread_recheck_enabled', True)
    @property
    def wide_spread_recheck_paper_only(self): return self.get('wide_spread_recheck_paper_only', True)
    @property
    def wide_spread_recheck_min_surplus_bp_extra(self): return self.get('wide_spread_recheck_min_surplus_bp_extra', 10)
    @property
    def wide_spread_recheck_min_net_profit_krw(self): return self.get('wide_spread_recheck_min_net_profit_krw', 10)
    @property
    def wide_spread_recheck_allowed_liquidity(self): return self.get('wide_spread_recheck_allowed_liquidity', ['GOOD', 'NORMAL'])
    @property
    def wide_spread_entry_min_net_profit_krw(self): return self.get('wide_spread_entry_min_net_profit_krw', 30)
    @property
    def wide_spread_entry_min_surplus_bp(self): return self.get('wide_spread_entry_min_surplus_bp', 40)
    @property
    def wide_spread_entry_max_dynamic_slippage_bp(self): return self.get('wide_spread_entry_max_dynamic_slippage_bp', 20)
    @property
    def use_websocket_market_data(self): return self.get('use_websocket_market_data', True)
    @property
    def rest_fallback_enabled(self): return self.get('rest_fallback_enabled', True)
    @property
    def rate_limiter_enabled(self): return self.get('rate_limiter_enabled', True)
    @property
    def rate_limit_upbit_per_sec(self): return self.get('rate_limit_upbit_per_sec', 5)
    @property
    def rate_limit_bithumb_per_sec(self): return self.get('rate_limit_bithumb_per_sec', 8)
    @property
    def rate_limit_binance_per_sec(self): return self.get('rate_limit_binance_per_sec', 10)
    @property
    def rate_limit_burst(self): return self.get('rate_limit_burst', 5)
    @property
    def rate_limit_429_backoff_sec(self): return self.get('rate_limit_429_backoff_sec', 30)
    @property
    def upbit_429_backoff_sec(self): return self.get('upbit_429_backoff_sec', 30)
    @property
    def rest_fallback_min_interval_ms(self): return self.get('rest_fallback_min_interval_ms', 1000)
    @property
    def rest_fallback_cache_enabled(self): return self.get('rest_fallback_cache_enabled', True)
    @property
    def rest_fallback_cache_refresh_ms(self): return self.get('rest_fallback_cache_refresh_ms', 1000)
    @property
    def rest_fallback_cache_stale_ms(self): return self.get('rest_fallback_cache_stale_ms', 3000)
    @property
    def rest_fallback_cache_skip_on_backoff(self): return self.get('rest_fallback_cache_skip_on_backoff', True)
    @property
    def rest_direct_fallback_enabled(self): return self.get('rest_direct_fallback_enabled', False)
    @property
    def rest_direct_call_warn_threshold(self): return self.get('rest_direct_call_warn_threshold', 1)
    @property
    def rest_cache_upbit_refresh_ms(self): return self.get('rest_cache_upbit_refresh_ms', 3000)
    @property
    def rest_cache_binance_refresh_ms(self): return self.get('rest_cache_binance_refresh_ms', 1000)
    @property
    def rest_cache_skip_upbit_when_ws_ok(self): return self.get('rest_cache_skip_upbit_when_ws_ok', True)
    @property
    def rest_cache_ws_fresh_threshold_ms(self): return self.get('rest_cache_ws_fresh_threshold_ms', 1500)
    @property
    def fx_cache_enabled(self): return self.get('fx_cache_enabled', True)
    @property
    def fx_cache_interval_sec(self): return self.get('fx_cache_interval_sec', 60)
    @property
    def fx_cache_max_age_sec(self): return self.get('fx_cache_max_age_sec', 300)
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
    def emergency_require_fresh_quote(self): return self.get('emergency_require_fresh_quote', True)
    @property
    def emergency_one_attempt_per_plan(self): return self.get('emergency_one_attempt_per_plan', True)
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
    @property
    def bithumb_quote_cache_enabled(self): return self.get('bithumb_quote_cache_enabled', True)
    @property
    def bithumb_quote_cache_refresh_ms(self): return self.get('bithumb_quote_cache_refresh_ms', 700)
    @property
    def bithumb_quote_cache_stale_ms(self): return self.get('bithumb_quote_cache_stale_ms', 5000)
    @property
    def bithumb_quote_cache_grace_ms(self): return self.get('bithumb_quote_cache_grace_ms', 3000)
    @property
    def bithumb_quote_cache_allow_last_good_on_stale(self): return self.get('bithumb_quote_cache_allow_last_good_on_stale', True)
    @property
    def bithumb_quote_cache_max_failures(self): return self.get('bithumb_quote_cache_max_failures', 10)
    @property
    def skip_missing_bithumb_quotes(self): return self.get('skip_missing_bithumb_quotes', True)
    @property
    def use_dynamic_symbols(self): return self.get('use_dynamic_symbols', True)
    @property
    def dynamic_symbol_top_n(self): return self.get('dynamic_symbol_top_n', 20)
    @property
    def dynamic_symbol_refresh_sec(self): return self.get('dynamic_symbol_refresh_sec', 300)
    @property
    def market_scanner_cache_enabled(self): return self.get('market_scanner_cache_enabled', True)
    @property
    def market_scanner_cache_max_age_sec(self): return self.get('market_scanner_cache_max_age_sec', 3600)
    @property
    def market_scanner_startup_mode(self): return self.get('market_scanner_startup_mode', 'cache_first')
    @property
    def market_scanner_timeout_sec(self): return self.get('market_scanner_timeout_sec', 3.0)
    @property
    def market_scanner_background_refresh_on_start(self): return self.get('market_scanner_background_refresh_on_start', True)
    @property
    def min_24h_quote_volume_krw(self): return self.get('min_24h_quote_volume_krw', 1000000000)
    @property
    def symbol_blacklist(self): return self.get('symbol_blacklist', [])
    @property
    def exclude_stablecoin_symbols(self): return self.get('exclude_stablecoin_symbols', True)
    @property
    def runtime_store_enabled(self): return self.get('runtime_store_enabled', True)
    @property
    def runtime_snapshot_interval_sec(self): return self.get('runtime_snapshot_interval_sec', 3)
    @property
    def runtime_snapshot_max_failures(self): return self.get('runtime_snapshot_max_failures', 5)
    @property
    def use_dynamic_slippage(self): return self.get('use_dynamic_slippage', True)
    @property
    def base_slippage_bp(self): return self.get('base_slippage_bp', 5)
    @property
    def max_dynamic_slippage_bp(self): return self.get('max_dynamic_slippage_bp', 30)
    @property
    def depth_safety_multiplier(self): return self.get('depth_safety_multiplier', 2.0)
    @property
    def paper_latency_sim_enabled(self): return self.get('paper_latency_sim_enabled', True)
    @property
    def paper_upbit_latency_ms(self): return self.get('paper_upbit_latency_ms', 60)
    @property
    def paper_bithumb_latency_ms(self): return self.get('paper_bithumb_latency_ms', 60)
    @property
    def paper_binance_latency_ms(self): return self.get('paper_binance_latency_ms', 80)
    @property
    def paper_latency_jitter_ms(self): return self.get('paper_latency_jitter_ms', 30)
    @property
    def paper_slippage_stress_bp(self): return self.get('paper_slippage_stress_bp', 5)
    @property
    def quote_history_maxlen(self): return self.get('quote_history_maxlen', 120)
    @property
    def quote_history_lightweight_enabled(self): return self.get('quote_history_lightweight_enabled', True)
    @property
    def memory_telemetry_enabled(self): return self.get('memory_telemetry_enabled', True)

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
    def fees(self): return self.get('fees', {})
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
    def paper_min_hold_before_sl_sec(self): return self.get('paper_min_hold_before_sl_sec', 3)
    @property
    def paper_min_hold_before_timeout_sec(self): return self.get('paper_min_hold_before_timeout_sec', 1)
    @property
    def paper_initial_upbit_krw(self): return self.get('paper_initial_upbit_krw', 1000000)
    @property
    def paper_initial_binance_usdt(self): return self.get('paper_initial_binance_usdt', 700)
    @property
    def paper_initial_coin_qty(self): return self.get('paper_initial_coin_qty', {})
    @property
    def paper_auto_seed_inventory(self): return self.get('paper_auto_seed_inventory', True)
    @property
    def paper_auto_seed_active_symbols(self): return self.get('paper_auto_seed_active_symbols', True)
    @property
    def paper_seed_notional_krw_per_symbol(self): return self.get('paper_seed_notional_krw_per_symbol', 100000)
    @property
    def paper_seed_krw_per_venue(self): return self.get('paper_seed_krw_per_venue', 10000000)

    # Iceberg placeholder only. Split order execution is not implemented.
    @property
    def iceberg_enabled(self): return self.get('iceberg_enabled', False)
    @property
    def iceberg_execution_enabled(self): return self.get('iceberg_execution_enabled', False)
    @property
    def iceberg_min_order_krw(self): return self.get('iceberg_min_order_krw', 1000000)
    @property
    def iceberg_slice_count(self): return self.get('iceberg_slice_count', 3)
    @property
    def iceberg_slice_interval_ms(self): return self.get('iceberg_slice_interval_ms', 120)
    @property
    def iceberg_max_total_slippage_bp(self): return self.get('iceberg_max_total_slippage_bp', 20)

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
    def telemetry_percentile_interval_sec(self): return self.get('telemetry_percentile_interval_sec', 5)
    @property
    def session_summary_interval_sec(self): return self.get('session_summary_interval_sec', 3600)
    @property
    def max_log_file_mb(self): return self.get('max_log_file_mb', 20)
    @property
    def log_retention_days(self): return self.get('log_retention_days', 14)


cfg = Config()
