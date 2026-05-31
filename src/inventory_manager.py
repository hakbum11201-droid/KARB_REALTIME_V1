"""
inventory_manager.py - config 기반 paper inventory 관리.
- Direction A: Upbit SELL (coin↓, KRW↑) / Binance BUY (USDT↓, coin↑)
- Direction B: Upbit BUY  (KRW↓, coin↑)  / Binance SELL (coin↓, USDT↑)
- paper 모드: config.yaml 초기값 기반 가상 잔고
- live inventory: 인터페이스만 유지 (미구현)
"""
import copy
from config import cfg


class InventoryManager:
    def __init__(self):
        # paper 초기 잔고 (config 기반)
        self._paper_upbit_krw:  float = float(cfg.paper_initial_upbit_krw)
        self._paper_binance_usdt: float = float(cfg.paper_initial_binance_usdt)
        self._paper_coin_qty: dict[str, float] = {
            sym: float(cfg.paper_initial_coin_qty.get(sym, 0))
            for sym in cfg.symbols
        }

    # ──────────────────────────────────────────────────────────────────────
    # Paper 잔고 조회
    # ──────────────────────────────────────────────────────────────────────

    def paper_snapshot(self) -> dict:
        """현재 paper 잔고 스냅샷 반환."""
        return {
            'upbit_krw':     self._paper_upbit_krw,
            'binance_usdt':  self._paper_binance_usdt,
            'coin_qty':      copy.copy(self._paper_coin_qty),
        }

    # ──────────────────────────────────────────────────────────────────────
    # 진입 가능 여부 확인
    # ──────────────────────────────────────────────────────────────────────

    def check_paper_entry(
        self, symbol: str, direction: str, qty: float, krw_usdt: float,
        upbit_ask: float, binance_ask: float,
    ) -> str:
        """
        반환:
          'OK'                 – 진입 가능
          'INVENTORY_SHORTAGE' – 잔고 부족
        """
        if direction == 'A':
            # Upbit SELL (coin qty 필요) + Binance BUY (USDT 필요)
            need_coin  = qty
            need_usdt  = qty * binance_ask
            have_coin  = self._paper_coin_qty.get(symbol, 0.0)
            have_usdt  = self._paper_binance_usdt
            if have_coin < need_coin or have_usdt < need_usdt:
                return 'INVENTORY_SHORTAGE'

        elif direction == 'B':
            # Upbit BUY (KRW 필요) + Binance SELL (coin qty 필요)
            need_krw   = qty * upbit_ask
            need_coin  = qty
            have_krw   = self._paper_upbit_krw
            have_coin  = self._paper_coin_qty.get(symbol, 0.0)
            if have_krw < need_krw or have_coin < need_coin:
                return 'INVENTORY_SHORTAGE'

        return 'OK'

    # ──────────────────────────────────────────────────────────────────────
    # Entry / Exit 가상 정산
    # ──────────────────────────────────────────────────────────────────────

    def apply_paper_entry(
        self, symbol: str, direction: str, qty: float, krw_usdt: float,
        upbit_ask: float, binance_ask: float,
    ) -> None:
        """진입 시 paper 잔고를 가상 차감한다."""
        if direction == 'A':
            self._paper_coin_qty[symbol] = self._paper_coin_qty.get(symbol, 0.0) - qty
            self._paper_binance_usdt    -= qty * binance_ask
        elif direction == 'B':
            self._paper_upbit_krw                        -= qty * upbit_ask
            self._paper_coin_qty[symbol] = self._paper_coin_qty.get(symbol, 0.0) - qty

    def apply_paper_exit(
        self, symbol: str, direction: str, qty: float,
        realized_pnl_krw: float, krw_usdt: float,
        exit_upbit_bid: float, exit_binance_bid: float,
    ) -> None:
        """
        청산 시 paper 잔고를 가상 복구/정산한다.
        순익 realized_pnl_krw를 KRW 잔고에 반영한다.
        """
        if direction == 'A':
            # Binance에서 산 코인 복구, Upbit 매도 KRW 입금
            self._paper_coin_qty[symbol] = self._paper_coin_qty.get(symbol, 0.0) + qty
            self._paper_upbit_krw       += qty * exit_upbit_bid
        elif direction == 'B':
            # Upbit에서 산 코인 복구, Binance 매도 USDT 입금
            self._paper_coin_qty[symbol] = self._paper_coin_qty.get(symbol, 0.0) + qty
            self._paper_binance_usdt    += qty * exit_binance_bid

        # 순수익(양수)/손실(음수) 반영
        self._paper_upbit_krw += realized_pnl_krw

    # ──────────────────────────────────────────────────────────────────────
    # Live interface (미구현)
    # ──────────────────────────────────────────────────────────────────────

    def fetch_live_balance(self, exchange: str) -> dict:
        """실제 잔고 조회 – 미구현. live 모드 전용."""
        raise NotImplementedError("fetch_live_balance: live mode not yet implemented.")

    def check_balance(self, exchange: str, asset: str, required_amount: float) -> bool:
        """하위 호환 인터페이스 유지."""
        if exchange == 'upbit':
            if asset == 'KRW':
                return self._paper_upbit_krw >= required_amount
            return self._paper_coin_qty.get(asset, 0.0) >= required_amount
        elif exchange == 'binance':
            if asset == 'USDT':
                return self._paper_binance_usdt >= required_amount
            return self._paper_coin_qty.get(asset, 0.0) >= required_amount
        return False
