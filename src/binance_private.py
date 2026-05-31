from exchange_base import ExchangeBase


class BinancePrivate(ExchangeBase):
    """
    Binance Private API.
    tiny_live / live 모드에서만 인스턴스화한다.
    paper 모드에서 이 클래스의 주문 함수를 호출하면 안 된다.
    """

    def __init__(self, api_key: str, api_secret: str):
        super().__init__("BinancePrivate")
        self._api_key = api_key
        self._api_secret = api_secret
        # 키 값은 절대 print/log 하지 않는다.

    def fetch_balance(self) -> dict:
        """실제 Binance 잔고 조회 (미구현 - 추후 HMAC 인증 추가)."""
        raise NotImplementedError("Binance private balance not yet implemented.")

    def create_order(self, symbol: str, side: str, amount: float,
                     price: float | None = None, order_type: str = 'limit') -> dict:
        """실제 Binance 주문 생성 (미구현)."""
        raise NotImplementedError("Binance private order not yet implemented.")
