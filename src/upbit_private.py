from exchange_base import ExchangeBase


class UpbitPrivate(ExchangeBase):
    """
    Upbit Private API.
    tiny_live / live 모드에서만 인스턴스화한다.
    paper 모드에서 이 클래스의 주문 함수를 호출하면 안 된다.
    """

    def __init__(self, access_key: str, secret_key: str):
        super().__init__("UpbitPrivate")
        self._access_key = access_key
        self._secret_key = secret_key
        # 키 값은 절대 print/log 하지 않는다.

    def fetch_balance(self) -> dict:
        """실제 Upbit 잔고 조회 (미구현 - 추후 JWT 인증 추가)."""
        raise NotImplementedError("Upbit private balance not yet implemented.")

    def create_order(self, symbol: str, side: str, amount: float,
                     price: float | None = None, order_type: str = 'limit') -> dict:
        """실제 Upbit 주문 생성 (미구현)."""
        raise NotImplementedError("Upbit private order not yet implemented.")
