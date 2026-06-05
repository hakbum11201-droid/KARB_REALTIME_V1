"""Safe execution plan model. Plans are inspectable but never executed here."""
from dataclasses import asdict, dataclass, field
import time

from config import cfg


VENUE_ALIASES = {
    'UPBIT': 'UPBIT',
    'BITHUMB': 'BITHUMB',
    'BINANCE': 'BINANCE',
}


class FeeModel:
    def __init__(self, config=cfg):
        self.config = config

    def fee_bp(self, venue: str, role='taker') -> tuple[float, str]:
        venue_key = VENUE_ALIASES.get(str(venue or '').upper(), str(venue or '').upper())
        fees = self.config.get('fees', {}) or {}
        row = fees.get(venue_key, {}) if isinstance(fees, dict) else {}
        key = 'maker_fee_bp' if str(role).lower() == 'maker' else 'taker_fee_bp'
        if key in row:
            return float(row.get(key) or 0), 'CONFIG'
        fallback = {
            'UPBIT': self.config.upbit_fee_bp,
            'BITHUMB': self.config.bithumb_fee_bp,
            'BINANCE': self.config.binance_fee_bp,
        }.get(venue_key, 0.0)
        return float(fallback or 0), 'FALLBACK'

    def leg_fee(self, venue: str, notional_krw: float, role='taker') -> dict:
        bp, source = self.fee_bp(venue, role)
        fee = max(0.0, float(notional_krw or 0)) * bp / 10000
        return {'venue': venue, 'fee_bp': bp, 'fee_krw': fee, 'fee_source': source}


def _levels(orderbook: dict, side: str) -> list[tuple[float, float]]:
    key = 'asks' if str(side).upper() == 'BUY' else 'bids'
    levels = orderbook.get(key) or orderbook.get('levels', {}).get(key) or []
    out = []
    for row in levels:
        try:
            if isinstance(row, dict):
                price = float(row.get('price', row.get('ask_price', row.get('bid_price', 0))) or 0)
                qty = float(row.get('qty', row.get('size', row.get('ask_size', row.get('bid_size', 0)))) or 0)
            else:
                price = float(row[0])
                qty = float(row[1])
            if price > 0 and qty > 0:
                out.append((price, qty))
        except Exception:
            continue
    if out:
        return out
    price_key = 'ask' if str(side).upper() == 'BUY' else 'bid'
    size_key = 'ask_size' if str(side).upper() == 'BUY' else 'bid_size'
    try:
        price = float(orderbook.get(price_key, 0) or 0)
        qty = float(orderbook.get(size_key, 0) or 0)
    except (TypeError, ValueError):
        price, qty = 0.0, 0.0
    return [(price, qty)] if price > 0 and qty > 0 else []


def estimate_market_fill(side: str, qty: float, orderbook_levels) -> dict:
    side = str(side or '').upper()
    requested_qty = max(0.0, float(qty or 0))
    levels = _levels({'asks': orderbook_levels, 'bids': orderbook_levels}, 'BUY') if isinstance(orderbook_levels, list) else _levels(orderbook_levels or {}, side)
    top_price = levels[0][0] if levels else 0.0
    remaining = requested_qty
    filled_qty = 0.0
    notional = 0.0
    used = 0
    for price, level_qty in levels:
        if remaining <= 1e-12:
            break
        take = min(remaining, level_qty)
        filled_qty += take
        notional += take * price
        remaining -= take
        used += 1
    vwap = notional / filled_qty if filled_qty > 0 else 0.0
    fill_ratio = filled_qty / requested_qty if requested_qty > 0 else 0.0
    if top_price > 0 and vwap > 0:
        slip = (
            (vwap - top_price) / top_price * 10000
            if side == 'BUY' else (top_price - vwap) / top_price * 10000
        )
    else:
        slip = 0.0
    slippage_bp = max(0.0, slip)
    return {
        'ok': requested_qty > 0 and fill_ratio >= 1.0,
        'requested_qty': requested_qty,
        'filled_qty': filled_qty,
        'fill_ratio': fill_ratio,
        'vwap_price': vwap,
        'top_price': top_price,
        'slippage_bp': slippage_bp,
        'notional_krw': notional,
        'depth_levels_used': used,
        'insufficient_depth': fill_ratio < 1.0,
    }


def _venue_book(signal: dict, venue: str) -> dict:
    key = str(venue or '').lower()
    aliases = {'bithumb': 'bithumb', 'binance': 'binance', 'upbit': 'upbit'}
    name = aliases.get(key, key)
    return (
        signal.get(f'{name}_orderbook')
        or signal.get(f'{name}_quote')
        or {
            'bid': signal.get(f'{name}_bid', 0),
            'ask': signal.get(f'{name}_ask', 0),
            'bid_size': signal.get(f'{name}_bid_size', signal.get('max_fillable_qty_raw', signal.get('selected_qty', 0))),
            'ask_size': signal.get(f'{name}_ask_size', signal.get('max_fillable_qty_raw', signal.get('selected_qty', 0))),
            'bids': signal.get(f'{name}_bids', []),
            'asks': signal.get(f'{name}_asks', []),
        }
    )


def _direction_venues(signal: dict) -> tuple[str, str]:
    pair_id = signal.get('pair_id', 'UPBIT_BINANCE')
    direction = signal.get('best_direction') or signal.get('direction', '')
    if pair_id == 'UPBIT_BITHUMB':
        return ('BITHUMB', 'UPBIT') if direction == 'UPBIT_BITHUMB_A' else ('UPBIT', 'BITHUMB')
    return ('BINANCE', 'UPBIT') if direction == 'A' else ('UPBIT', 'BINANCE')


def _krw_price(price: float, venue: str, signal: dict) -> float:
    price = float(price or 0)
    if str(venue).upper() == 'BINANCE':
        return price * float(signal.get('krw_usdt', 0) or 0)
    return price


def build_execution_plan(signal: dict, mode: str, config=cfg) -> dict:
    pair_id = signal.get('pair_id', 'UPBIT_BINANCE')
    direction = signal.get('best_direction') or signal.get('direction', '')
    buy_venue, sell_venue = _direction_venues(signal)
    qty = float(signal.get('selected_qty', signal.get('effective_qty', signal.get('max_fillable_qty', 0))) or 0)
    min_fill_ratio = float(signal.get('min_fill_ratio', config.min_fill_ratio) or config.min_fill_ratio)
    buy_book = _venue_book(signal, buy_venue)
    sell_book = _venue_book(signal, sell_venue)
    buy_fill = estimate_market_fill('BUY', qty, buy_book)
    sell_fill = estimate_market_fill('SELL', qty, sell_book)
    buy_vwap = _krw_price(buy_fill['vwap_price'], buy_venue, signal)
    sell_vwap = _krw_price(sell_fill['vwap_price'], sell_venue, signal)
    buy_top = _krw_price(buy_fill['top_price'], buy_venue, signal)
    sell_top = _krw_price(sell_fill['top_price'], sell_venue, signal)
    selected_notional = float(signal.get('selected_notional_krw', 0) or buy_vwap * qty)
    fee_model = FeeModel(config)
    buy_fee = fee_model.leg_fee(buy_venue, selected_notional)
    sell_fee = fee_model.leg_fee(sell_venue, selected_notional)
    fee_source = 'CONFIG' if buy_fee['fee_source'] == sell_fee['fee_source'] == 'CONFIG' else (
        'FALLBACK' if 'FALLBACK' in (buy_fee['fee_source'], sell_fee['fee_source']) else buy_fee['fee_source']
    )
    total_fee = buy_fee['fee_krw'] + sell_fee['fee_krw']
    total_slip_bp = max(0.0, buy_fill['slippage_bp']) + max(0.0, sell_fill['slippage_bp'])
    slippage_cost = selected_notional * total_slip_bp / 10000
    gross_edge = (sell_vwap - buy_vwap) * qty
    expected_net = gross_edge - total_fee - slippage_cost
    expected_net_bp = expected_net / selected_notional * 10000 if selected_notional else 0.0
    depth_ok = (
        buy_fill['fill_ratio'] >= min_fill_ratio
        and sell_fill['fill_ratio'] >= min_fill_ratio
    )
    blockers = []
    if qty <= 0:
        blockers.append('PLAN_QTY_INVALID')
    if buy_vwap <= 0 or sell_vwap <= 0:
        blockers.append('PLAN_PRICE_INVALID')
    if not depth_ok:
        blockers.append('DEPTH_INSUFFICIENT')
    if expected_net <= 0:
        blockers.append('PLAN_NET_NOT_POSITIVE')
    plan_ok = not blockers
    planned = {
        **signal,
        'pair_id': pair_id,
        'symbol': signal.get('symbol', ''),
        'direction': direction,
        'mode': mode,
        'buy_venue': buy_venue,
        'sell_venue': sell_venue,
        'buy_side': 'BUY',
        'sell_side': 'SELL',
        'buy_qty': qty,
        'sell_qty': qty,
        'selected_qty': qty,
        'qty': qty,
        'normalized_qty': qty,
        'quantity': qty,
        'selected_notional_krw': selected_notional,
        'order_krw': selected_notional,
        'order_krw_used': selected_notional,
        'order_usdt': selected_notional / float(signal.get('krw_usdt', 0) or 1),
        'binance_usdt': selected_notional / float(signal.get('krw_usdt', 0) or 1),
        'buy_top_price': buy_top,
        'sell_top_price': sell_top,
        'buy_vwap_price': buy_vwap,
        'sell_vwap_price': sell_vwap,
        'selected_buy_price_krw': buy_vwap,
        'selected_sell_price_krw': sell_vwap,
        'buy_price': buy_vwap,
        'sell_price': sell_vwap,
        'buy_slippage_bp': buy_fill['slippage_bp'],
        'sell_slippage_bp': sell_fill['slippage_bp'],
        'total_slippage_bp': total_slip_bp,
        'dynamic_slippage_bp': total_slip_bp,
        'expected_slippage_bp': total_slip_bp,
        'slippage_cost_krw': slippage_cost,
        'buy_fee_krw': buy_fee['fee_krw'],
        'sell_fee_krw': sell_fee['fee_krw'],
        'total_fee_krw': total_fee,
        'expected_fee_krw': total_fee,
        'buy_fee_bp': buy_fee['fee_bp'],
        'sell_fee_bp': sell_fee['fee_bp'],
        'fee_source': fee_source,
        'gross_edge_krw': gross_edge,
        'gross_gap_krw': gross_edge,
        'expected_net_profit_krw': expected_net,
        'net_expected_profit_krw': expected_net,
        'entry_net_expected_profit_krw': expected_net,
        'expected_net_bp': expected_net_bp,
        'best_net_surplus_bp': expected_net_bp,
        'entry_surplus_bp': expected_net_bp,
        'min_fill_ratio': min_fill_ratio,
        'expected_fill_ratio_buy': buy_fill['fill_ratio'],
        'expected_fill_ratio_sell': sell_fill['fill_ratio'],
        'fill_ratio_buy': buy_fill['fill_ratio'],
        'fill_ratio_sell': sell_fill['fill_ratio'],
        'depth_ok': depth_ok,
        'depth_levels_used_buy': buy_fill['depth_levels_used'],
        'depth_levels_used_sell': sell_fill['depth_levels_used'],
        'slippage_source': 'ORDERBOOK_VWAP',
        'quote_age_ms': signal.get('entry_quote_age_ms', signal.get('max_leg_quote_age_ms')),
        'quote_age_cap_ms': signal.get('entry_quote_age_cap_ms'),
        'plan_ok': plan_ok,
        'blocker': blockers[0] if blockers else '',
        'execution_plan_blockers': blockers,
        'planned_buy_vwap_price': buy_vwap,
        'planned_sell_vwap_price': sell_vwap,
        'planned_total_fee_krw': total_fee,
        'planned_slippage_cost_krw': slippage_cost,
        'planned_expected_net_profit_krw': expected_net,
        'actual_buy_avg_price': buy_vwap if mode == 'paper' else None,
        'actual_sell_avg_price': sell_vwap if mode == 'paper' else None,
        'actual_fee_krw': total_fee if mode == 'paper' else None,
        'actual_realized_pnl_krw': expected_net if mode == 'paper' else None,
        'pnl_diff_krw': 0.0 if mode == 'paper' else None,
        'execution_latency_ms': None,
        'submit_started_at': None,
        'submit_finished_at': None,
    }
    return planned


@dataclass
class ExecutionPlan:
    symbol: str
    direction: str
    mode: str
    quote_available: bool
    inventory_sufficient: bool
    pair_id: str = 'UPBIT_BINANCE'
    left_venue: str = 'UPBIT'
    right_venue: str = 'BINANCE'
    domestic_only: bool = False
    fx_required: bool = True
    strategy_type: str = 'CROSS_BORDER_SPOT'
    left_side: str = ''
    right_side: str = ''
    left_order_type: str = 'MARKET'
    right_order_type: str = 'MARKET'
    left_expected_price: float = 0
    right_expected_price: float = 0
    quote_source: str = ''
    entry_reason: str = ''
    min_order_ok: bool = False
    risk_ok: bool = False
    iceberg_required: bool = False
    iceberg_enabled: bool = False
    iceberg_execution_enabled: bool = False
    iceberg_slice_count: int = 0
    iceberg_warning: str = ''
    plan_id: str = ''
    direction_label: str = ''
    upbit_side: str = ''
    binance_side: str = ''
    order_krw: float = 0
    order_usdt: float = 0
    qty: float = 0
    normalized_qty: float = 0
    quantity: float = 0
    binance_usdt: float = 0
    quote_timestamp: float = 0
    quote_age_ms: float = 0
    upbit_quote_age_ms: float | None = None
    binance_quote_age_ms: float | None = None
    bithumb_quote_age_ms: float | None = None
    buy_leg_quote_age_ms: float | None = None
    sell_leg_quote_age_ms: float | None = None
    entry_quote_age_ms: float | None = None
    entry_refreshed_at: float | None = None
    entry_fetch_ms: float | None = None
    entry_decision_wait_ms: float | None = None
    max_leg_quote_age_ms: float | None = None
    uses_stale_grace_quote: bool = False
    has_stale_quote: bool = False
    live_freshness_ok: bool = False
    tiny_live_freshness_ok: bool = False
    live_freshness_blockers: list[str] = field(default_factory=list)
    tiny_live_freshness_blockers: list[str] = field(default_factory=list)
    live_watchlist_ok: bool = False
    upbit_bid: float = 0
    upbit_ask: float = 0
    binance_bid: float = 0
    binance_ask: float = 0
    upbit_expected_price: float = 0
    binance_expected_price: float = 0
    fx_rate: float = 0
    expected_net_profit_krw: float = 0
    entry_net_expected_profit_krw: float = 0
    best_net_surplus_bp: float = 0
    entry_surplus_bp: float = 0
    raw_depth_qty: float = 0
    selected_required_assets: dict = field(default_factory=dict)
    order_krw_used: float = 0
    effective_qty: float = 0
    buy_venue: str = ''
    sell_venue: str = ''
    buy_price: float = 0
    sell_price: float = 0
    expected_slippage_bp: float = 0
    expected_fee_krw: float = 0
    recheck_status: str = ''
    wide_spread_recheck_status: str = ''
    max_fillable_qty_raw: float = 0
    selected_notional_krw: float = 0
    selected_qty: float = 0
    selected_buy_price_krw: float = 0
    selected_sell_price_krw: float = 0
    notional_basis: str = ''
    preflight_status: str = 'BLOCKED'
    created_at: float = field(default_factory=time.time)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    executable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
