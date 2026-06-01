"""Latency-aware bounded-memory fill estimates for paper opportunities."""
import time


def _latency_for(pair_id, config):
    if pair_id == 'UPBIT_BITHUMB':
        return max(config.paper_upbit_latency_ms, config.paper_bithumb_latency_ms)
    return max(config.paper_upbit_latency_ms, config.paper_binance_latency_ms)


def _snapshot_fill_price(plan, snapshot):
    if not snapshot:
        return 0.0
    direction = plan.get('best_direction')
    if plan.get('pair_id') == 'UPBIT_BITHUMB':
        venue, key = ('bithumb', 'ask') if direction == 'UPBIT_BITHUMB_A' else ('upbit', 'ask')
    else:
        venue, key = ('binance', 'ask') if direction == 'A' else ('upbit', 'ask')
    return float(snapshot.get(venue, {}).get(key, 0) or 0)


def simulate_paper_fill(plan, quote_history, cfg):
    latency_ms = float(_latency_for(plan.get('pair_id'), cfg) + cfg.paper_latency_jitter_ms)
    target_ts = time.time() - latency_ms / 1000
    history = list(quote_history or [])
    snapshot = next((row for row in reversed(history) if float(row.get('_ts', 0) or 0) <= target_ts), None)
    history_used = snapshot is not None
    dynamic_bp = float(plan.get('dynamic_slippage_bp', cfg.base_slippage_bp) or cfg.base_slippage_bp)
    stress_bp = 0.0 if history_used else float(cfg.paper_slippage_stress_bp)
    total_slippage = min(float(cfg.max_dynamic_slippage_bp), dynamic_bp + stress_bp)
    base_price = _snapshot_fill_price(plan, snapshot) or float(plan.get('fill_price_estimate', 0) or 0)
    fill_price = base_price * (1 + total_slippage / 10000) if base_price else 0.0
    fill_qty = float(
        plan.get('selected_qty', plan.get('effective_qty', plan.get('max_fillable_qty', 0))) or 0
    )
    fill_notional_krw = float(plan.get('selected_notional_krw', 0) or 0)
    if fill_notional_krw <= 0 and fill_price:
        fill_notional_krw = fill_qty * fill_price
    if fill_notional_krw <= 0:
        fill_notional_krw = float(plan.get('order_krw_used', cfg.max_one_trade_krw) or 0)
    fee_bp = cfg.upbit_fee_bp + (
        cfg.bithumb_fee_bp if plan.get('pair_id') == 'UPBIT_BITHUMB' else cfg.binance_fee_bp
    )
    edge_pass = (
        plan.get('reason_no_trade') in ('', 'OK')
        and plan.get('liquidity_class') != 'LOW_DEPTH'
        and float(plan.get('best_net_surplus_bp', -9999) or -9999) >= cfg.min_net_surplus_bp
    )
    return {
        'fill_price': round(fill_price, 12),
        'fill_qty': fill_qty,
        'fill_notional_krw': round(fill_notional_krw, 2),
        'fee_estimate': round(fill_notional_krw * fee_bp / 10000, 2),
        'slippage_estimate_bp': round(total_slippage, 4),
        'latency_used_ms': latency_ms,
        'fill_quality': 'PAPER_EDGE_PASS' if edge_pass else 'PAPER_EDGE_FAIL',
        'paper_edge_quality': 'PAPER_EDGE_PASS' if edge_pass else 'PAPER_EDGE_FAIL',
        'quote_history_used': history_used,
    }
