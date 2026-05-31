"""
mining_engine.py - 심볼/임계값별 최적 파라미터 탐색 (제한적 그리드 서치).
대량 raw data 로딩 없음. BoundedCollector 인메모리 데이터만 사용.
"""
from backtest_engine import BacktestEngine
from arb_calculator import ArbCalculator


class _MockConfig:
    """BacktestEngine에 주입할 간이 config 객체."""
    def __init__(self, min_net_surplus_bp: float, min_expected_profit_krw: float,
                 upbit_fee_bp: float, binance_fee_bp: float,
                 slippage_bp: float, fx_error_bp: float, risk_buffer_bp: float):
        self.min_net_surplus_bp = min_net_surplus_bp
        self.min_expected_profit_krw = min_expected_profit_krw
        self.upbit_fee_bp = upbit_fee_bp
        self.binance_fee_bp = binance_fee_bp
        self.slippage_bp = slippage_bp
        self.fx_error_bp = fx_error_bp
        self.risk_buffer_bp = risk_buffer_bp


class MiningEngine:
    """
    BoundedCollector에 보관된 인메모리 틱에 대해
    min_net_surplus_bp / min_expected_profit_krw 조합을 그리드 탐색하여
    최적 임계값 후보를 반환한다.

    주의:
    - 결과를 파일에 저장하지 않는다.
    - sqlite / jsonl 대량 저장 없음.
    """

    def __init__(self, base_cfg):
        """base_cfg: 현재 cfg 객체 (fee/slippage 기준값 참조)."""
        self._base_cfg = base_cfg

    def search(
        self,
        ticks: list[dict],
        krw_usdt: float,
        surplus_candidates: list[float] | None = None,
        profit_candidates: list[float] | None = None,
    ) -> dict:
        """
        surplus_candidates: min_net_surplus_bp 후보 리스트 (기본: [5, 10, 15, 20, 30])
        profit_candidates: min_expected_profit_krw 후보 리스트 (기본: [500, 1000, 2000])
        반환: 최적 파라미터 + 성과 테이블
        """
        if surplus_candidates is None:
            surplus_candidates = [5.0, 10.0, 15.0, 20.0, 30.0]
        if profit_candidates is None:
            profit_candidates = [500.0, 1000.0, 2000.0]

        results = []
        calc = ArbCalculator()

        for surplus_bp in surplus_candidates:
            for profit_krw in profit_candidates:
                mock_cfg = _MockConfig(
                    min_net_surplus_bp=surplus_bp,
                    min_expected_profit_krw=profit_krw,
                    upbit_fee_bp=self._base_cfg.upbit_fee_bp,
                    binance_fee_bp=self._base_cfg.binance_fee_bp,
                    slippage_bp=self._base_cfg.slippage_bp,
                    fx_error_bp=self._base_cfg.fx_error_bp,
                    risk_buffer_bp=self._base_cfg.risk_buffer_bp,
                )
                bt = BacktestEngine(calc, mock_cfg)
                summary = bt.run(ticks, krw_usdt)
                results.append({
                    'min_net_surplus_bp': surplus_bp,
                    'min_expected_profit_krw': profit_krw,
                    **summary,
                })

        # 총 순수익 기준 정렬
        results.sort(key=lambda x: x['total_net_profit_krw'], reverse=True)
        best = results[0] if results else {}

        return {
            'best': best,
            'grid': results,
            'tick_count': len(ticks),
        }
