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
    min_order_ok: bool = False
    risk_ok: bool = False
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
    upbit_bid: float = 0
    upbit_ask: float = 0
    binance_bid: float = 0
    binance_ask: float = 0
    upbit_expected_price: float = 0
    binance_expected_price: float = 0
    fx_rate: float = 0
    expected_net_profit_krw: float = 0
    best_net_surplus_bp: float = 0
    preflight_status: str = 'BLOCKED'
    created_at: float = field(default_factory=time.time)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    executable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
