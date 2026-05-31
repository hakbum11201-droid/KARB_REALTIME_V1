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

    def inventory_summary(self, quotes: dict | None = None, mode: str = 'paper') -> dict:
        """Return display-only inventory readiness. Never moves assets."""
        quotes = quotes or {}
        if mode != 'paper':
            from exchange_clients import BinanceSpotPrivateClient, UpbitPrivateClient
            upbit = UpbitPrivateClient().get_balances()
            binance = BinanceSpotPrivateClient().get_balances()
            blockers = list(upbit['blockers']) + list(binance['blockers'])
            return {
                'mode': mode,
                'source': 'read_only_private_clients',
                'ok': False,
                'blockers': blockers,
                'manual_rebalance_only': True,
                'balances': {'upbit': upbit['balances'], 'binance': binance['balances']},
                'symbols': [],
            }

        snap = self.paper_snapshot()
        symbols = []
        for symbol in cfg.symbols:
            quote = quotes.get(symbol, {})
            upbit = quote.get('upbit', {})
            binance = quote.get('binance', {})
            calc = quote.get('calc', {})
            qty = float(calc.get('max_fillable_qty', 0) or 0)
            upbit_krw = float(snap['upbit_krw'])
            binance_usdt = float(snap['binance_usdt'])
            upbit_coin = float(snap['coin_qty'].get(symbol, 0))
            binance_coin = float(snap['coin_qty'].get(symbol, 0))
            need_usdt = qty * float(binance.get('ask', 0) or 0)
            need_krw = qty * float(upbit.get('ask', 0) or 0)
            direction_a_possible = qty > 0 and upbit_coin >= qty and binance_usdt >= need_usdt
            direction_b_possible = qty > 0 and upbit_krw >= need_krw and binance_coin >= qty
            missing_for_a = []
            missing_for_b = []
            if upbit_coin < qty:
                missing_for_a.append(f'Upbit {symbol}')
            if binance_usdt < need_usdt:
                missing_for_a.append('Binance USDT')
            if upbit_krw < need_krw:
                missing_for_b.append('Upbit KRW')
            if binance_coin < qty:
                missing_for_b.append(f'Binance {symbol}')
            missing = missing_for_a + missing_for_b
            action = (
                'Inventory ready for both directions.'
                if not missing else
                'Manual rebalance required: hold or buy ' + ', '.join(sorted(set(missing))) + '.'
            )
            symbols.append({
                'symbol': symbol,
                'upbit_coin_qty': upbit_coin,
                'binance_coin_qty': binance_coin,
                'upbit_krw_available': upbit_krw,
                'binance_usdt_available': binance_usdt,
                'direction_a_possible': direction_a_possible,
                'direction_b_possible': direction_b_possible,
                'missing_for_a': missing_for_a,
                'missing_for_b': missing_for_b,
                'recommended_manual_action': action,
                'status': 'OK' if direction_a_possible or direction_b_possible else 'INVENTORY_SHORTAGE',
            })
        return {
            'mode': mode,
            'source': 'paper_config_inventory',
            'ok': True,
            'blockers': [],
            'manual_rebalance_only': True,
            'balances': {
                'upbit': {'KRW': snap['upbit_krw'], **snap['coin_qty']},
                'binance': {'USDT': snap['binance_usdt'], **snap['coin_qty']},
            },
            'symbols': symbols,
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
