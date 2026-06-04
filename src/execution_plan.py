"""Safe execution plan model. Plans are inspectable but never executed here."""
from dataclasses import asdict, dataclass, field
import time


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
