class ExchangeBase:
    """CCXT식 exchange abstraction 기반 클래스."""
    def __init__(self, name: str):
        self.name = name

    def fetch_order_book(self, symbol: str) -> dict | None:
        """최우선 호가 1단계 반환: {bid, ask, bid_size, ask_size}"""
        raise NotImplementedError

    def fetch_balance(self) -> dict:
        """잔고 반환: {'KRW': float, 'USDT': float, symbol: float, ...}"""
        raise NotImplementedError

    def create_order(self, symbol: str, side: str, amount: float,
                     price: float | None = None, order_type: str = 'limit') -> dict:
        """주문 생성. tiny_live/live 전용. paper 모드에서 호출 금지."""
        raise NotImplementedError
