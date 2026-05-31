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

    def try_entry(self, calc_result: dict) -> dict | None:
        """
        진입 조건 충족 시 open trade 생성.
        반환: trade 딕셔너리 or None (진입 거부)
        """
        # 조건 1: reason_no_trade == 'OK'만 허용
        if calc_result.get('reason_no_trade') != 'OK':
            return None

        sym  = calc_result['symbol']
        dirn = calc_result['best_direction']

        # 조건 2: 같은 symbol/direction의 open이 있으면 스킵
        for t in self._open_trades.values():
            if t['symbol'] == sym and t['best_direction'] == dirn:
                return None

        qty = calc_result['max_fillable_qty']

        # 조건 3: InventoryManager 검사 (주입된 경우)
        if self._inv is not None:
            status = self._inv.check_paper_entry(
                symbol=sym, direction=dirn, qty=qty,
                krw_usdt=calc_result['krw_usdt'],
                upbit_ask=calc_result['upbit_ask'],
                binance_ask=calc_result['binance_ask'],
            )
            if status != 'OK':
                return None
            self._inv.apply_paper_entry(
                symbol=sym, direction=dirn, qty=qty,
                krw_usdt=calc_result['krw_usdt'],
                upbit_ask=calc_result['upbit_ask'],
                binance_ask=calc_result['binance_ask'],
            )

        trade_id = str(uuid.uuid4())[:12]
        trade = {
            'trade_id':              trade_id,
            'event':                 'ENTRY',
            'status':                'OPEN',
            'entry_time':            time.time(),
            'symbol':                sym,
            'best_direction':        dirn,
            # 진입 호가 스냅샷
            'entry_upbit_bid':       calc_result['upbit_bid'],
            'entry_upbit_ask':       calc_result['upbit_ask'],
            'entry_binance_bid':     calc_result['binance_bid'],
            'entry_binance_ask':     calc_result['binance_ask'],
            'entry_krw_usdt':        calc_result['krw_usdt'],
            # 계산 결과
            'best_net_surplus_bp':   calc_result['best_net_surplus_bp'],
            'net_expected_profit_krw': calc_result['net_expected_profit_krw'],
            'gross_gap_krw':         calc_result['gross_gap_krw'],
            'max_fillable_qty':      qty,
            # 비용 분해
            'fees_bp': cfg.upbit_fee_bp + cfg.binance_fee_bp,
            'slippage_bp':           cfg.slippage_bp,
            'fx_error_bp':           cfg.fx_error_bp,
            'risk_buffer_bp':        cfg.risk_buffer_bp,
        }
        self._open_trades[trade_id] = trade
        self._append_log({**trade})
        return trade

    # ──────────────────────────────────────────────────────────────────────
    # Exit check (매 루프마다 호출)
    # ──────────────────────────────────────────────────────────────────────

    def check_exits(self, current_quotes: dict, krw_usdt: float) -> list[dict]:
        """
        open trade 전체를 검사하여 TP/SL/TIMEOUT 조건 충족 시 청산.
        current_quotes: { sym: { 'upbit': {...}, 'binance': {...} } }
        반환: 청산된 trade 목록
        """
        closed = []
        for trade_id in list(self._open_trades.keys()):
            trade = self._open_trades[trade_id]
            sym   = trade['symbol']
            q     = current_quotes.get(sym)
            if not q:
                continue

            exit_trade = self._evaluate_exit(trade, q, krw_usdt)
            if exit_trade:
                del self._open_trades[trade_id]
                self._closed_trades.append(exit_trade)
                self._append_log(exit_trade)

                if self._inv is not None:
                    self._inv.apply_paper_exit(
                        symbol=sym,
                        direction=trade['best_direction'],
                        qty=trade['max_fillable_qty'],
                        realized_pnl_krw=exit_trade['realized_pnl_krw'],
                        krw_usdt=krw_usdt,
                        exit_upbit_bid=q['upbit']['bid'],
                        exit_binance_bid=q['binance']['bid'],
                    )
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
