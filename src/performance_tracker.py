"""
performance_tracker.py - paper 성과 집계기.
- closed trade 목록 기반 통계 계산
- runtime/performance_summary.json overwrite 저장
- 메모리: 최대 MAX_TRADES 건 (deque)
"""
import json
import os
import time
from collections import deque, Counter


class PerformanceTracker:
    MAX_TRADES = 500

    def __init__(self):
        self._closed: deque[dict] = deque(maxlen=self.MAX_TRADES)
        self._open_count: int = 0
        self._started_at: float = time.time()

        # today 집계
        self._today_pnl_krw: float = 0.0
        self._today_date: str = ''

        base_dir    = os.path.dirname(os.path.abspath(__file__))
        runtime_dir = os.path.normpath(os.path.join(base_dir, '..', 'runtime'))
        os.makedirs(runtime_dir, exist_ok=True)
        self._summary_path = os.path.join(runtime_dir, 'performance_summary.json')

    # ──────────────────────────────────────────────────────────────────────

    def update_open_count(self, n: int) -> None:
        self._open_count = n

    def record_exit(self, trade: dict) -> None:
        """closed trade(EXIT 이벤트)를 기록하고 성과를 갱신한다."""
        self._closed.append(trade)

        import datetime
        today = datetime.date.today().isoformat()
        if today != self._today_date:
            self._today_pnl_krw = 0.0
            self._today_date = today
        self._today_pnl_krw += trade.get('realized_pnl_krw', 0.0)

    def summary(self) -> dict:
        trades = list(self._closed)
        total  = len(trades)
        wins   = [t for t in trades if t.get('win')]
        losses = [t for t in trades if not t.get('win') and t.get('exit_reason') != 'TIMEOUT']
        timeouts = [t for t in trades if t.get('exit_reason') == 'TIMEOUT']
        clean_wins = [t for t in trades if t.get('clean_win')]

        pnls = [t.get('realized_pnl_krw', 0.0) for t in trades]
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss   = abs(sum(p for p in pnls if p < 0))
        net_pnl      = sum(pnls)
        avg_pnl      = (net_pnl / total) if total else 0.0

        # Max drawdown (running)
        max_drawdown = 0.0
        peak = 0.0
        running = 0.0
        for p in pnls:
            running += p
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_drawdown:
                max_drawdown = dd

        # best symbol / direction
        sym_pnl: dict[str, float] = {}
        dir_pnl: dict[str, float] = {}
        for t in trades:
            sym = t.get('symbol', '?')
            d   = t.get('best_direction', '?')
            p   = t.get('realized_pnl_krw', 0.0)
            sym_pnl[sym] = sym_pnl.get(sym, 0.0) + p
            dir_pnl[d]   = dir_pnl.get(d, 0.0)   + p
        best_symbol    = max(sym_pnl, key=sym_pnl.get) if sym_pnl else ''
        best_direction = max(dir_pnl, key=dir_pnl.get) if dir_pnl else ''

        positive_net = sum(1 for p in pnls if p > 0)

        s = {
            'paper_trade_count':   total,
            'open_trade_count':    self._open_count,
            'closed_trade_count':  total,
            'win_count':           len(wins),
            'loss_count':          len(losses),
            'timeout_count':       len(timeouts),
            'win_rate':            round(len(wins) / total * 100, 2) if total else 0.0,
            'clean_win_ratio':     round(len(clean_wins) / total * 100, 2) if total else 0.0,
            'gross_profit_krw':    round(gross_profit, 2),
            'gross_loss_krw':      round(gross_loss, 2),
            'net_pnl_krw':         round(net_pnl, 2),
            'avg_pnl_krw':         round(avg_pnl, 2),
            'max_drawdown_krw':    round(max_drawdown, 2),
            'positive_net_ratio':  round(positive_net / total * 100, 2) if total else 0.0,
            'best_symbol':         best_symbol,
            'best_direction':      best_direction,
            'today_pnl_krw':       round(self._today_pnl_krw, 2),
            'elapsed_hours':       round((time.time() - self._started_at) / 3600, 3),
            'buffered_trades':     len(self._closed),
            'updated_at':          time.time(),
        }
        self._write_summary(s)
        return s

    def last_closed(self, n: int = 20) -> list[dict]:
        return list(self._closed)[-n:]

    def _write_summary(self, s: dict) -> None:
        try:
            with open(self._summary_path, 'w', encoding='utf-8') as f:
                json.dump(s, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
