"""
inventory_manager.py - config 기반 paper inventory 관리.
- Direction A: Upbit SELL (coin↓, KRW↑) / Binance BUY (USDT↓, coin↑)
- Direction B: Upbit BUY  (KRW↓, coin↑)  / Binance SELL (coin↓, USDT↑)
- paper 모드: config.yaml 초기값 기반 가상 잔고
- live inventory: 인터페이스만 유지 (미구현)
"""
import copy
import threading
from functools import wraps
from config import cfg


def _inventory_locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class InventoryManager:
    def __init__(self):
        self._lock = threading.RLock()
        # paper 초기 잔고 (config 기반)
        initial_coins = {
            sym: float(cfg.paper_initial_coin_qty.get(sym, 0))
            for sym in cfg.symbols
        }
        self._paper_inventory = {
            'UPBIT': {'KRW': float(cfg.paper_initial_upbit_krw), 'coins': copy.copy(initial_coins)},
            'BINANCE': {'USDT': float(cfg.paper_initial_binance_usdt), 'coins': copy.copy(initial_coins)},
            'BITHUMB': {'KRW': float(cfg.paper_initial_upbit_krw), 'coins': copy.copy(initial_coins)},
        }

    # ──────────────────────────────────────────────────────────────────────
    # Paper 잔고 조회
    # ──────────────────────────────────────────────────────────────────────

    @_inventory_locked
    def paper_snapshot(self) -> dict:
        """현재 paper 잔고 스냅샷 반환."""
        venues = copy.deepcopy(self._paper_inventory)
        return {
            'venues': venues,
            'upbit_krw': venues['UPBIT']['KRW'],
            'binance_usdt': venues['BINANCE']['USDT'],
            'coin_qty': copy.copy(venues['UPBIT']['coins']),
        }

    def inventory_summary(self, quotes: dict | None = None, mode: str = 'paper') -> dict:
        """Return display-only inventory readiness. Never moves assets."""
        quotes = quotes or {}
        if mode != 'paper':
            from exchange_clients import BinanceSpotPrivateClient, UpbitPrivateClient
            upbit = UpbitPrivateClient().get_balances()
            binance = BinanceSpotPrivateClient().get_balances()
            blockers = list(upbit['blockers']) + list(binance['blockers'])
            if blockers:
                return {
                    'mode': mode,
                    'source': 'read_only_private_clients',
                    'ok': False,
                    'blockers': blockers,
                    'manual_rebalance_only': True,
                    'balances': {'upbit': upbit['balances'], 'binance': binance['balances']},
                    'symbols': [],
                }
            source = 'read_only_private_clients'
            snap = {
                'upbit_krw': upbit['balances'].get('KRW', 0),
                'binance_usdt': binance['balances'].get('USDT', 0),
                'upbit_coin_qty': upbit['balances'],
                'binance_coin_qty': binance['balances'],
            }
        else:
            source = 'paper_config_inventory'
            paper = self.paper_snapshot()
            snap = {
                'upbit_krw': paper['upbit_krw'],
                'binance_usdt': paper['binance_usdt'],
                'upbit_coin_qty': paper['venues']['UPBIT']['coins'],
                'binance_coin_qty': paper['venues']['BINANCE']['coins'],
            }
        symbols = []
        trade_krw = float(min(cfg.max_one_trade_krw, cfg.max_position_krw))
        for symbol in cfg.symbols:
            quote = quotes.get(symbol, {})
            upbit = quote.get('upbit', {})
            binance = quote.get('binance', {})
            calc = quote.get('calc', {})
            upbit_krw = float(snap['upbit_krw'])
            binance_usdt = float(snap['binance_usdt'])
            upbit_coin = float(snap['upbit_coin_qty'].get(symbol, 0))
            binance_coin = float(snap['binance_coin_qty'].get(symbol, 0))
            upbit_bid = float(upbit.get('bid', 0) or 0)
            binance_bid = float(binance.get('bid', 0) or 0)
            krw_usdt = float(calc.get('krw_usdt', 0) or 0)
            direction_a_assets = calc.get('direction_a_required_assets') or {}
            direction_b_assets = calc.get('direction_b_required_assets') or {}
            selected_required_assets = calc.get('selected_required_assets') or {}

            required_upbit_coin_for_a = None
            required_binance_usdt_for_a = None
            required_upbit_krw_for_b = trade_krw
            required_binance_coin_for_b = None
            blockers = []
            if not quote:
                blockers.append('QUOTE_UNAVAILABLE')
            elif upbit_bid <= 0 or binance_bid <= 0 or krw_usdt <= 0:
                blockers.append('PRICE_UNAVAILABLE')

            if blockers:
                blocker = blockers[0]
                symbols.append({
                    'symbol': symbol,
                    'required_trade_krw': trade_krw,
                    'required_upbit_coin_for_a': required_upbit_coin_for_a,
                    'required_binance_usdt_for_a': required_binance_usdt_for_a,
                    'required_upbit_krw_for_b': required_upbit_krw_for_b,
                    'required_binance_coin_for_b': required_binance_coin_for_b,
                    'upbit_coin_qty': upbit_coin,
                    'binance_coin_qty': binance_coin,
                    'upbit_krw_available': upbit_krw,
                    'binance_usdt_available': binance_usdt,
                    'direction_a_possible': False,
                    'direction_b_possible': False,
                    'missing_for_a': [blocker],
                    'missing_for_b': [blocker],
                    'recommended_manual_action': 'Wait for valid quote data before evaluating inventory.',
                    'status': blocker,
                    'blockers': blockers,
                })
                continue

            required_upbit_coin_for_a = float(
                direction_a_assets.get('upbit_coin_qty', trade_krw / upbit_bid) or 0
            )
            required_binance_usdt_for_a = float(
                direction_a_assets.get('binance_usdt', trade_krw / krw_usdt) or 0
            )
            required_upbit_krw_for_b = float(
                direction_b_assets.get('upbit_krw', trade_krw) or 0
            )
            required_binance_coin_for_b = float(
                direction_b_assets.get('binance_coin_qty', trade_krw / (binance_bid * krw_usdt)) or 0
            )
            direction_a_possible = (
                upbit_coin >= required_upbit_coin_for_a
                and binance_usdt >= required_binance_usdt_for_a
            )
            direction_b_possible = (
                upbit_krw >= required_upbit_krw_for_b
                and binance_coin >= required_binance_coin_for_b
            )
            missing_for_a = []
            missing_for_b = []
            if upbit_coin < required_upbit_coin_for_a:
                missing_for_a.append(
                    f'Upbit {symbol} need {required_upbit_coin_for_a:.8f} / have {upbit_coin:.8f}'
                )
            if binance_usdt < required_binance_usdt_for_a:
                missing_for_a.append(
                    f'Binance USDT need {required_binance_usdt_for_a:.2f} / have {binance_usdt:.2f}'
                )
            if upbit_krw < required_upbit_krw_for_b:
                missing_for_b.append(
                    f'Upbit KRW need {required_upbit_krw_for_b:.0f} / have {upbit_krw:.0f}'
                )
            if binance_coin < required_binance_coin_for_b:
                missing_for_b.append(
                    f'Binance {symbol} need {required_binance_coin_for_b:.8f} / have {binance_coin:.8f}'
                )
            missing = missing_for_a + missing_for_b
            action = (
                'Inventory ready for both directions.'
                if not missing else
                'Manual rebalance required: ' + ', '.join(missing) + '.'
            )
            symbols.append({
                'symbol': symbol,
                'required_trade_krw': trade_krw,
                'required_upbit_coin_for_a': required_upbit_coin_for_a,
                'required_binance_usdt_for_a': required_binance_usdt_for_a,
                'required_upbit_krw_for_b': required_upbit_krw_for_b,
                'required_binance_coin_for_b': required_binance_coin_for_b,
                'upbit_coin_qty': upbit_coin,
                'binance_coin_qty': binance_coin,
                'upbit_krw_available': upbit_krw,
                'binance_usdt_available': binance_usdt,
                'direction_a_required_assets': direction_a_assets,
                'direction_b_required_assets': direction_b_assets,
                'selected_required_assets': selected_required_assets,
                'direction_a_possible': direction_a_possible,
                'direction_b_possible': direction_b_possible,
                'missing_for_a': missing_for_a,
                'missing_for_b': missing_for_b,
                'recommended_manual_action': action,
                'status': 'OK' if direction_a_possible or direction_b_possible else 'INVENTORY_SHORTAGE',
                'blockers': [],
            })
        return {
            'mode': mode,
            'source': source,
            'ok': True,
            'blockers': [],
            'manual_rebalance_only': True,
            'balances': {
                'upbit': {'KRW': snap['upbit_krw'], **snap['upbit_coin_qty']},
                'binance': {'USDT': snap['binance_usdt'], **snap['binance_coin_qty']},
            },
            'symbols': symbols,
        }

    def upbit_bithumb_inventory_summary(self, opportunities=None, mode='paper') -> dict:
        """Return read-only Upbit/Bithumb domestic KRW inventory sufficiency."""
        opportunities = opportunities or []
        if mode == 'paper':
            venues = self.paper_snapshot()['venues']
            upbit = venues['UPBIT']
            bithumb = venues['BITHUMB']
            upbit_balances = {'KRW': upbit['KRW'], **upbit['coins']}
            bithumb_balances = {'KRW': bithumb['KRW'], **bithumb['coins']}
            blockers, source = [], 'paper_config_inventory'
        else:
            from bithumb_private import BithumbPrivateClient
            from exchange_clients import UpbitPrivateClient
            upbit = UpbitPrivateClient().get_balances()
            bithumb = BithumbPrivateClient().get_balances()
            blockers = list(upbit.get('blockers', [])) + list(bithumb.get('blockers', []))
            upbit_balances, bithumb_balances = upbit.get('balances', {}), bithumb.get('balances', {})
            source = 'read_only_private_clients'
        rows = {row.get('symbol'): row for row in opportunities if row.get('pair_id') == 'UPBIT_BITHUMB'}
        symbols = []
        trade_krw = float(cfg.upbit_bithumb_order_krw)
        for symbol in cfg.symbols:
            quote = rows.get(symbol, {})
            upbit_bid = float(quote.get('upbit_bid', 0) or 0)
            bithumb_bid = float(quote.get('bithumb_bid', 0) or 0)
            upbit_coin = float(upbit_balances.get(symbol, 0) or 0)
            bithumb_coin = float(bithumb_balances.get(symbol, 0) or 0)
            upbit_krw = float(upbit_balances.get('KRW', 0) or 0)
            bithumb_krw = float(bithumb_balances.get('KRW', 0) or 0)
            missing_a, missing_b = [], []
            direction_a_assets = quote.get('direction_a_required_assets') or {}
            direction_b_assets = quote.get('direction_b_required_assets') or {}
            selected_required_assets = quote.get('selected_required_assets') or {}
            required_upbit_coin = None
            required_bithumb_krw = None
            required_bithumb_coin = None
            required_upbit_krw = None
            if not quote or upbit_bid <= 0 or bithumb_bid <= 0:
                missing_a = ['PRICE_UNAVAILABLE']
                missing_b = ['PRICE_UNAVAILABLE']
            else:
                required_upbit_coin = float(
                    direction_a_assets.get('upbit_coin_qty', trade_krw / upbit_bid) or 0
                )
                required_bithumb_krw = float(
                    direction_a_assets.get('bithumb_krw', trade_krw) or 0
                )
                required_bithumb_coin = float(
                    direction_b_assets.get('bithumb_coin_qty', trade_krw / bithumb_bid) or 0
                )
                required_upbit_krw = float(
                    direction_b_assets.get('upbit_krw', trade_krw) or 0
                )
                if upbit_coin < required_upbit_coin:
                    missing_a.append(f'Upbit {symbol} need {required_upbit_coin:.8f} / have {upbit_coin:.8f}')
                if bithumb_krw < required_bithumb_krw:
                    missing_a.append(f'Bithumb KRW need {required_bithumb_krw:.0f} / have {bithumb_krw:.0f}')
                if bithumb_coin < required_bithumb_coin:
                    missing_b.append(f'Bithumb {symbol} need {required_bithumb_coin:.8f} / have {bithumb_coin:.8f}')
                if upbit_krw < required_upbit_krw:
                    missing_b.append(f'Upbit KRW need {required_upbit_krw:.0f} / have {upbit_krw:.0f}')
            symbols.append({
                'pair_id': 'UPBIT_BITHUMB', 'symbol': symbol,
                'upbit_coin_qty': upbit_coin, 'bithumb_coin_qty': bithumb_coin,
                'upbit_krw_available': upbit_krw, 'bithumb_krw_available': bithumb_krw,
                'required_upbit_coin_for_a': required_upbit_coin,
                'required_bithumb_krw_for_a': required_bithumb_krw,
                'required_bithumb_coin_for_b': required_bithumb_coin,
                'required_upbit_krw_for_b': required_upbit_krw,
                'direction_a_required_assets': direction_a_assets,
                'direction_b_required_assets': direction_b_assets,
                'selected_required_assets': selected_required_assets,
                'direction_a_possible': not missing_a, 'direction_b_possible': not missing_b,
                'missing_for_a': missing_a, 'missing_for_b': missing_b,
                'recommended_manual_action': (
                    'Inventory ready for both directions.' if not missing_a and not missing_b
                    else 'Manual rebalance required. Withdrawals and automatic transfers are not provided.'
                ),
                'status': 'OK' if not missing_a or not missing_b else 'INVENTORY_SHORTAGE',
            })
        return {
            'ok': not blockers, 'pair_id': 'UPBIT_BITHUMB', 'mode': mode, 'source': source,
            'blockers': blockers, 'manual_rebalance_only': True,
            'balances': {'upbit': upbit_balances, 'bithumb': bithumb_balances}, 'symbols': symbols,
        }

    # ──────────────────────────────────────────────────────────────────────
    # 진입 가능 여부 확인
    # ──────────────────────────────────────────────────────────────────────

    @_inventory_locked
    def check_paper_entry(
        self, symbol: str, direction: str, qty: float, krw_usdt: float,
        upbit_ask: float, binance_ask: float = 0,
        pair_id: str = 'UPBIT_BINANCE', bithumb_ask: float = 0,
        upbit_bid: float = 0, binance_bid: float = 0, bithumb_bid: float = 0,
        fee_krw: float = 0,
    ) -> str:
        """
        반환:
          'OK'                 – 진입 가능
          'INVENTORY_SHORTAGE' – 잔고 부족
        """
        if pair_id == 'UPBIT_BITHUMB' and direction == 'UPBIT_BITHUMB_A':
            if (
                self._coin('UPBIT', symbol) < qty
                or self._paper_inventory['BITHUMB']['KRW'] < qty * bithumb_ask
            ):
                return 'INVENTORY_SHORTAGE'
        elif pair_id == 'UPBIT_BITHUMB' and direction == 'UPBIT_BITHUMB_B':
            if (
                self._coin('BITHUMB', symbol) < qty
                or self._paper_inventory['UPBIT']['KRW'] < qty * upbit_ask
            ):
                return 'INVENTORY_SHORTAGE'
        elif direction == 'A':
            # Upbit SELL (coin qty 필요) + Binance BUY (USDT 필요)
            need_coin  = qty
            need_usdt  = qty * binance_ask
            have_coin  = self._coin('UPBIT', symbol)
            have_usdt  = self._paper_inventory['BINANCE']['USDT']
            if have_coin < need_coin or have_usdt < need_usdt:
                return 'INVENTORY_SHORTAGE'

        elif direction == 'B':
            # Upbit BUY (KRW 필요) + Binance SELL (coin qty 필요)
            need_krw   = qty * upbit_ask
            need_coin  = qty
            have_krw   = self._paper_inventory['UPBIT']['KRW']
            have_coin  = self._coin('BINANCE', symbol)
            if have_krw < need_krw or have_coin < need_coin:
                return 'INVENTORY_SHORTAGE'

        return 'OK'

    # ──────────────────────────────────────────────────────────────────────
    # Entry / Exit 가상 정산
    # ──────────────────────────────────────────────────────────────────────

    @_inventory_locked
    def apply_paper_entry(
        self, symbol: str, direction: str, qty: float, krw_usdt: float,
        upbit_ask: float, binance_ask: float = 0,
        pair_id: str = 'UPBIT_BINANCE', bithumb_ask: float = 0,
        upbit_bid: float = 0, binance_bid: float = 0, bithumb_bid: float = 0,
        fee_krw: float = 0,
    ) -> dict:
        """진입 시 paper 잔고를 가상 차감한다."""
        before = self.paper_snapshot()['venues']
        if pair_id == 'UPBIT_BITHUMB' and direction == 'UPBIT_BITHUMB_A':
            self._add_coin('UPBIT', symbol, -qty)
            self._paper_inventory['BITHUMB']['KRW'] -= qty * bithumb_ask
            self._paper_inventory['UPBIT']['KRW'] += qty * upbit_bid
            self._add_coin('BITHUMB', symbol, qty)
        elif pair_id == 'UPBIT_BITHUMB' and direction == 'UPBIT_BITHUMB_B':
            self._add_coin('BITHUMB', symbol, -qty)
            self._paper_inventory['UPBIT']['KRW'] -= qty * upbit_ask
            self._paper_inventory['BITHUMB']['KRW'] += qty * bithumb_bid
            self._add_coin('UPBIT', symbol, qty)
        elif direction == 'A':
            self._add_coin('UPBIT', symbol, -qty)
            self._paper_inventory['BINANCE']['USDT'] -= qty * binance_ask
            self._paper_inventory['UPBIT']['KRW'] += qty * upbit_bid
            self._add_coin('BINANCE', symbol, qty)
        elif direction == 'B':
            self._paper_inventory['UPBIT']['KRW'] -= qty * upbit_ask
            self._add_coin('BINANCE', symbol, -qty)
            self._add_coin('UPBIT', symbol, qty)
            self._paper_inventory['BINANCE']['USDT'] += qty * binance_bid
        self._paper_inventory['UPBIT']['KRW'] -= fee_krw
        return self._inventory_delta(before)

    @_inventory_locked
    def apply_paper_exit(
        self, symbol: str, direction: str, qty: float,
        realized_pnl_krw: float, krw_usdt: float,
        exit_upbit_bid: float, exit_binance_bid: float = 0,
        pair_id: str = 'UPBIT_BINANCE', exit_bithumb_bid: float = 0,
        exit_upbit_ask: float = 0, exit_binance_ask: float = 0,
        exit_bithumb_ask: float = 0,
        fee_krw: float = 0,
    ) -> dict:
        """
        청산 시 paper 잔고를 가상 복구/정산한다.
        순익 realized_pnl_krw를 KRW 잔고에 반영한다.
        """
        before = self.paper_snapshot()['venues']
        if pair_id == 'UPBIT_BITHUMB' and direction == 'UPBIT_BITHUMB_A':
            self._paper_inventory['UPBIT']['KRW'] -= qty * exit_upbit_ask
            self._add_coin('UPBIT', symbol, qty)
            self._add_coin('BITHUMB', symbol, -qty)
            self._paper_inventory['BITHUMB']['KRW'] += qty * exit_bithumb_bid
        elif pair_id == 'UPBIT_BITHUMB' and direction == 'UPBIT_BITHUMB_B':
            self._paper_inventory['BITHUMB']['KRW'] -= qty * exit_bithumb_ask
            self._add_coin('BITHUMB', symbol, qty)
            self._add_coin('UPBIT', symbol, -qty)
            self._paper_inventory['UPBIT']['KRW'] += qty * exit_upbit_bid
        elif direction == 'A':
            # Binance에서 산 코인 복구, Upbit 매도 KRW 입금
            self._paper_inventory['UPBIT']['KRW'] -= qty * exit_upbit_ask
            self._add_coin('UPBIT', symbol, qty)
            self._add_coin('BINANCE', symbol, -qty)
            self._paper_inventory['BINANCE']['USDT'] += qty * exit_binance_bid
        elif direction == 'B':
            # Upbit에서 산 코인 복구, Binance 매도 USDT 입금
            self._add_coin('UPBIT', symbol, -qty)
            self._paper_inventory['UPBIT']['KRW'] += qty * exit_upbit_bid
            self._paper_inventory['BINANCE']['USDT'] -= qty * exit_binance_ask
            self._add_coin('BINANCE', symbol, qty)

        # 순수익(양수)/손실(음수) 반영
        self._paper_inventory['UPBIT']['KRW'] -= fee_krw
        return self._inventory_delta(before)

    def _coin(self, venue: str, symbol: str) -> float:
        return float(self._paper_inventory[venue]['coins'].get(symbol, 0.0))

    def _add_coin(self, venue: str, symbol: str, qty: float) -> None:
        coins = self._paper_inventory[venue]['coins']
        coins[symbol] = float(coins.get(symbol, 0.0)) + qty

    def _inventory_delta(self, before: dict) -> dict:
        after = self.paper_snapshot()['venues']
        delta = {}
        for venue, balances in after.items():
            previous = before[venue]
            delta[venue] = {
                key: value - previous.get(key, 0.0)
                for key, value in balances.items() if key != 'coins'
            }
            delta[venue]['coins'] = {
                symbol: qty - previous['coins'].get(symbol, 0.0)
                for symbol, qty in balances['coins'].items()
                if qty != previous['coins'].get(symbol, 0.0)
            }
        return delta

    # ──────────────────────────────────────────────────────────────────────
    # Live interface (미구현)
    # ──────────────────────────────────────────────────────────────────────

    def fetch_live_balance(self, exchange: str) -> dict:
        """실제 잔고 조회 – 미구현. live 모드 전용."""
        raise NotImplementedError("fetch_live_balance: live mode not yet implemented.")

    @_inventory_locked
    def check_balance(self, exchange: str, asset: str, required_amount: float) -> bool:
        """하위 호환 인터페이스 유지."""
        if exchange == 'upbit':
            if asset == 'KRW':
                return self._paper_inventory['UPBIT']['KRW'] >= required_amount
            return self._coin('UPBIT', asset) >= required_amount
        elif exchange == 'binance':
            if asset == 'USDT':
                return self._paper_inventory['BINANCE']['USDT'] >= required_amount
            return self._coin('BINANCE', asset) >= required_amount
        elif exchange == 'bithumb':
            if asset == 'KRW':
                return self._paper_inventory['BITHUMB']['KRW'] >= required_amount
            return self._coin('BITHUMB', asset) >= required_amount
        return False
