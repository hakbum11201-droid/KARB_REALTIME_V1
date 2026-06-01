"""Conservative depth-aware slippage estimates for paper evaluation."""
from config import cfg


def _levels(orderbook, side):
    key = 'asks' if str(side).upper() == 'BUY' else 'bids'
    levels = orderbook.get(key) or orderbook.get('levels', {}).get(key) or []
    if levels:
        return [
            (float(row.get('price', 0)), float(row.get('qty', row.get('size', 0))))
            if isinstance(row, dict) else (float(row[0]), float(row[1]))
            for row in levels
        ]
    price_key = 'ask' if str(side).upper() == 'BUY' else 'bid'
    size_key = 'ask_size' if str(side).upper() == 'BUY' else 'bid_size'
    price, qty = float(orderbook.get(price_key, 0) or 0), float(orderbook.get(size_key, 0) or 0)
    return [(price, qty)] if price > 0 and qty > 0 else []


def estimate_depth_available(orderbook, side, max_levels=15):
    return round(sum(price * qty for price, qty in _levels(orderbook, side)[:max_levels]), 2)


def estimate_fill_price(orderbook, side, order_krw):
    remaining = float(order_krw)
    filled_qty = 0.0
    spent = 0.0
    levels = _levels(orderbook, side)[:15]
    for price, qty in levels:
        take_krw = min(remaining, price * qty)
        if take_krw <= 0:
            continue
        filled_qty += take_krw / price
        spent += take_krw
        remaining -= take_krw
        if remaining <= 1e-9:
            break
    return round(spent / filled_qty, 12) if filled_qty else 0.0


def classify_liquidity(orderbook, order_krw, side='BUY'):
    depth = estimate_depth_available(orderbook, side)
    if depth < float(order_krw):
        return 'LOW_DEPTH'
    ratio = depth / max(float(order_krw), 1.0)
    if ratio >= 5:
        return 'GOOD'
    if ratio >= 2:
        return 'NORMAL'
    return 'THIN'


def estimate_slippage_bp(orderbook, side, order_krw, base_slippage_bp):
    base = float(base_slippage_bp)
    levels = _levels(orderbook, side)
    depth = estimate_depth_available(orderbook, side)
    best_key = 'ask' if str(side).upper() == 'BUY' else 'bid'
    best = float(orderbook.get(best_key, 0) or (levels[0][0] if levels else 0))
    fill_price = estimate_fill_price(orderbook, side, order_krw)
    model_used = 'depth' if len(levels) > 1 else 'fallback'
    if best <= 0 or not fill_price:
        dynamic = float(cfg.max_dynamic_slippage_bp)
        liquidity = 'LOW_DEPTH'
    else:
        move = (
            (fill_price - best) / best * 10000
            if str(side).upper() == 'BUY'
            else (best - fill_price) / best * 10000
        )
        ratio = float(order_krw) / max(depth, 1.0)
        dynamic = max(base, move + base * max(1.0, ratio * cfg.depth_safety_multiplier))
        dynamic = min(float(cfg.max_dynamic_slippage_bp), dynamic)
        liquidity = classify_liquidity(orderbook, order_krw, side)
    return {
        'dynamic_slippage_bp': round(dynamic, 4),
        'fill_price_estimate': fill_price or best,
        'depth_available_krw': depth,
        'liquidity_class': liquidity,
        'model_used': model_used,
    }
