"""
risk_guard.py - 실전 RiskGuard.
config.yaml의 모든 가드 조건을 실제로 적용한다.
reason_no_trade는 명확한 코드로만 반환한다.
"""
import time
from config import cfg


LIVE_BLOCKER_CODES = (
    'MODE_GUARD', 'KEY_MISSING', 'STALE_QUOTE', 'LOW_SURPLUS',
    'LOW_EXPECTED_PROFIT', 'LOW_DEPTH', 'WIDE_SPREAD',
    'INVENTORY_SHORTAGE', 'MIN_ORDER_FAIL', 'DAILY_LOSS_LIMIT',
    'MAX_TRADES_LIMIT', 'COOLDOWN', 'PARTIAL_RISK_ACTIVE',
    'CONFIG_LIVE_DISABLED', 'ORDER_TRACKER_ACTIVE', 'EMERGENCY_PENDING',
    'EMERGENCY_FAILED', 'ORDER_LEDGER_UNSYNCED', 'EMERGENCY_DISABLED',
    'EMERGENCY_REQUIRED', 'EMERGENCY_AUTO_EXECUTE_DISABLED',
    'EMERGENCY_ATTEMPTED_ALREADY',
    'EMERGENCY_LIMIT_EXCEEDED', 'EMERGENCY_SLIPPAGE_TOO_HIGH',
    'PAIR_DISABLED', 'BITHUMB_KEY_MISSING', 'BITHUMB_PRIVATE_DISABLED',
    'BITHUMB_LIVE_DISABLED', 'UPBIT_BITHUMB_LIVE_DISABLED',
    'ICEBERG_REQUIRED', 'BLOCK_LARGE_ORDER_WITHOUT_ICEBERG',
    'ICEBERG_EXECUTION_DISABLED',
)


class RiskGuard:
    """
    check_trade() 반환값:
      True  – 진입 가능 (reason_no_trade = 'OK')
      False – 진입 불가 (reason_no_trade = 구체적 사유)

    사유 코드:
      OK / LOW_SURPLUS / LOW_EXPECTED_PROFIT / STALE_QUOTE / WIDE_SPREAD
      LOW_DEPTH / FX_UNTRUSTED / INVENTORY_SHORTAGE / COOLDOWN
      DAILY_LOSS_LIMIT / MODE_GUARD
    """

    def __init__(self):
        self._last_fail_time:    float = 0.0
        self._consecutive_fails: int   = 0
        self._daily_loss_krw:    float = 0.0
        self._day_start:         float = time.time()

    @staticmethod
    def iceberg_policy(order_krw: float) -> dict:
        """Expose placeholder policy without changing the live execution flow."""
        from iceberg_planner import IcebergPlanner
        return IcebergPlanner().build_placeholder_plan({'order_krw': order_krw}, cfg)

    @staticmethod
    def selected_required_assets(calc_result: dict) -> dict:
        """Prefer direction-aware assets while preserving legacy result support."""
        selected = calc_result.get('selected_required_assets')
        if isinstance(selected, dict):
            return selected
        return {
            'upbit_krw': float(calc_result.get('required_upbit_balance_krw', 0) or 0),
            'binance_usdt': float(calc_result.get('required_binance_balance_usdt', 0) or 0),
        }

    @staticmethod
    def live_order_blockers(order_tracker_state: dict) -> list[str]:
        """Block fresh entries while a partial-risk ledger needs operator review."""
        if not order_tracker_state:
            return []
        blockers = []
        status = order_tracker_state.get('status')
        emergency_required = bool(order_tracker_state.get('emergency_required'))
        if status == 'PARTIAL_RISK' or emergency_required:
            blockers.append('EMERGENCY_REQUIRED')
        if emergency_required and not cfg.emergency_liquidation_enabled:
            blockers.append('EMERGENCY_DISABLED')
        if emergency_required and not cfg.emergency_auto_execute:
            blockers.append('EMERGENCY_AUTO_EXECUTE_DISABLED')
        if emergency_required and order_tracker_state.get('emergency_attempted'):
            blockers.append('EMERGENCY_ATTEMPTED_ALREADY')
        if status in ('EMERGENCY_PENDING', 'EMERGENCY_FAILED'):
            blockers.append(status)
        if status == 'EMERGENCY_FAILED':
            blockers.append('EMERGENCY_FAILED')
        return list(dict.fromkeys(blockers))

    # ──────────────────────────────────────────────────────────────────────
    # 공개 API
    # ──────────────────────────────────────────────────────────────────────

    def check_trade(self, calc_result: dict) -> bool:
        """
        calc_result를 읽어 진입 가능 여부를 판단한다.
        calc_result['reason_no_trade']를 직접 수정한다.
        """
        self._reset_daily_if_needed()
        calc_result.setdefault(
            'selected_required_assets', self.selected_required_assets(calc_result)
        )

        def reject(reason: str) -> bool:
            calc_result['reason_no_trade'] = reason
            return False

        # 1. MODE_GUARD: enable_live_trading이 false인데 live 모드면 차단
        if cfg.mode in ('tiny_live', 'live') and not cfg.enable_live_trading:
            return reject('MODE_GUARD')

        # 2. FX 신뢰성 – calc_result에 fx_ok 필드가 있으면 참조
        if calc_result.get('fx_status') not in (None, 'OK'):
            return reject('FX_UNTRUSTED')

        # 3. STALE_QUOTE – 호가 타임스탬프 검사
        now_ms = time.time() * 1000
        for side in ('upbit_ts', 'binance_ts'):
            ts = calc_result.get(side)
            if ts is not None:
                age_ms = now_ms - ts * 1000
                if age_ms > cfg.stale_quote_ms:
                    return reject('STALE_QUOTE')

        # 4. WIDE_SPREAD – Upbit/Binance 스프레드 검사
        u_bid, u_ask = calc_result.get('upbit_bid'), calc_result.get('upbit_ask')
        b_bid, b_ask = calc_result.get('binance_bid'), calc_result.get('binance_ask')
        if u_bid and u_ask and u_bid > 0:
            u_spread_bp = (u_ask - u_bid) / u_bid * 10000
            if u_spread_bp > cfg.max_spread_bp:
                return reject('WIDE_SPREAD')
        if b_bid and b_ask and b_bid > 0:
            b_spread_bp = (b_ask - b_bid) / b_bid * 10000
            if b_spread_bp > cfg.max_spread_bp:
                return reject('WIDE_SPREAD')

        # 5. LOW_DEPTH – fillable qty 검사
        max_qty = calc_result.get('max_fillable_qty', 0)
        # 목표 수량: max_one_trade_krw / Upbit ask
        if u_ask and u_ask > 0:
            target_qty = cfg.max_one_trade_krw / u_ask
            if max_qty < target_qty / cfg.min_depth_multiplier:
                return reject('LOW_DEPTH')

        # 6. LOW_SURPLUS
        if calc_result.get('best_net_surplus_bp', -9999) < cfg.min_net_surplus_bp:
            return reject('LOW_SURPLUS')

        # 7. LOW_EXPECTED_PROFIT
        if calc_result.get('net_expected_profit_krw', 0) < cfg.min_expected_profit_krw:
            return reject('LOW_EXPECTED_PROFIT')

        # 8. INVENTORY_SHORTAGE – 외부에서 주입된 경우
        if calc_result.get('reason_no_trade') == 'INVENTORY_SHORTAGE':
            return False

        # 9. COOLDOWN – 최근 실패 후 쿨다운 중
        if time.time() - self._last_fail_time < cfg.cooldown_sec:
            return reject('COOLDOWN')

        # 10. DAILY_LOSS_LIMIT
        if self._daily_loss_krw >= cfg.daily_loss_limit_krw:
            return reject('DAILY_LOSS_LIMIT')

        # ── 통과 ──────────────────────────────────────────────────────────
        self._consecutive_fails = 0
        calc_result['reason_no_trade'] = 'OK'
        return True

    def record_trade_result(self, pnl_krw: float) -> None:
        """trade 결과(pnl)를 기록한다. 손실 누적 및 쿨다운 업데이트."""
        if pnl_krw < 0:
            self._daily_loss_krw += abs(pnl_krw)
            self._consecutive_fails += 1
            if self._consecutive_fails >= cfg.consecutive_fail_limit:
                self._last_fail_time = time.time()
        else:
            self._consecutive_fails = 0

    # ──────────────────────────────────────────────────────────────────────
    # 내부 헬퍼
    # ──────────────────────────────────────────────────────────────────────

    def _reset_daily_if_needed(self) -> None:
        """자정 지나면 일일 손실 초기화."""
        import datetime
        now = datetime.datetime.now()
        start = datetime.datetime.fromtimestamp(self._day_start)
        if now.date() > start.date():
            self._daily_loss_krw = 0.0
            self._day_start = time.time()
