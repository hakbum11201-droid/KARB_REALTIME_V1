"""
session_analyzer.py - 세션 종료 시 자동 분석 리포트 생성.

출력:
  reports/sessions/{run_id}_summary.json
  reports/sessions/{run_id}_summary.txt
  runtime/last_session_summary.json (overwrite)

판정(judgement):
  PAPER_EDGE_PASS  – paper 기준 유의미한 엣지 확인
  PAPER_EDGE_WEAK  – 일부 조건만 충족
  PAPER_EDGE_FAIL  – 순익 음수
  NOT_ENOUGH_TRADES – closed < 10건
  RUNTIME_ERROR    – 세션 에러로 분석 불가
"""
import json
import os
import time
import glob
import statistics
from config import cfg


BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'runtime'))
LOGS_DIR    = os.path.normpath(os.path.join(BASE_DIR, '..', 'logs'))
REPORTS_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'reports', 'sessions'))


def analyze_session(run_id: str) -> dict:
    """
    엔진 종료 후 호출되어 파일 기반으로 세션 리포트를 생성한다.
    입력: runtime/performance_summary.json, runtime/latest_state.json,
          logs/paper_trades.jsonl, logs/decisions.jsonl
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    
    # ── 파일 읽기 ──────────────────────────────────────────────────────────
    def _read_json(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _read_jsonl(path):
        lines = []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            lines.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            pass
        return lines

    perf_path = os.path.join(RUNTIME_DIR, 'performance_summary.json')
    state_path = os.path.join(RUNTIME_DIR, 'latest_state.json')
    trades_path = os.path.join(LOGS_DIR, 'paper_trades.jsonl')
    decisions_path = os.path.join(LOGS_DIR, 'decisions.jsonl')

    perf = _read_json(perf_path)
    state = _read_json(state_path)
    trades = _read_jsonl(trades_path)
    decisions = _read_jsonl(decisions_path)

    analyzer = SessionAnalyzer(cfg)
    
    # ── 통계 집계 ────────────────────────────────────────────────────────
    session_stats = {
        **perf,
        'run_id': run_id,
        'started_at': state.get('started_at', 0),
        'ended_at': time.time(),
        'duration_sec': state.get('runtime_sec', 0),
        'total_loops': len(decisions),
        'quote_count': len(decisions),
        'candidate_count': sum(1 for d in decisions if d.get('reason_no_trade') == 'OK'),
        'paper_entry_count': sum(1 for t in trades if t.get('event') == 'ENTRY'),
        'paper_exit_count': sum(1 for t in trades if t.get('event') == 'EXIT'),
        'error_count': sum(1 for d in decisions if d.get('error') or d.get('fx_status') == 'ERROR'),
        'avg_trade_size_krw': cfg.max_one_trade_krw,
    }

    # Reason 분포 추정
    reasons = {}
    surplus_list = []
    for d in decisions:
        r = d.get('reason_no_trade', 'UNKNOWN')
        reasons[r] = reasons.get(r, 0) + 1
        surplus = d.get('best_net_surplus_bp')
        if surplus is not None:
            surplus_list.append(surplus)
            
    session_stats['reason_counts'] = reasons
    session_stats['surplus_bp_list'] = surplus_list
    session_stats['loop_latency_ms_list'] = []  # 파일에서는 레이턴시를 구하기 어려움
    session_stats['quote_latency_ms_list'] = []

    report = analyzer.analyze(session_stats)
    return report


class SessionAnalyzer:
    """분석 코어 엔진"""

    def __init__(self, config):
        self._cfg = config

    def analyze(self, session_stats: dict) -> dict:
        """
        session_stats: 집계된 세션 통계.
        반환: 분석 결과 딕셔너리 (judgement 포함).
        """
        run_id = session_stats.get('run_id', 'unknown')

        # ── 기본 통계 ────────────────────────────────────────────────────
        r = {
            'run_id':             run_id,
            'started_at':         session_stats.get('started_at', 0),
            'ended_at':           session_stats.get('ended_at', time.time()),
            'duration_sec':       session_stats.get('duration_sec', 0),
            'total_loops':        session_stats.get('total_loops', 0),
            'quote_count':        session_stats.get('quote_count', 0),
            'candidate_count':    session_stats.get('candidate_count', 0),
            'paper_entry_count':  session_stats.get('paper_entry_count', 0),
            'paper_exit_count':   session_stats.get('paper_exit_count', 0),
            'open_trade_count':   session_stats.get('open_trade_count', 0),
            'closed_trade_count': session_stats.get('closed_trade_count', 0),
            'win_count':          session_stats.get('win_count', 0),
            'loss_count':         session_stats.get('loss_count', 0),
            'timeout_count':      session_stats.get('timeout_count', 0),
            'win_rate':           session_stats.get('win_rate', 0.0),
            'clean_win_ratio':    session_stats.get('clean_win_ratio', 0.0),
            'gross_profit_krw':   session_stats.get('gross_profit_krw', 0.0),
            'gross_loss_krw':     session_stats.get('gross_loss_krw', 0.0),
            'net_pnl_krw':        session_stats.get('net_pnl_krw', 0.0),
            'avg_pnl_krw':        session_stats.get('avg_pnl_krw', 0.0),
            'max_drawdown_krw':   session_stats.get('max_drawdown_krw', 0.0),
            'best_symbol':        session_stats.get('best_symbol', ''),
            'best_direction':     session_stats.get('best_direction', ''),
            'reason_counts':      session_stats.get('reason_counts', {}),
            'positive_net_ratio': session_stats.get('positive_net_ratio', 0.0),
            'error_count':        session_stats.get('error_count', 0),
        }

        # ── surplus 통계 ─────────────────────────────────────────────────
        surplus_list = session_stats.get('surplus_bp_list', [])
        if surplus_list:
            r['avg_best_net_surplus_bp'] = round(statistics.mean(surplus_list), 4)
            r['max_best_net_surplus_bp'] = round(max(surplus_list), 4)
        else:
            r['avg_best_net_surplus_bp'] = 0.0
            r['max_best_net_surplus_bp'] = 0.0

        # ── 레이턴시 통계 ─────────────────────────────────────────────────
        loop_lats  = session_stats.get('loop_latency_ms_list', [])
        quote_lats = session_stats.get('quote_latency_ms_list', [])
        r['p95_loop_latency_ms']  = self._percentile(loop_lats, 95)
        r['p95_quote_latency_ms'] = self._percentile(quote_lats, 95)

        # 네트워크 건강도
        p95_q = r['p95_quote_latency_ms']
        max_lat = self._cfg.max_latency_ms
        if p95_q <= max_lat * 0.7:
            r['network_health'] = 'GOOD'
        elif p95_q <= max_lat:
            r['network_health'] = 'WARNING'
        else:
            r['network_health'] = 'BAD'

        # ── 슬리피지 스트레스 테스트 ───────────────────────────────────────
        base_slippage = self._cfg.slippage_bp
        r['configured_slippage_bp'] = base_slippage
        r['slippage_stress_plus_5bp_estimated_pnl'] = self._stress_pnl(session_stats, 5)
        r['slippage_stress_plus_10bp_estimated_pnl'] = self._stress_pnl(session_stats, 10)

        # 트레이딩 퀄리티
        stress5 = r['slippage_stress_plus_5bp_estimated_pnl']
        if r['net_pnl_krw'] > 0 and stress5 > 0:
            r['trading_quality'] = 'GOOD'
        elif r['net_pnl_krw'] > 0:
            r['trading_quality'] = 'WEAK'
        else:
            r['trading_quality'] = 'BAD'

        # ── 로그 크기 ─────────────────────────────────────────────────────
        r['log_total_size_mb'] = self._calc_log_size_mb()

        # ── Judgement ─────────────────────────────────────────────────────
        r['judgement'] = self._judge(r)

        # ── 저장 ──────────────────────────────────────────────────────────
        self._save_json(r, run_id)
        self._save_text(r, run_id)
        self._save_last_session(r)

        return r

    # ── 판정 ──────────────────────────────────────────────────────────────

    def _judge(self, r: dict) -> str:
        closed = r.get('closed_trade_count', 0)
        if r.get('error_count', 0) > closed * 0.5 and closed < 5:
            return 'RUNTIME_ERROR'
        if closed < 10:
            return 'NOT_ENOUGH_TRADES'
        if r['net_pnl_krw'] <= 0:
            return 'PAPER_EDGE_FAIL'

        # PASS 조건: 모두 만족
        conditions = [
            r['net_pnl_krw'] > 0,
            r['win_rate'] >= 65.0,
            r['avg_pnl_krw'] > 0,
            r['max_drawdown_krw'] <= self._cfg.daily_loss_limit_krw,
            r.get('slippage_stress_plus_5bp_estimated_pnl', 0) >= 0,
        ]
        if all(conditions):
            return 'PAPER_EDGE_PASS'
        return 'PAPER_EDGE_WEAK'

    # ── 슬리피지 스트레스 ─────────────────────────────────────────────────

    def _stress_pnl(self, stats: dict, extra_bp: float) -> float:
        """순익에서 슬리피지 추가 비용을 빼서 추정한다."""
        closed = stats.get('closed_trade_count', 0)
        if closed == 0:
            return 0.0
        avg_trade_size_krw = stats.get('avg_trade_size_krw', 50000)
        extra_cost_per_trade = avg_trade_size_krw * (extra_bp / 10000)
        return round(stats.get('net_pnl_krw', 0) - extra_cost_per_trade * closed, 2)

    # ── 유틸 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _percentile(data: list, pct: int) -> float:
        if not data:
            return 0.0
        s = sorted(data)
        idx = int(len(s) * pct / 100)
        idx = min(idx, len(s) - 1)
        return round(s[idx], 2)

    @staticmethod
    def _calc_log_size_mb() -> float:
        total = 0
        for f in glob.glob(os.path.join(LOGS_DIR, '*')):
            try:
                if os.path.isfile(f):
                    total += os.path.getsize(f)
            except Exception:
                pass
        return round(total / (1024 * 1024), 3)

    def _save_json(self, r: dict, run_id: str) -> None:
        path = os.path.join(REPORTS_DIR, f'{run_id}_summary.json')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(r, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _save_text(self, r: dict, run_id: str) -> None:
        path = os.path.join(REPORTS_DIR, f'{run_id}_summary.txt')
        try:
            lines = [
                f"KARB_REALTIME_V1 Session Report",
                f"{'='*60}",
                f"Run ID:        {r['run_id']}",
                f"Duration:      {r['duration_sec']:.0f} sec ({r['duration_sec']/3600:.2f} h)",
                f"Judgement:     {r['judgement']}",
                f"",
                f"─── 성과 ───",
                f"Total Closed:  {r['closed_trade_count']}",
                f"Win/Loss/TO:   {r['win_count']} / {r['loss_count']} / {r['timeout_count']}",
                f"Win Rate:      {r['win_rate']:.1f}%",
                f"Clean Win:     {r['clean_win_ratio']:.1f}%",
                f"Net PnL:       {r['net_pnl_krw']:+,.0f} KRW",
                f"Avg PnL:       {r['avg_pnl_krw']:+,.0f} KRW",
                f"Max Drawdown:  {r['max_drawdown_krw']:,.0f} KRW",
                f"Best Symbol:   {r['best_symbol']}",
                f"Best Dir:      {r['best_direction']}",
                f"",
                f"─── 신호 분석 ───",
                f"Total Loops:   {r['total_loops']}",
                f"Quotes:        {r['quote_count']}",
                f"Candidates:    {r['candidate_count']}",
                f"Entries:       {r['paper_entry_count']}",
                f"Avg Surplus:   {r['avg_best_net_surplus_bp']:.2f} bp",
                f"Max Surplus:   {r['max_best_net_surplus_bp']:.2f} bp",
                f"",
                f"─── 인프라 ───",
                f"P95 Loop:      {r['p95_loop_latency_ms']:.0f} ms",
                f"P95 Quote:     {r['p95_quote_latency_ms']:.0f} ms",
                f"Network:       {r['network_health']}",
                f"Log Size:      {r['log_total_size_mb']:.2f} MB",
                f"Errors:        {r['error_count']}",
                f"",
                f"─── 스트레스 테스트 ───",
                f"Slippage:      {r['configured_slippage_bp']} bp",
                f"+5bp Stress:   {r['slippage_stress_plus_5bp_estimated_pnl']:+,.0f} KRW",
                f"+10bp Stress:  {r['slippage_stress_plus_10bp_estimated_pnl']:+,.0f} KRW",
                f"Quality:       {r['trading_quality']}",
                f"",
                f"─── Reason 분포 ───",
            ]
            for reason, count in sorted(r.get('reason_counts', {}).items(), key=lambda x: -x[1]):
                lines.append(f"  {reason:25s} {count}")
            lines.append(f"\n{'='*60}")
            lines.append(f"Judgement: {r['judgement']}")
            if r['judgement'] == 'PAPER_EDGE_PASS':
                lines.append("→ 이 전략은 paper 기준 승산 있음. tiny_live 검토 가능.")
            elif r['judgement'] == 'PAPER_EDGE_WEAK':
                lines.append("→ 일부 조건만 충족. 파라미터 조정 후 재검증 권장.")
            elif r['judgement'] == 'PAPER_EDGE_FAIL':
                lines.append("→ 이 전략은 paper 기준 승산 없음.")
            elif r['judgement'] == 'NOT_ENOUGH_TRADES':
                lines.append("→ 거래 수 부족. 더 긴 시간 검증 필요.")
            else:
                lines.append("→ 런타임 에러로 분석 불가. 로그 확인 필요.")

            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
        except Exception:
            pass

    @staticmethod
    def _save_last_session(r: dict) -> None:
        path = os.path.join(RUNTIME_DIR, 'last_session_summary.json')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(r, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
