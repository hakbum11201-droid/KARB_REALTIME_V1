"""
paper_engine.py - Paper 모드 가상 거래 실행기 (entry/exit/TP/SL/timeout 완전 구현).

규칙:
- reason_no_trade == 'OK'일 때만 entry 생성
- 같은 symbol/direction의 open trade가 있으면 중복 entry 금지
- paper_trades.jsonl에는 entry/exit 이벤트만 append (매초 전체 저장 금지)
- net_expected_profit_krw 기준. expected_profit_krw 참조 금지
- 로그 최대 MAX_LINES 행으로 자동 회전
"""
import json
import time
import uuid
import os

from config import cfg


class PaperEngine:
    MAX_LINES = 2000

    def __init__(self, inventory_manager=None):
        self._inv = inventory_manager   # InventoryManager (옵션)
        self._open_trades:  dict[str, dict] = {}  # trade_id → trade
        self._closed_trades: list[dict] = []

        base_dir  = os.path.dirname(os.path.abspath(__file__))
        logs_dir  = os.path.normpath(os.path.join(base_dir, '..', 'logs'))
        os.makedirs(logs_dir, exist_ok=True)
        self.log_path = os.path.join(logs_dir, 'paper_trades.jsonl')
        self._line_count = self._count_existing_lines()

    # ──────────────────────────────────────────────────────────────────────
    # Entry
    # ──────────────────────────────────────────────────────────────────────

    def _entry_result(self, ok: bool, reason: str, trade: dict | None = None, detail: dict | None = None) -> dict:
        return {
            'ok': ok,
            'trade': trade,
            'reason': reason,
            'detail': detail or {},
        }

    def _entry_quote_age_limit(self, pair_id: str) -> float:
        return (
            cfg.paper_entry_domestic_max_quote_age_ms
            if pair_id == 'UPBIT_BITHUMB'
            else cfg.paper_entry_cross_border_max_quote_age_ms
        )

    def _entry_venues_and_prices(self, calc_result: dict) -> dict:
        pair_id = calc_result.get('pair_id', 'UPBIT_BINANCE')
        direction = calc_result.get('best_direction') or calc_result.get('direction', '')
        krw_usdt = float(calc_result.get('krw_usdt', 0) or 0)
        upbit_bid = float(calc_result.get('upbit_bid', 0) or 0)
        upbit_ask = float(calc_result.get('upbit_ask', 0) or 0)
        binance_bid = float(calc_result.get('binance_bid', 0) or 0)
        binance_ask = float(calc_result.get('binance_ask', 0) or 0)
        bithumb_bid = float(calc_result.get('bithumb_bid', 0) or 0)
        bithumb_ask = float(calc_result.get('bithumb_ask', 0) or 0)
        if pair_id == 'UPBIT_BITHUMB':
            if direction == 'UPBIT_BITHUMB_A':
                return {
                    'buy_venue': 'BITHUMB', 'sell_venue': 'UPBIT',
                    'buy_price': bithumb_ask, 'sell_price': upbit_bid,
                }
            if direction == 'UPBIT_BITHUMB_B':
                return {
                    'buy_venue': 'UPBIT', 'sell_venue': 'BITHUMB',
                    'buy_price': upbit_ask, 'sell_price': bithumb_bid,
                }
            return {'unsupported_direction': direction}
        if direction == 'A':
            return {
                'buy_venue': 'BINANCE', 'sell_venue': 'UPBIT',
                'buy_price': binance_ask * krw_usdt, 'sell_price': upbit_bid,
            }
        if direction == 'B':
            return {
                'buy_venue': 'UPBIT', 'sell_venue': 'BINANCE',
                'buy_price': upbit_ask, 'sell_price': binance_bid * krw_usdt,
            }
        return {'unsupported_direction': direction}

    def try_entry(self, calc_result: dict) -> dict:
        """
        진입 조건 충족 시 open trade 생성.
        entry_reason은 NORMAL_GO, RECHECK_ACTIONABLE, WIDE_SPREAD_RECHECK_ACTIONABLE 등을 보존한다.
        반환: trade 딕셔너리 or None (진입 거부)
        """
        # 조건 1: reason_no_trade == 'OK'만 허용
        if calc_result.get('reason_no_trade') != 'OK':
            return self._entry_result(False, 'PAPER_UNKNOWN_REJECT', detail={
                'reason_no_trade': calc_result.get('reason_no_trade'),
            })

        sym = calc_result['symbol']
        dirn = calc_result['best_direction']
        pair_id = calc_result.get('pair_id', 'UPBIT_BINANCE')

        # 조건 2: 같은 symbol/direction의 open이 있으면 스킵
        for t in self._open_trades.values():
            if t['symbol'] == sym and t.get('pair_id', 'UPBIT_BINANCE') == pair_id:
                return self._entry_result(False, 'PAPER_DUPLICATE_OPEN_TRADE', detail={
                    'symbol': sym,
                    'pair_id': pair_id,
                    'open_trade_id': t.get('trade_id'),
                })

        qty = float(calc_result.get('selected_qty', calc_result.get('max_fillable_qty', 0)) or 0)
        if qty <= 0:
            return self._entry_result(False, 'PAPER_SELECTED_QTY_INVALID', detail={
                'selected_qty': qty,
            })
        fees_bp = cfg.upbit_fee_bp + (
            cfg.bithumb_fee_bp if pair_id == 'UPBIT_BITHUMB' else cfg.binance_fee_bp
        )
        selected_notional_krw = float(calc_result.get('selected_notional_krw', 0) or 0)
        if selected_notional_krw <= 0:
            return self._entry_result(False, 'PAPER_SELECTED_NOTIONAL_INVALID', detail={
                'selected_notional_krw': selected_notional_krw,
            })
        min_notional = float(cfg.bithumb_min_order_krw)
        if selected_notional_krw < min_notional:
            return self._entry_result(False, 'PAPER_MIN_ORDER_NOTIONAL', detail={
                'selected_notional_krw': selected_notional_krw,
                'min_notional_krw': min_notional,
            })
        if cfg.paper_entry_require_positive_net and float(calc_result.get('net_expected_profit_krw', 0) or 0) <= 0:
            return self._entry_result(False, 'PAPER_NET_NOT_POSITIVE', detail={
                'net_expected_profit_krw': calc_result.get('net_expected_profit_krw', 0),
            })
        if calc_result.get('liquidity_class', 'NORMAL') not in ('GOOD', 'NORMAL'):
            return self._entry_result(False, 'PAPER_LIQUIDITY_BLOCKED', detail={
                'liquidity_class': calc_result.get('liquidity_class'),
            })
        if any(bool(calc_result.get(name)) for name in ('stale', 'stale_grace', 'has_stale_quote')):
            return self._entry_result(False, 'PAPER_STALE_QUOTE', detail={
                'stale': bool(calc_result.get('stale')),
                'stale_grace': bool(calc_result.get('stale_grace')),
                'has_stale_quote': bool(calc_result.get('has_stale_quote')),
            })
        entry_quote_age_ms = calc_result.get('entry_quote_age_ms')
        if entry_quote_age_ms is not None:
            entry_quote_age_ms = float(entry_quote_age_ms)
            limit_ms = self._entry_quote_age_limit(pair_id)
            if entry_quote_age_ms > limit_ms:
                return self._entry_result(False, 'PAPER_QUOTE_TOO_OLD', detail={
                    'entry_quote_age_ms': entry_quote_age_ms,
                    'limit_ms': limit_ms,
                    'entry_quote_age_source': calc_result.get('entry_quote_age_source', ''),
                })
        price_info = self._entry_venues_and_prices(calc_result)
        if price_info.get('unsupported_direction'):
            return self._entry_result(False, 'PAPER_DIRECTION_UNSUPPORTED', detail={
                'pair_id': pair_id,
                'direction': price_info.get('unsupported_direction'),
            })
        buy_price = float(calc_result.get('selected_buy_price_krw', 0) or price_info.get('buy_price', 0) or 0)
        sell_price = float(calc_result.get('selected_sell_price_krw', 0) or price_info.get('sell_price', 0) or 0)
        if buy_price <= 0 or sell_price <= 0:
            return self._entry_result(False, 'PAPER_PRICE_INVALID', detail={
                'pair_id': pair_id,
                'direction': dirn,
                'buy_price': buy_price,
                'sell_price': sell_price,
            })
        entry_fee_krw = selected_notional_krw * fees_bp / 10000
        entry_reason = calc_result.get('entry_reason') or 'NORMAL_GO'
        entered_at = time.time()

        # 조건 3: InventoryManager 검사 (주입된 경우)
        if self._inv is not None:
            status = self._inv.check_paper_entry(
                symbol=sym, direction=dirn, qty=qty,
                pair_id=pair_id,
                krw_usdt=calc_result.get('krw_usdt', 0),
                upbit_ask=calc_result['upbit_ask'],
                binance_ask=calc_result.get('binance_ask', 0),
                bithumb_ask=calc_result.get('bithumb_ask', 0),
                upbit_bid=calc_result.get('upbit_bid', 0),
                binance_bid=calc_result.get('binance_bid', 0),
                bithumb_bid=calc_result.get('bithumb_bid', 0),
                fee_krw=entry_fee_krw,
            )
            if status != 'OK':
                return self._entry_result(False, 'PAPER_INVENTORY_INSUFFICIENT', detail={
                    'inventory_status': status,
                    'symbol': sym,
                    'pair_id': pair_id,
                    'direction': dirn,
                    'qty': qty,
                    'selected_notional_krw': selected_notional_krw,
                })
            inventory_delta = self._inv.apply_paper_entry(
                symbol=sym, direction=dirn, qty=qty,
                pair_id=pair_id,
                krw_usdt=calc_result.get('krw_usdt', 0),
                upbit_ask=calc_result['upbit_ask'],
                binance_ask=calc_result.get('binance_ask', 0),
                bithumb_ask=calc_result.get('bithumb_ask', 0),
                upbit_bid=calc_result.get('upbit_bid', 0),
                binance_bid=calc_result.get('binance_bid', 0),
                bithumb_bid=calc_result.get('bithumb_bid', 0),
            )
        else:
            inventory_delta = {}

        trade_id = str(uuid.uuid4())[:12]
        trade = {
            'trade_id':              trade_id,
            'event':                 'ENTRY',
            'status':                'OPEN',
            'entry_time':            entered_at,
            'entered_at':            entered_at,
            'pair_id':               pair_id,
            'strategy_type':         calc_result.get('strategy_type', 'CROSS_BORDER_SPOT'),
            'symbol':                sym,
            'best_direction':        dirn,
            'direction':             dirn,
            'selected_qty':          qty,
            'selected_notional_krw': selected_notional_krw,
            'raw_depth_qty':         calc_result.get('raw_depth_qty', calc_result.get('max_fillable_qty_raw', qty)),
            'effective_qty':         calc_result.get('effective_qty', qty),
            'selected_required_assets': calc_result.get('selected_required_assets', {}),
            'entry_buy_price_krw':   buy_price,
            'entry_sell_price_krw':  sell_price,
            'buy_venue':             calc_result.get('buy_venue') or price_info.get('buy_venue', ''),
            'sell_venue':            calc_result.get('sell_venue') or price_info.get('sell_venue', ''),
            'buy_price':             calc_result.get('buy_price') or buy_price,
            'sell_price':            calc_result.get('sell_price') or sell_price,
            'buy_leg_quote_age_ms':  calc_result.get('buy_leg_quote_age_ms'),
            'sell_leg_quote_age_ms': calc_result.get('sell_leg_quote_age_ms'),
            'entry_fee_krw':         entry_fee_krw,
            'venues':                ['UPBIT', 'BITHUMB'] if pair_id == 'UPBIT_BITHUMB' else ['UPBIT', 'BINANCE'],
            'inventory_delta':       inventory_delta,
            'expected_net_profit_krw': calc_result['net_expected_profit_krw'],
            'entry_reason':          entry_reason,
            'recheck_status':        calc_result.get('stale_recheck_status', ''),
            'entry_surplus_bp':      calc_result.get('best_net_surplus_bp', 0),
            'entry_net_expected_profit_krw': calc_result.get('net_expected_profit_krw', 0),
            'max_leg_quote_age_ms':  calc_result.get('max_leg_quote_age_ms'),
            'entry_quote_age_ms':    calc_result.get('entry_quote_age_ms'),
            'entry_quote_age_source': calc_result.get('entry_quote_age_source', ''),
            'entry_refreshed_at':    calc_result.get('entry_refreshed_at'),
            'entry_refresh_started_at': calc_result.get('entry_refresh_started_at'),
            'entry_fetch_ms':        calc_result.get('entry_fetch_ms'),
            'entry_decision_wait_ms': calc_result.get('entry_decision_wait_ms'),
            'quote_source':          calc_result.get('quote_source', ''),
            'expected_slippage_bp':  calc_result.get('expected_slippage_bp', calc_result.get('dynamic_slippage_bp', cfg.slippage_bp)),
            'expected_fee_krw':      calc_result.get('expected_fee_krw', entry_fee_krw),
            'wide_spread_recheck_status': calc_result.get('wide_spread_recheck_status', ''),
            # 진입 호가 스냅샷
            'entry_upbit_bid':       calc_result['upbit_bid'],
            'entry_upbit_ask':       calc_result['upbit_ask'],
            'entry_binance_bid':     calc_result.get('binance_bid'),
            'entry_binance_ask':     calc_result.get('binance_ask'),
            'entry_bithumb_bid':     calc_result.get('bithumb_bid'),
            'entry_bithumb_ask':     calc_result.get('bithumb_ask'),
            'entry_krw_usdt':        calc_result.get('krw_usdt'),
            # 계산 결과
            'best_net_surplus_bp':   calc_result['best_net_surplus_bp'],
            'net_expected_profit_krw': calc_result['net_expected_profit_krw'],
            'gross_gap_krw':         calc_result.get('gross_gap_krw', 0),
            'max_fillable_qty':      qty,
            # 비용 분해
            'fees_bp':               fees_bp,
            'slippage_bp':           cfg.slippage_bp,
            'fx_error_bp':           cfg.fx_error_bp,
            'risk_buffer_bp':        cfg.risk_buffer_bp,
        }
        self._open_trades[trade_id] = trade
        self._append_log({**trade})
        return self._entry_result(True, 'ENTERED', trade=trade)

    # ──────────────────────────────────────────────────────────────────────
    # Exit check (매 루프마다 호출)
    # ──────────────────────────────────────────────────────────────────────

    def check_exits(
        self, current_quotes: dict, krw_usdt: float, domestic_quotes: dict | None = None
    ) -> list[dict]:
        """
        open trade 전체를 검사하여 TP/SL/TIMEOUT 조건 충족 시 청산.
        current_quotes: { sym: { 'upbit': {...}, 'binance': {...} } }
        반환: 청산된 trade 목록
        """
        closed = []
        for trade_id in list(self._open_trades.keys()):
            trade = self._open_trades[trade_id]
            sym   = trade['symbol']
            pair_id = trade.get('pair_id', 'UPBIT_BINANCE')
            if pair_id == 'UPBIT_BITHUMB':
                q = (domestic_quotes or {}).get('UPBIT_BITHUMB', {}).get(sym)
            else:
                q = current_quotes.get(sym)
            if not q:
                continue

            exit_trade = self._evaluate_exit(trade, q, krw_usdt)
            if exit_trade:
                del self._open_trades[trade_id]
                self._closed_trades.append(exit_trade)

                if self._inv is not None:
                    inventory_delta = self._inv.apply_paper_exit(
                        symbol=sym,
                        direction=trade['best_direction'],
                        qty=trade['max_fillable_qty'],
                        realized_pnl_krw=exit_trade['realized_pnl_krw'],
                        pair_id=pair_id,
                        krw_usdt=krw_usdt,
                        exit_upbit_bid=q['upbit']['bid'],
                        exit_binance_bid=q.get('binance', {}).get('bid', 0),
                        exit_bithumb_bid=q.get('bithumb', {}).get('bid', 0),
                        exit_upbit_ask=q['upbit']['ask'],
                        exit_binance_ask=q.get('binance', {}).get('ask', 0),
                        exit_bithumb_ask=q.get('bithumb', {}).get('ask', 0),
                        fee_krw=float(trade.get('selected_notional_krw', 0) or 0)
                        * trade['fees_bp'] / 10000,
                    )
                    exit_trade['exit_inventory_delta'] = inventory_delta
                self._append_log(exit_trade)
                closed.append(exit_trade)
        return closed

    # ──────────────────────────────────────────────────────────────────────
    # 내부: 청산 평가
    # ──────────────────────────────────────────────────────────────────────

    def _evaluate_exit(self, trade: dict, quote: dict, krw_usdt: float) -> dict | None:
        """
        TP / SL / TIMEOUT 중 조건 충족 시 closed trade 딕셔너리 반환.
        아직 조건 미달이면 None.
        """
        now = time.time()
        holding_sec = now - trade['entry_time']
        dirn        = trade['best_direction']
        qty         = trade['max_fillable_qty']

        u_bid = quote['upbit']['bid']
        u_ask = quote['upbit']['ask']
        if trade.get('pair_id', 'UPBIT_BINANCE') == 'UPBIT_BITHUMB':
            return self._evaluate_domestic_exit(trade, quote, now, holding_sec, qty)
        b_bid = quote['binance']['bid']
        b_ask = quote['binance']['ask']

        # 현재 방향별 순익 bp 계산
        total_cost_bp = cfg.total_cost_bp
        if dirn == 'A':
            # 청산: Upbit SELL bid, Binance BUY ask
            current_surplus_bp = (u_bid - b_ask * krw_usdt) / (b_ask * krw_usdt) * 10000
            realized_pnl_krw   = qty * (u_bid - b_ask * krw_usdt) - qty * b_ask * krw_usdt * (total_cost_bp / 10000)
            realized_bp        = current_surplus_bp - total_cost_bp
        else:  # B
            # 청산: Upbit BUY ask, Binance SELL bid
            current_surplus_bp = (b_bid * krw_usdt - u_ask) / u_ask * 10000
            realized_pnl_krw   = qty * (b_bid * krw_usdt - u_ask) - qty * u_ask * (total_cost_bp / 10000)
            realized_bp        = current_surplus_bp - total_cost_bp

        entry_bp = trade['best_net_surplus_bp']
        bp_change = realized_bp - entry_bp   # 양수 = 스프레드 확대(불리), 음수 = 축소(유리)

        exit_reason = None

        # TIMEOUT
        if holding_sec >= cfg.paper_timeout_sec:
            exit_reason = 'TIMEOUT'

        # TP: realized_bp가 entry_bp에서 take_profit_bp만큼 개선
        # (스프레드 역방향으로 이익 실현 = realized_bp가 더 음수로 감소 = 수익 확대)
        # 단순화: realized_pnl_krw > net_expected_profit_krw * (1 + tp_ratio)
        tp_threshold_krw = trade['net_expected_profit_krw'] * (1 + cfg.paper_take_profit_bp / 10000)
        if exit_reason is None and realized_pnl_krw >= tp_threshold_krw:
            exit_reason = 'TP'

        # SL: realized_pnl_krw < -SL_threshold
        sl_threshold_krw = trade['net_expected_profit_krw'] * (cfg.paper_stop_loss_bp / 10000)
        if exit_reason is None and realized_pnl_krw <= -sl_threshold_krw:
            exit_reason = 'SL'

        if exit_reason is None:
            return None

        win       = realized_pnl_krw > 0
        clean_win = win and exit_reason == 'TP'

        return {
            **trade,
            'event':            'EXIT',
            'status':           'CLOSED',
            'exit_time':        now,
            'exit_reason':      exit_reason,
            'holding_sec':      round(holding_sec, 2),
            'realized_pnl_krw': round(realized_pnl_krw, 2),
            'realized_bp':      round(realized_bp, 4),
            'exit_upbit_bid':   u_bid,
            'exit_upbit_ask':   u_ask,
            'exit_binance_bid': b_bid,
            'exit_binance_ask': b_ask,
            'exit_krw_usdt':    krw_usdt,
            'win':              win,
            'clean_win':        clean_win,
        }

    def _evaluate_domestic_exit(
        self, trade: dict, quote: dict, now: float, holding_sec: float, qty: float
    ) -> dict | None:
        u_bid, u_ask = quote['upbit']['bid'], quote['upbit']['ask']
        h_bid, h_ask = quote['bithumb']['bid'], quote['bithumb']['ask']
        direction = trade['best_direction']
        entry_buy = float(trade.get('entry_buy_price_krw', 0) or 0)
        entry_sell = float(trade.get('entry_sell_price_krw', 0) or 0)
        exit_buy, exit_sell = (
            (u_ask, h_bid) if direction == 'UPBIT_BITHUMB_A' else (h_ask, u_bid)
        )
        exit_fee_krw = qty * exit_buy * trade['fees_bp'] / 10000
        realized_pnl_krw = (
            qty * ((entry_sell - entry_buy) + (exit_sell - exit_buy))
            - float(trade.get('entry_fee_krw', 0) or 0)
            - exit_fee_krw
        )
        notional = float(trade.get('selected_notional_krw', 0) or 0)
        realized_bp = realized_pnl_krw / notional * 10000 if notional else 0.0
        exit_reason = None
        if holding_sec >= cfg.paper_timeout_sec:
            exit_reason = 'TIMEOUT'
        tp_threshold_krw = trade['net_expected_profit_krw'] * (
            1 + cfg.paper_take_profit_bp / 10000
        )
        if exit_reason is None and realized_pnl_krw >= tp_threshold_krw:
            exit_reason = 'TP'
        sl_threshold_krw = trade['net_expected_profit_krw'] * (
            cfg.paper_stop_loss_bp / 10000
        )
        if exit_reason is None and realized_pnl_krw <= -sl_threshold_krw:
            exit_reason = 'SL'
        if exit_reason is None:
            return None
        win = realized_pnl_krw > 0
        return {
            **trade,
            'event': 'EXIT', 'status': 'CLOSED', 'exit_time': now,
            'exit_reason': exit_reason, 'holding_sec': round(holding_sec, 2),
            'realized_pnl_krw': round(realized_pnl_krw, 2),
            'realized_bp': round(realized_bp, 4),
            'exit_upbit_bid': u_bid, 'exit_upbit_ask': u_ask,
            'exit_bithumb_bid': h_bid, 'exit_bithumb_ask': h_ask,
            'exit_krw_usdt': None, 'win': win,
            'clean_win': win and exit_reason == 'TP',
        }

    # ──────────────────────────────────────────────────────────────────────
    # 조회 헬퍼
    # ──────────────────────────────────────────────────────────────────────

    def open_count(self) -> int:
        return len(self._open_trades)

    def closed_count(self) -> int:
        return len(self._closed_trades)

    def recent_closed(self, n: int = 20) -> list[dict]:
        return self._closed_trades[-n:]

    # ──────────────────────────────────────────────────────────────────────
    # 로그
    # ──────────────────────────────────────────────────────────────────────

    def _count_existing_lines(self) -> int:
        if not os.path.exists(self.log_path):
            return 0
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def _rotate_if_needed(self) -> None:
        if self._line_count < self.MAX_LINES:
            return
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            keep = lines[len(lines) // 2:]
            with open(self.log_path, 'w', encoding='utf-8') as f:
                f.writelines(keep)
            self._line_count = len(keep)
        except Exception:
            pass

    def _append_log(self, record: dict) -> None:
        self._rotate_if_needed()
        try:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
            self._line_count += 1
        except Exception:
            pass
