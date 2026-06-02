"""Latency-aware bounded-memory fill estimates for paper opportunities."""
import time


def _venue_latency_ms(venue, config):
    return float(getattr(config, f'paper_{venue}_latency_ms') + config.paper_latency_jitter_ms)


def _leg_venues(plan):
    direction = plan.get('best_direction')
    if direction == 'A':
        return 'binance', 'upbit'
    if direction == 'B':
        return 'upbit', 'binance'
    if direction == 'UPBIT_BITHUMB_A':
        return 'bithumb', 'upbit'
    return 'upbit', 'bithumb'


def _snapshot_for_latency(history, latency_ms, now):
    target_ts = now - latency_ms / 1000
    return next((
        row for row in reversed(history)
        if float(row.get('_ts', 0) or 0) <= target_ts
    ), None)


def _snapshot_price_krw(plan, snapshot, venue, side):
    if not snapshot:
        return 0.0
    price = float(snapshot.get(venue, {}).get('ask' if side == 'buy' else 'bid', 0) or 0)
    if venue == 'binance':
        price *= float(plan.get('krw_usdt', 0) or 0)
    return price


def simulate_paper_fill(plan, quote_history, cfg):
    history = list(quote_history or [])
    now = time.time()
    buy_venue, sell_venue = _leg_venues(plan)
    buy_latency_ms = _venue_latency_ms(buy_venue, cfg)
    sell_latency_ms = _venue_latency_ms(sell_venue, cfg)
    buy_snapshot = _snapshot_for_latency(history, buy_latency_ms, now)
    sell_snapshot = _snapshot_for_latency(history, sell_latency_ms, now)
    history_used = buy_snapshot is not None and sell_snapshot is not None
    dynamic_bp = float(plan.get('dynamic_slippage_bp', cfg.base_slippage_bp) or cfg.base_slippage_bp)
    stress_bp = 0.0 if history_used else float(cfg.paper_slippage_stress_bp)
    total_slippage = min(float(cfg.max_dynamic_slippage_bp), dynamic_bp + stress_bp)
    buy_base_price = (
        _snapshot_price_krw(plan, buy_snapshot, buy_venue, 'buy')
        or float(plan.get('selected_buy_price_krw', plan.get('fill_price_estimate', 0)) or 0)
    )
    sell_base_price = (
        _snapshot_price_krw(plan, sell_snapshot, sell_venue, 'sell')
        or float(plan.get('selected_sell_price_krw', 0) or 0)
    )
    fill_buy_price_krw = buy_base_price * (1 + total_slippage / 10000) if buy_base_price else 0.0
    fill_sell_price_krw = sell_base_price * (1 - total_slippage / 10000) if sell_base_price else 0.0
    fill_qty = float(
        plan.get('selected_qty', plan.get('effective_qty', plan.get('max_fillable_qty', 0))) or 0
    )
    fill_notional_krw = float(plan.get('selected_notional_krw', 0) or 0)
    if fill_notional_krw <= 0 and fill_buy_price_krw:
        fill_notional_krw = fill_qty * fill_buy_price_krw
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
        'fill_price': round(fill_buy_price_krw, 12),
        'fill_buy_price_krw': round(fill_buy_price_krw, 12),
        'fill_sell_price_krw': round(fill_sell_price_krw, 12),
        'fill_qty': fill_qty,
        'fill_notional_krw': round(fill_notional_krw, 2),
        'fee_estimate': round(fill_notional_krw * fee_bp / 10000, 2),
        'slippage_estimate_bp': round(total_slippage, 4),
        'latency_used_ms': max(buy_latency_ms, sell_latency_ms),
        'leg_latency_used_ms': {buy_venue: buy_latency_ms, sell_venue: sell_latency_ms},
        'upbit_latency_used_ms': _venue_latency_ms('upbit', cfg) if 'upbit' in (buy_venue, sell_venue) else 0.0,
        'binance_latency_used_ms': _venue_latency_ms('binance', cfg) if 'binance' in (buy_venue, sell_venue) else 0.0,
        'bithumb_latency_used_ms': _venue_latency_ms('bithumb', cfg) if 'bithumb' in (buy_venue, sell_venue) else 0.0,
        'buy_leg_latency_ms': buy_latency_ms,
        'sell_leg_latency_ms': sell_latency_ms,
        'buy_leg_snapshot_age_ms': round(max(0.0, now - float(buy_snapshot.get('_ts', 0) or 0)) * 1000, 2) if buy_snapshot else None,
        'sell_leg_snapshot_age_ms': round(max(0.0, now - float(sell_snapshot.get('_ts', 0) or 0)) * 1000, 2) if sell_snapshot else None,
        'leg_latency_model_used': 'per_leg',
        'fill_quality': 'PAPER_EDGE_PASS' if edge_pass else 'PAPER_EDGE_FAIL',
        'paper_edge_quality': 'PAPER_EDGE_PASS' if edge_pass else 'PAPER_EDGE_FAIL',
        'quote_history_used': history_used,
    }
