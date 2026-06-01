"""Inspectable venue-pair catalog. Bithumb execution remains disabled."""
from dataclasses import asdict, dataclass

from config import cfg


@dataclass(frozen=True)
class VenuePair:
    pair_id: str
    left_venue: str
    right_venue: str
    quote_currency: str
    enabled: bool
    paper_enabled: bool
    tiny_live_enabled: bool
    live_enabled: bool
    strategy_type: str

    def to_dict(self) -> dict:
        return asdict(self)


def get_venue_pairs() -> list[VenuePair]:
    enabled = cfg.enabled_strategy_pairs
    return [
        VenuePair(
            'UPBIT_BINANCE', 'UPBIT', 'BINANCE', 'KRW/USDT',
            bool(enabled.get('UPBIT_BINANCE', True)), True,
            cfg.tiny_live_enabled, cfg.live_mode_enabled, 'CROSS_BORDER_SPOT',
        ),
        VenuePair(
            'BITHUMB_BINANCE', 'BITHUMB', 'BINANCE', 'KRW/USDT',
            bool(enabled.get('BITHUMB_BINANCE', False)), False,
            False, False, 'CROSS_BORDER_SPOT',
        ),
        VenuePair(
            'UPBIT_BITHUMB', 'UPBIT', 'BITHUMB', 'KRW',
            bool(enabled.get('UPBIT_BITHUMB', True)),
            cfg.upbit_bithumb_paper_enabled, cfg.upbit_bithumb_tiny_live_enabled,
            cfg.upbit_bithumb_live_enabled, 'DOMESTIC_KRW',
        ),
        VenuePair(
            'BINANCE_MAKER_DOMESTIC_TAKER', 'BINANCE', 'DOMESTIC', 'KRW/USDT',
            bool(enabled.get('BINANCE_MAKER_DOMESTIC_TAKER', False)), False,
            False, False, 'MAKER_TAKER',
        ),
    ]


def venue_pair_payload() -> list[dict]:
    return [pair.to_dict() for pair in get_venue_pairs()]
