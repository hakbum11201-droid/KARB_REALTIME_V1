"""
backtest_engine.py - 수집된 bounded_collector 데이터 기반 간단한 백테스트.
대량 raw tick 파일 로딩 구조 없음. 메모리 내 데이터만 사용.
"""
from typing import Optional


class BacktestEngine:
    """
    BoundedCollector에서 가져온 인메모리 틱 데이터를 기반으로
    전략 수익성을 시뮬레이션한다.

    주의:
    - 외부 파일 로딩 없음
    - sqlite / jsonl 대량 저장 없음
    - 최대 500틱 제한 (BoundedCollector.MAX_TICKS)
    """

    def __init__(self, arb_calculator, config):
        """
        arb_calculator: ArbCalculator 인스턴스
        config: cfg 객체 (threshold 참조용)
        """
        self._calc = arb_calculator
        self._cfg = config

    def run(self, ticks: list[dict], krw_usdt: float) -> dict:
        """
        ticks: [{'upbit': {...}, 'binance': {...}, 'symbol': str}, ...]
        krw_usdt: 시뮬레이션용 고정 FX 환율
        반환: 백테스트 성과 요약 딕셔너리
        """
        total_net_krw = 0.0
        total_gross_krw = 0.0
        trade_count = 0
        win_count = 0
        decisions = []

        for tick in ticks:
            symbol = tick.get('symbol', 'UNKNOWN')
            upbit_q = tick.get('upbit')
            binance_q = tick.get('binance')
            if not upbit_q or not binance_q:
                continue

            res = self._calc.calculate(symbol, upbit_q, binance_q, krw_usdt)
            net = res['net_expected_profit_krw']
            gross = res['gross_gap_krw']

            passed = (
                res['best_net_surplus_bp'] >= self._cfg.min_net_surplus_bp
                and net >= self._cfg.min_expected_profit_krw
            )

            if passed:
                total_net_krw += net
                total_gross_krw += gross
                trade_count += 1
                if net > 0:
                    win_count += 1
                decisions.append({
                    'symbol': symbol,
                    'direction': res['best_direction'],
                    'net_krw': net,
                    'gross_krw': gross,
                    'kimp_pct': res['kimchi_premium_pct'],
                })

        win_rate = (win_count / trade_count * 100) if trade_count else 0.0
        return {
            'tick_count': len(ticks),
            'trade_count': trade_count,
            'win_count': win_count,
            'win_rate_pct': round(win_rate, 2),
            'total_net_profit_krw': round(total_net_krw, 2),
            'total_gross_gap_krw': round(total_gross_krw, 2),
            'avg_net_profit_krw': round(total_net_krw / trade_count, 2) if trade_count else 0.0,
            'last_decisions': decisions[-10:],
        }
