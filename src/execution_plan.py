"""Safe execution plan model. Plans are inspectable but never executed here."""
from dataclasses import asdict, dataclass, field


@dataclass
class ExecutionPlan:
    symbol: str
    direction: str
    mode: str
    quote_available: bool
    inventory_sufficient: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    executable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
