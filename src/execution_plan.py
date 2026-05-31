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
    plan_id: str = ''
    direction_label: str = ''
    upbit_side: str = ''
    binance_side: str = ''
    order_krw: float = 0
    order_usdt: float = 0
    qty: float = 0
    quantity: float = 0
    binance_usdt: float = 0
    quote_timestamp: float = 0
    quote_age_ms: float = 0
    upbit_bid: float = 0
    upbit_ask: float = 0
    binance_bid: float = 0
    binance_ask: float = 0
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
