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


PAIR_IDS = ('UPBIT_BINANCE', 'UPBIT_BITHUMB', 'UNKNOWN')
ENTRY_REASONS = (
    'NORMAL_GO',
    'RECHECK_ACTIONABLE',
    'WIDE_SPREAD_RECHECK_ACTIONABLE',
    'UNKNOWN',
)


def summarize_pair_trades(trades: list[dict]) -> dict:
    grouped = {pair_id: [] for pair_id in PAIR_IDS}
    for trade in trades:
        pair_id = trade.get('pair_id') or 'UNKNOWN'
        grouped.setdefault(pair_id, []).append(trade)

    summary = {}
    for pair_id, pair_trades in grouped.items():
        pnls = [float(trade.get('realized_pnl_krw', 0) or 0) for trade in pair_trades]
        running = peak = max_drawdown = 0.0
        for pnl in pnls:
            running += pnl
            peak = max(peak, running)
            max_drawdown = max(max_drawdown, peak - running)
        wins = sum(1 for trade in pair_trades if trade.get('win'))
        losses = len(pair_trades) - wins
        summary[pair_id] = {
            'pair_id': pair_id,
            'closed_trade_count': len(pair_trades),
            'win_count': wins,
            'loss_count': losses,
            'win_rate': round(wins / len(pair_trades) * 100, 2) if pair_trades else 0.0,
            'net_pnl_krw': round(sum(pnls), 2),
            'gross_profit_krw': round(sum(pnl for pnl in pnls if pnl > 0), 2),
            'gross_loss_krw': round(abs(sum(pnl for pnl in pnls if pnl < 0)), 2),
            'avg_pnl_krw': round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
            'best_trade_krw': round(max(pnls), 2) if pnls else 0.0,
            'worst_trade_krw': round(min(pnls), 2) if pnls else 0.0,
            'max_drawdown_krw': round(max_drawdown, 2),
            'last_trade_at': max(
                (float(trade.get('exit_time', 0) or 0) for trade in pair_trades), default=0
            ),
        }
    return summary


def pair_summary_leaders(pair_summary: dict) -> dict:
    active = [row for row in pair_summary.values() if row.get('closed_trade_count', 0) > 0]
    if not active:
        return {'best_pair_by_pnl': '', 'best_pair_by_win_rate': '', 'most_active_pair': ''}
    return {
        'best_pair_by_pnl': max(active, key=lambda row: row['net_pnl_krw'])['pair_id'],
        'best_pair_by_win_rate': max(active, key=lambda row: row['win_rate'])['pair_id'],
        'most_active_pair': max(active, key=lambda row: row['closed_trade_count'])['pair_id'],
    }


def _normalize_entry_reason(trade: dict) -> str:
    reason = trade.get('entry_reason') or 'UNKNOWN'
    return reason if reason in ENTRY_REASONS else 'UNKNOWN'


def summarize_entry_reason_trades(trades: list[dict]) -> dict:
    grouped = {reason: [] for reason in ENTRY_REASONS}
    for trade in trades:
        grouped[_normalize_entry_reason(trade)].append(trade)

    summary = {}
    for reason, reason_trades in grouped.items():
        pnls = [float(trade.get('realized_pnl_krw', 0) or 0) for trade in reason_trades]
        wins = sum(1 for trade in reason_trades if trade.get('win'))
        losses = len(reason_trades) - wins
        holding = [float(trade.get('holding_sec', 0) or 0) for trade in reason_trades]
        summary[reason] = {
            'entry_reason': reason,
            'trade_count': len(reason_trades),
            'win_count': wins,
            'loss_count': losses,
            'win_rate': round(wins / len(reason_trades) * 100, 2) if reason_trades else 0.0,
            'net_pnl_krw': round(sum(pnls), 2),
            'avg_pnl_krw': round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
            'best_trade_krw': round(max(pnls), 2) if pnls else 0.0,
            'worst_trade_krw': round(min(pnls), 2) if pnls else 0.0,
            'avg_holding_sec': round(sum(holding) / len(holding), 2) if holding else 0.0,
            'last_trade_at': max(
                (float(trade.get('exit_time', 0) or 0) for trade in reason_trades),
                default=0,
            ),
        }
    return summary


def entry_reason_summary_leaders(by_entry_reason: dict) -> dict:
    active = [row for row in by_entry_reason.values() if row.get('trade_count', 0) > 0]
    if not active:
        return {'best_entry_reason_by_pnl': '', 'most_active_entry_reason': ''}
    return {
        'best_entry_reason_by_pnl': max(active, key=lambda row: row['net_pnl_krw'])['entry_reason'],
        'most_active_entry_reason': max(active, key=lambda row: row['trade_count'])['entry_reason'],
    }


class PerformanceTracker:
    MAX_TRADES = 500

    def __init__(self):
        self._closed: deque[dict] = deque(maxlen=self.MAX_TRADES)
        self._open_count: int = 0
        self._started_at: float = time.time()
        self._runtime_metrics: dict = {}

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

    def update_runtime_metrics(self, metrics: dict) -> None:
        self._runtime_metrics = dict(metrics)

    def record_exit(self, trade: dict) -> None:
        """closed trade(EXIT 이벤트)를 기록하고 성과를 갱신한다."""
        trade.setdefault('pair_id', 'UPBIT_BINANCE')
        trade['entry_reason'] = _normalize_entry_reason(trade)
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

        quote_summary = self._runtime_metrics.get('quote_source_summary', {})
        source_total = sum(float(quote_summary.get(key, 0) or 0) for key in ('ws', 'rest'))
        ws_ratio = round(float(quote_summary.get('ws', 0) or 0) / source_total * 100, 2) if source_total else 0.0
        pair_summary = summarize_pair_trades(trades)
        by_entry_reason = summarize_entry_reason_trades(trades)
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
            'ws_ratio':            ws_ratio,
            'avg_dynamic_slippage_bp': self._runtime_metrics.get('avg_dynamic_slippage_bp', 0.0),
            'max_dynamic_slippage_bp': self._runtime_metrics.get('max_dynamic_slippage_bp', 0.0),
            'low_depth_count': self._runtime_metrics.get('low_depth_count', 0),
            'liquidity_class_counts': self._runtime_metrics.get('liquidity_class_counts', {}),
            'paper_edge_pass_count': self._runtime_metrics.get('paper_edge_pass_count', 0),
            'paper_edge_fail_count': self._runtime_metrics.get('paper_edge_fail_count', 0),
            'avg_latency_used_ms': self._runtime_metrics.get('avg_latency_used_ms', 0.0),
            'rest_fallback_skip_count': self._runtime_metrics.get('rest_fallback_skip_count', 0),
            'rate_limit_throttle_count': self._runtime_metrics.get('rate_limit_throttle_count', 0),
            'api_429_count': self._runtime_metrics.get('api_429_count', 0),
            'pair_summary':        pair_summary,
            **pair_summary_leaders(pair_summary),
            'by_entry_reason':     by_entry_reason,
            **entry_reason_summary_leaders(by_entry_reason),
            'updated_at':          time.time(),
            **self._runtime_metrics,
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
