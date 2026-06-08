#!/usr/bin/env python
"""Acceptance checks for a running KARB dashboard/API instance.

This script is intentionally read-only. It talks to the existing web API and
reports whether paper routing, telemetry, and live/tiny-live guards look sane.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


REQUIRED_ENTRY_REASONS = (
    "NORMAL_GO",
    "RECHECK_ACTIONABLE",
    "WIDE_SPREAD_RECHECK_ACTIONABLE",
    "UNKNOWN",
)

MOCK_SYMBOLS = {"MOCK", "MOCK2", "NORMAL"}


@dataclass
class CheckResult:
    code: str
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = {"code": self.code}
        if self.message:
            data["message"] = self.message
        if self.details:
            data["details"] = self.details
        return data


class AcceptanceCheck:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.base_url = args.base_url.rstrip("/")
        self.passes: list[CheckResult] = []
        self.warnings: list[CheckResult] = []
        self.failures: list[CheckResult] = []
        self.summary: dict[str, Any] = {}
        self.api: dict[str, Any] = {}

    def run(self) -> int:
        self._fetch_required_apis()
        if not self.failures:
            self._check_health()
            self._check_direct_rest()
            self._check_api_429()
            self._check_loop_latency()
            self._check_paper_entry_route()
            self._check_recent_trades()
            self._check_execution_plan_fields()
            self._check_entry_reason_summary()
            self._check_recheck_health()
            self._check_completed_handoff()
            self._check_live_readiness()
            self._check_tiny_live()
            self._check_execution_calibration()
            self._check_tiny_live_preflight()
            self._check_entry_diagnostics()
            self._check_notional_sweep()
            self._check_trading_capital()
        self._build_summary()
        self._print()
        return 1 if self.failures else 0

    def _fetch_required_apis(self) -> None:
        required = {
            "health": "/api/health",
            "telemetry": "/api/telemetry",
            "trades": "/api/trades/recent",
            "performance": "/api/performance/pairs",
            "stale_recheck": "/api/stale-recheck/status",
            "execution_calibration": "/api/execution-calibration/status",
            "notional_sweep": "/api/notional-sweep",
            "tiny_live_preflight": "/api/tiny-live/preflight",
            "entry_diagnostics": "/api/entry-diagnostics",
            "data": "/api/data",
            "trading_capital": "/api/trading-capital",
        }
        optional = {
            "live_readiness": "/api/live/readiness",
            "tiny_live_status": "/api/tiny-live/status",
        }
        for name, path in required.items():
            ok, payload, error = self._get_json(path)
            if ok:
                self.api[name] = payload
            else:
                self._fail("API_REQUEST_FAILED", f"{path}: {error}", {"api": name, "path": path})
        for name, path in optional.items():
            ok, payload, error = self._get_json(path)
            if ok:
                self.api[name] = payload
            else:
                self._warn("OPTIONAL_API_UNAVAILABLE", f"{path}: {error}", {"api": name, "path": path})

    def _get_json(self, path: str) -> tuple[bool, Any, str]:
        url = self.base_url + path
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.args.timeout) as resp:
                raw = resp.read()
                status = getattr(resp, "status", 200)
            if status < 200 or status >= 300:
                return False, None, f"HTTP {status}"
            try:
                return True, json.loads(raw.decode("utf-8")), ""
            except json.JSONDecodeError as exc:
                return False, None, f"invalid JSON: {exc}"
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:240]
            return False, None, f"HTTP {exc.code}: {body}"
        except urllib.error.URLError as exc:
            return False, None, str(exc.reason)
        except TimeoutError:
            return False, None, "timeout"
        except Exception as exc:  # Keep the checker stable for diagnostics.
            return False, None, f"{type(exc).__name__}: {exc}"

    def _telemetry(self) -> dict[str, Any]:
        payload = self.api.get("telemetry", {})
        if isinstance(payload.get("telemetry"), dict):
            return payload["telemetry"]
        return payload if isinstance(payload, dict) else {}

    def _check_health(self) -> None:
        health = self.api.get("health", {})
        if health.get("status") == "ok":
            self._pass("HEALTH_OK")
        else:
            self._fail("HEALTH_NOT_OK", f"status={health.get('status')}", {"health": health})

    def _check_direct_rest(self) -> None:
        count = _to_number(self._telemetry().get("rest_direct_call_count"), 0)
        details = {"rest_direct_call_count": count}
        if count == 0:
            self._pass("REST_DIRECT_ZERO", details=details)
        elif self.args.fail_on_rest_direct:
            self._fail("REST_DIRECT_CALL_DETECTED", f"count={count}", details)
        else:
            self._warn("REST_DIRECT_CALL_DETECTED", f"count={count}", details)

    def _check_api_429(self) -> None:
        telemetry = self._telemetry()
        count = _to_number(telemetry.get("api_429_count"), 0)
        if count == 0:
            rate_status = telemetry.get("rate_limiter_status")
            if isinstance(rate_status, dict):
                for item in rate_status.values():
                    if isinstance(item, dict):
                        count += _to_number(item.get("api_429_count"), 0)
        details = {"api_429_count": count}
        if count == 0:
            self._pass("API_429_ZERO", details=details)
        elif self.args.fail_on_api_429:
            self._fail("API_429_DETECTED", f"count={count}", details)
        else:
            self._warn("API_429_DETECTED", f"count={count}", details)

    def _check_loop_latency(self) -> None:
        p95 = _to_number(self._telemetry().get("p95_loop_latency_ms"), 0)
        details = {"p95_loop_latency_ms": p95, "max_p95_loop_ms": self.args.max_p95_loop_ms}
        if p95 <= self.args.max_p95_loop_ms:
            self._pass("LOOP_LATENCY_OK", f"p95={p95:.2f}ms", details)
        else:
            self._fail("LOOP_LATENCY_TOO_HIGH", f"p95={p95:.2f}ms", details)
        decision_p95 = _to_number(self._telemetry().get("entry_decision_wait_p95_ms"), 0)
        if decision_p95 > 700:
            self._warn("ENTRY_DECISION_WAIT_HIGH", f"p95={decision_p95:.2f}ms", {"entry_decision_wait_p95_ms": decision_p95})
        refresh_count = _to_number(self._telemetry().get("stale_leg_priority_refresh_request_count"), 0)
        fast_routes = _to_number(self._telemetry().get("completed_handoff_fast_route_count"), 0)
        if refresh_count > 0 and fast_routes == 0:
            self._warn(
                "STALE_LEG_REFRESH_NOT_HELPING",
                f"requests={refresh_count} fast_routes=0",
                {"stale_leg_priority_refresh_request_count": refresh_count},
            )

    def _check_paper_entry_route(self) -> None:
        telemetry = self._telemetry()
        attempts = int(_to_number(telemetry.get("paper_entry_attempt_count"), 0))
        success = int(_to_number(telemetry.get("paper_entry_success_count"), 0))
        blocked = int(_to_number(telemetry.get("paper_entry_blocked_count"), 0))
        arb_fill_count = int(_to_number(telemetry.get("paper_arb_fill_count"), 0))
        blocker = str(telemetry.get("paper_entry_last_blocker") or "")
        quote_age = _to_optional_number(telemetry.get("paper_entry_last_quote_age_ms"))
        details = {
            "attempts": attempts,
            "success": success,
            "blocked": blocked,
            "paper_arb_fill_count": arb_fill_count,
            "last_blocker": blocker,
            "last_quote_age_ms": quote_age,
            "last_quote_age_source": telemetry.get("paper_entry_last_quote_age_source"),
            "paper_engine_reject_last_reason": telemetry.get("paper_engine_reject_last_reason"),
            "paper_engine_reject_last_detail": telemetry.get("paper_engine_reject_last_detail"),
        }
        if attempts > 0 and success == 0:
            self._fail(
                "PAPER_ENTRY_ATTEMPT_BUT_NO_SUCCESS",
                f"attempts={attempts} success={success} blocker={blocker} "
                f"reason={details['paper_engine_reject_last_reason']}",
                details,
            )
            return
        if success > 0 and arb_fill_count == 0:
            self._fail(
                "PAPER_ENTRY_SUCCESS_WITHOUT_ARB_FILL",
                f"success={success} paper_arb_fill_count={arb_fill_count}",
                details,
            )
            return
        if self.args.require_paper_trade and success < self.args.min_paper_entry_success:
            self._fail(
                "PAPER_ENTRY_SUCCESS_TOO_LOW",
                f"success={success} min={self.args.min_paper_entry_success}",
                details,
            )
            return
        if (
            quote_age is not None
            and quote_age > self.args.max_paper_entry_quote_age_ms
            and blocker == "ENTRY_QUOTE_TOO_OLD"
            and success == 0
        ):
            self._fail(
                "PAPER_ENTRY_QUOTE_AGE_TOO_HIGH",
                f"age={quote_age:.1f}ms max={self.args.max_paper_entry_quote_age_ms:.1f}ms",
                details,
            )
            return
        self._pass(
            "PAPER_ENTRY_ROUTE_OK",
            f"attempts={attempts} success={success} blocked={blocked}",
            details,
        )

    def _check_recent_trades(self) -> None:
        trades = self._extract_trades(self.api.get("trades", {}))
        visible_mock = []
        real_trades = []
        for trade in trades:
            symbol = str(trade.get("symbol") or "").upper()
            is_mock = bool(trade.get("is_mock") or trade.get("test_only"))
            if symbol in MOCK_SYMBOLS or is_mock:
                visible_mock.append(symbol or "UNKNOWN")
            else:
                real_trades.append(trade)
        if visible_mock:
            self._fail("MOCK_TRADE_VISIBLE", ",".join(visible_mock), {"symbols": visible_mock})
        else:
            self._pass("MOCK_TRADE_FILTER_OK")
        if real_trades:
            self._pass("REAL_RECENT_TRADES_PRESENT", f"count={len(real_trades)}", {"count": len(real_trades)})
        elif self.args.require_paper_trade:
            self._fail("NO_REAL_RECENT_TRADES", "require-paper-trade is set", {"count": 0})
        else:
            self._warn("NO_REAL_RECENT_TRADES", "count=0", {"count": 0})
        bad_arb_sl = [
            {
                "trade_id": trade.get("trade_id"),
                "symbol": trade.get("symbol"),
                "pair_id": trade.get("pair_id"),
                "exit_reason": trade.get("exit_reason"),
            }
            for trade in real_trades
            if str(trade.get("pair_id") or "") in {"UPBIT_BINANCE", "UPBIT_BITHUMB"}
            and trade.get("execution_model") == "INVENTORY_ARBITRAGE_FILL"
            and trade.get("exit_reason") == "SL"
        ]
        if bad_arb_sl:
            self._fail("ARB_FILL_MARKED_AS_SL", "inventory arbitrage fill closed as SL", {"trades": bad_arb_sl[:5]})
        else:
            self._pass("ARB_FILL_NOT_MARKED_AS_SL")
        self._check_old_open_trades()

    def _check_execution_plan_fields(self) -> None:
        trades = self._extract_trades(self.api.get("trades", {}))
        real_trades = [
            trade for trade in trades
            if not bool(trade.get("is_mock") or trade.get("test_only"))
            and str(trade.get("symbol") or "").upper() not in MOCK_SYMBOLS
        ]
        arb_fills = [
            trade for trade in real_trades
            if trade.get("execution_model") == "INVENTORY_ARBITRAGE_FILL"
            and trade.get("exit_reason") == "ARB_FILLED"
        ]
        zero_fee = [
            trade.get("trade_id") for trade in arb_fills
            if _to_number(trade.get("total_fee_krw", trade.get("entry_fee_krw")), 0) <= 0
        ]
        if zero_fee:
            self._fail("FEE_ZERO_ON_FILLED_TRADE", ",".join(map(str, zero_fee[:5])), {"trade_ids": zero_fee[:5]})
        missing_ratio = [
            trade.get("trade_id") for trade in arb_fills
            if trade.get("expected_fill_ratio_buy") is None or trade.get("expected_fill_ratio_sell") is None
        ]
        if missing_ratio:
            self._warn("FILL_RATIO_MISSING", ",".join(map(str, missing_ratio[:5])), {"trade_ids": missing_ratio[:5]})
        missing_slip_source = [
            trade.get("trade_id") for trade in arb_fills
            if not trade.get("slippage_source")
        ]
        if missing_slip_source:
            self._warn("SLIPPAGE_SOURCE_MISSING", ",".join(map(str, missing_slip_source[:5])), {"trade_ids": missing_slip_source[:5]})
        missing_leg_age = [
            trade.get("trade_id") for trade in arb_fills
            if trade.get("buy_leg_quote_age_ms") is None or trade.get("sell_leg_quote_age_ms") is None
        ]
        if missing_leg_age:
            self._warn("LEG_AGE_FIELD_MISSING", ",".join(map(str, missing_leg_age[:5])), {"trade_ids": missing_leg_age[:5]})
        too_old_leg = [
            {
                "trade_id": trade.get("trade_id"),
                "symbol": trade.get("symbol"),
                "max_leg_quote_age_ms": trade.get("max_leg_quote_age_ms"),
                "cap_ms": trade.get("leg_quote_age_cap_ms", 1200),
            }
            for trade in arb_fills
            if trade.get("max_leg_quote_age_ms") is not None
            and _to_number(trade.get("max_leg_quote_age_ms"), 0) > _to_number(trade.get("leg_quote_age_cap_ms"), 1200)
        ]
        if too_old_leg:
            self._fail("ARB_FILL_LEG_QUOTE_TOO_OLD", details={"trades": too_old_leg[:5]})
        non_vwap = [
            trade.get("trade_id") for trade in arb_fills
            if trade.get("slippage_source") and trade.get("slippage_source") != "ORDERBOOK_VWAP"
        ]
        if non_vwap:
            self._warn("SLIPPAGE_NOT_ORDERBOOK_VWAP", ",".join(map(str, non_vwap[:5])), {"trade_ids": non_vwap[:5]})
        slip_values = [
            round(_to_number(trade.get("dynamic_slippage_bp", trade.get("total_slippage_bp")), -1), 6)
            for trade in arb_fills[:20]
        ]
        if len(slip_values) >= 10 and len(set(slip_values)) == 1 and slip_values[0] == 5.0:
            self._warn("SLIPPAGE_APPEARS_STATIC", "10+ ARB_FILLED trades all show 5.0bp", {"sample": slip_values[:10]})
        pnl_diffs = [
            {
                "trade_id": trade.get("trade_id"),
                "diff": abs(
                    _to_number(trade.get("realized_pnl_krw"), 0)
                    - _to_number(trade.get("planned_expected_net_profit_krw"), 0)
                ),
            }
            for trade in arb_fills
            if trade.get("planned_expected_net_profit_krw") is not None
        ]
        large_diff = [row for row in pnl_diffs if row["diff"] > 1000]
        if large_diff:
            self._warn("PLANNED_ACTUAL_PNL_DIFF_LARGE", details={"items": large_diff[:5]})
        if arb_fills and not (zero_fee or missing_ratio or missing_slip_source or non_vwap):
            self._pass("EXECUTION_PLAN_FIELDS_OK", f"arb_fills={len(arb_fills)}")

    def _extract_trades(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            trades = payload.get("trades") or payload.get("recent_trades") or []
        else:
            trades = payload
        if not isinstance(trades, list):
            return []
        return [t for t in trades if isinstance(t, dict)]

    def _check_entry_reason_summary(self) -> None:
        perf = self.api.get("performance", {})
        by_reason = perf.get("by_entry_reason") if isinstance(perf, dict) else None
        if not isinstance(by_reason, dict):
            self._fail("ENTRY_REASON_SUMMARY_MISSING")
            return
        missing = [key for key in REQUIRED_ENTRY_REASONS if key not in by_reason]
        if missing:
            self._warn("ENTRY_REASON_KEY_MISSING", ",".join(missing), {"missing": missing})
        else:
            self._pass("ENTRY_REASON_SUMMARY_OK")

    def _check_old_open_trades(self) -> None:
        payload = self.api.get("data", {})
        state = payload.get("state") if isinstance(payload, dict) else {}
        if not isinstance(state, dict):
            return
        open_trades = state.get("open_trades_detail") or state.get("open_trade_details") or []
        if not isinstance(open_trades, list):
            return
        now = _to_number(state.get("updated_at"), 0)
        old = []
        for trade in open_trades:
            if not isinstance(trade, dict):
                continue
            entered_at = _to_optional_number(trade.get("entered_at") or trade.get("entry_time"))
            holding = _to_optional_number(trade.get("holding_sec"))
            if holding is None and entered_at and now:
                holding = max(0.0, now - entered_at)
            if holding is not None and holding > 60:
                old.append({
                    "trade_id": trade.get("trade_id"),
                    "symbol": trade.get("symbol"),
                    "pair_id": trade.get("pair_id"),
                    "holding_sec": holding,
                })
        if old:
            self._warn("PAPER_OPEN_TRADE_TOO_OLD", f"count={len(old)}", {"trades": old[:5]})

        telemetry = self._telemetry()
        sl_count = int(_to_number(telemetry.get("paper_exit_sl_count"), 0))
        arb_fill_count = int(_to_number(telemetry.get("paper_arb_fill_count"), 0))
        if sl_count > 0 and arb_fill_count > 0:
            self._warn(
                "PAPER_SL_PRESENT_CHECK_REQUIRED",
                f"paper_exit_sl_count={sl_count} paper_arb_fill_count={arb_fill_count}",
                {"paper_exit_sl_count": sl_count, "paper_arb_fill_count": arb_fill_count},
            )

    def _check_recheck_health(self) -> None:
        payload = self.api.get("stale_recheck", {})
        status = payload.get("stale_recheck_status") if isinstance(payload, dict) else None
        if isinstance(status, dict):
            source = status
        elif isinstance(payload, dict):
            source = payload
        else:
            source = {}
        health = source.get("stale_recheck_health") or source.get("health") or "UNKNOWN"
        details = {
            "health": health,
            "avg_fetch_ms": source.get("avg_fetch_ms") or source.get("avg_elapsed_fetch_ms"),
            "avg_decision_wait_ms": source.get("avg_decision_wait_ms"),
            "actionable_fast_pass_count": source.get("actionable_fast_pass_count"),
        }
        if health in ("GOOD", "WATCH"):
            self._pass("STALE_RECHECK_HEALTH_OK", f"health={health}", details)
        elif health == "UNKNOWN":
            self._warn("STALE_RECHECK_HEALTH_UNKNOWN", details=details)
        else:
            self._warn("STALE_RECHECK_BAD", f"health={health}", details)

    def _check_completed_handoff(self) -> None:
        telemetry = self._telemetry()
        fields = (
            "completed_handoff_entry_route_count",
            "completed_handoff_entry_skip_old_count",
            "completed_handoff_entry_duplicate_skip_count",
        )
        missing = [field for field in fields if field not in telemetry]
        if missing:
            self._warn("COMPLETED_HANDOFF_TELEMETRY_MISSING", ",".join(missing), {"missing": missing})
        else:
            self._pass("COMPLETED_HANDOFF_TELEMETRY_OK")

    def _check_live_readiness(self) -> None:
        payload = self.api.get("live_readiness")
        if not isinstance(payload, dict):
            return
        blockers = _collect_strings(payload.get("blockers"))
        enabled = _bool_from_payload(payload, ("live_enabled", "live_order_enabled", "enable_live_trading"))
        if not enabled:
            self._info("LIVE_DISABLED", "live_enabled=false")
        if "EXECUTOR_NOT_FOUND" in blockers:
            if enabled:
                self._fail("LIVE_ENABLED_BUT_EXECUTOR_NOT_FOUND", details={"blockers": blockers})
            else:
                self._warn("LIVE_EXECUTOR_NOT_FOUND", details={"blockers": blockers})
        elif any("KEY_MISSING" in item for item in blockers):
            self._info("LIVE_API_KEY_MISSING", "expected when keys are absent", {"blockers": blockers})
        else:
            self._pass("LIVE_READINESS_CHECKED", details={"blockers": blockers, "enabled": enabled})

    def _check_tiny_live(self) -> None:
        payload = self.api.get("tiny_live_status")
        if not isinstance(payload, dict):
            return
        blockers = _collect_strings(payload.get("blockers"))
        enabled = _bool_from_payload(payload, ("tiny_live_enabled", "enabled"))
        if not enabled:
            self._info("TINY_LIVE_DISABLED", "tiny_live_enabled=false")
        executor_problem = [
            item for item in blockers
            if "EXECUTOR" in item or "ORDER" in item or "CLIENT" in item
        ]
        if enabled and executor_problem:
            self._fail("TINY_LIVE_EXECUTOR_PROBLEM", ",".join(executor_problem), {"blockers": blockers})
        else:
            self._pass("TINY_LIVE_STATUS_CHECKED", details={"blockers": blockers, "enabled": enabled})
        telemetry = self._telemetry()
        mode = (self.api.get("data", {}).get("state", {}) or {}).get("mode")
        submit_attempts = (
            _to_number(telemetry.get("tiny_live_order_submit_attempt_count"), 0)
            + _to_number(telemetry.get("live_order_submit_attempt_count"), 0)
        )
        if mode in {"tiny_live", "live"} and submit_attempts > 0 and telemetry.get("leg_quote_last_blocker"):
            self._fail("SUBMIT_WITH_STALE_LEG", details={
                "mode": mode,
                "submit_attempts": submit_attempts,
                "leg_quote_last_blocker": telemetry.get("leg_quote_last_blocker"),
            })

    def _check_execution_calibration(self) -> None:
        payload = self.api.get("execution_calibration")
        if not isinstance(payload, dict):
            return
        enabled = bool(payload.get("enabled"))
        details = {
            "enabled": enabled,
            "tiny_live_enabled": bool(payload.get("tiny_live_enabled")),
            "trade_count": payload.get("trade_count", 0),
            "max_order_krw": payload.get("max_order_krw"),
            "blockers": payload.get("blockers", []),
        }
        if not enabled:
            self._info("CALIBRATION_DISABLED", "tiny-live calibration is off by default", details)
            return
        if not payload.get("tiny_live_enabled"):
            self._fail("CALIBRATION_ENABLED_BUT_TINY_LIVE_DISABLED", details=details)
        blockers = _collect_strings(payload.get("blockers"))
        if any("KEY_MISSING" in item for item in blockers):
            self._fail("CALIBRATION_ENABLED_BUT_API_KEY_MISSING", details=details)
        if _to_number(payload.get("max_order_krw"), 0) > 10000:
            self._fail("CALIBRATION_MAX_ORDER_TOO_HIGH", details=details)
        if payload.get("tiny_live_max_leg_quote_age_ms") is None:
            self._fail("CALIBRATION_LEG_QUOTE_CAP_MISSING", details=payload)
        if _to_number(payload.get("trade_count"), 0) > 0:
            if payload.get("last_pnl_diff_krw") is None:
                self._fail("CALIBRATION_PNL_DIFF_MISSING", details=payload)
            if payload.get("avg_actual_slippage_bp") is None:
                self._warn("CALIBRATION_ACTUAL_SLIPPAGE_MISSING", details=payload)
            if payload.get("avg_pnl_diff_krw") is None:
                self._warn("CALIBRATION_AVG_PNL_DIFF_MISSING", details=payload)
        self._pass("CALIBRATION_STATUS_CHECKED", details=details)

    def _check_tiny_live_preflight(self) -> None:
        payload = self.api.get("tiny_live_preflight", {})
        if not isinstance(payload, dict):
            self._fail("TINY_LIVE_PREFLIGHT_API_FAIL")
            return
        config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        enabled = bool(config.get("tiny_live_enabled"))
        calibration_enabled = bool(config.get("calibration_enabled"))
        blockers = _collect_strings(payload.get("blockers"))
        candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
        executor = payload.get("executor") if isinstance(payload.get("executor"), dict) else {}
        can_submit = bool(payload.get("can_submit"))
        selection = payload.get("candidate_selection") if isinstance(payload.get("candidate_selection"), dict) else {}
        details = {
            "enabled": enabled,
            "calibration_enabled": calibration_enabled,
            "can_submit": can_submit,
            "blockers": blockers,
            "candidate": candidate,
            "candidate_selection": selection,
        }
        allowed_symbols = {str(item).upper() for item in config.get("allowed_symbols", []) or []}
        if "NO_ELIGIBLE_CANDIDATE" in blockers:
            self._info("NO_ELIGIBLE_CANDIDATE", "no preflight candidate passed filters", details)
        if candidate:
            symbol = str(candidate.get("symbol", "")).upper()
            if allowed_symbols and symbol not in allowed_symbols:
                self._fail("PREFLIGHT_CANDIDATE_NOT_ALLOWED", details=details)
            if _to_number(candidate.get("expected_net_profit_krw"), 0) <= 0:
                self._fail("PREFLIGHT_CANDIDATE_NET_NOT_POSITIVE", details=details)
            if candidate.get("leg_freshness_ok") is False:
                self._fail("PREFLIGHT_CANDIDATE_STALE_LEG", details=details)
            if (
                candidate.get("max_leg_quote_age_ms") is not None
                and candidate.get("leg_quote_age_cap_ms") is not None
                and _to_number(candidate.get("max_leg_quote_age_ms"), 0) > _to_number(candidate.get("leg_quote_age_cap_ms"), 0)
            ):
                self._fail("PREFLIGHT_CANDIDATE_STALE_LEG", details=details)
        if not enabled:
            self._info("TINY_LIVE_PREFLIGHT_DISABLED", "tiny_live_enabled=false", details)
            return
        if not calibration_enabled:
            self._info("TINY_LIVE_PREFLIGHT_CALIBRATION_DISABLED", "calibration disabled", details)
            return
        if any("KEY_MISSING" in item for item in blockers):
            self._fail("TINY_LIVE_KEY_MISSING", details=details)
        if _to_number(config.get("max_order_krw"), 0) > 10000:
            self._fail("TINY_LIVE_PREFLIGHT_MAX_ORDER_TOO_HIGH", details=details)
        if can_submit and not bool(executor.get("submit_ready")):
            self._fail("TINY_LIVE_PREFLIGHT_EXECUTOR_NOT_READY", details=details)
        if can_submit and not bool(payload.get("balance_ok")):
            self._fail("TINY_LIVE_PREFLIGHT_BALANCE_NOT_OK", details=details)
        if can_submit and _to_number(candidate.get("expected_net_profit_krw"), 0) <= 0:
            self._fail("TINY_LIVE_PREFLIGHT_NET_NOT_POSITIVE", details=details)
        if (
            can_submit
            and candidate.get("max_leg_quote_age_ms") is not None
            and _to_number(candidate.get("max_leg_quote_age_ms"), 0) > _to_number(candidate.get("leg_quote_age_cap_ms"), 0)
        ):
            self._fail("TINY_LIVE_PREFLIGHT_STALE_LEG", details=details)
        if bool(config.get("one_shot_first", True)) and _to_number(payload.get("session_submit_count"), 0) > 1:
            self._fail("TINY_LIVE_PREFLIGHT_ONE_SHOT_EXCEEDED", details=details)
        if not can_submit and not blockers:
            self._fail("PREFLIGHT_INCONSISTENT_STATE", details=details)
        if not self.failures:
            self._pass("TINY_LIVE_PREFLIGHT_CHECKED", details=details)

    def _check_entry_diagnostics(self) -> None:
        payload = self.api.get("entry_diagnostics", {})
        if not isinstance(payload, dict) or not payload.get("ok", False):
            self._fail("ENTRY_DIAGNOSTICS_API_FAIL", details={"payload": payload})
            return
        telemetry = self._telemetry()
        recovery = payload.get("recovery") if isinstance(payload.get("recovery"), dict) else {}
        stale_recovery = (
            payload.get("profitable_stale_recovery")
            if isinstance(payload.get("profitable_stale_recovery"), dict)
            else {}
        )
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        details = {"recovery": recovery, "profitable_stale_recovery": stale_recovery, "summary": summary}
        if bool(telemetry.get("entry_recovery_enabled", False)) and _to_number(telemetry.get("rest_direct_call_count"), 0) > 0:
            self._fail("ENTRY_RECOVERY_DIRECT_REST_INCREASED", details=details)
        if _to_number(telemetry.get("stale_recheck_queue_size"), 0) > _to_number(recovery.get("max_queue_size"), 50):
            self._warn("ENTRY_RECOVERY_QUEUE_OVER_MAX", details=details)
        if _to_number(stale_recovery.get("queue_size"), 0) > _to_number(stale_recovery.get("max_queue_size"), 100):
            self._warn("PROFITABLE_STALE_QUEUE_OVER_MAX", details=details)
        stale_positive = _to_number(
            stale_recovery.get("stale_quote_positive_count", telemetry.get("stale_quote_positive_count")),
            0,
        )
        stale_requests = _to_number(
            stale_recovery.get("request_count", telemetry.get("profitable_stale_recovery_request_count")),
            0,
        )
        stale_success = _to_number(
            stale_recovery.get("success_count", telemetry.get("profitable_stale_recovery_success_count")),
            0,
        )
        paper_arb_fill_count = _to_number(telemetry.get("paper_arb_fill_count"), 0)
        if bool(stale_recovery.get("enabled")) and stale_positive > 0 and stale_requests == 0:
            self._warn("PROFITABLE_STALE_NOT_RECOVERED", details=details)
        if stale_success > 0 and paper_arb_fill_count == 0:
            self._warn("RECOVERY_SUCCESS_WITHOUT_FILL", details=details)
        if _to_number(telemetry.get("live_order_submit_attempt_count"), 0) > 0:
            self._fail("LIVE_ORDER_SUBMIT_ATTEMPT_DETECTED", details=details)
        if _to_number(telemetry.get("tiny_live_order_submit_attempt_count"), 0) > 0:
            self._fail("TINY_LIVE_ORDER_SUBMIT_ATTEMPT_DETECTED", details=details)
        stale_direct_fills = [
            {
                "trade_id": trade.get("trade_id"),
                "symbol": trade.get("symbol"),
                "entry_reason": trade.get("entry_reason"),
                "reason_no_trade": trade.get("reason_no_trade"),
                "completed_handoff_reason": trade.get("completed_handoff_reason"),
            }
            for trade in self._extract_trades(self.api.get("trades", {}))
            if trade.get("exit_reason") == "ARB_FILLED"
            and trade.get("reason_no_trade") == "STALE_QUOTE"
            and not trade.get("completed_handoff_reason")
        ]
        if stale_direct_fills:
            self._fail("STALE_QUOTE_DIRECT_FILL", details={"trades": stale_direct_fills[:5]})
        if bool(summary.get("likely_overblocking")):
            self._warn("ENTRY_OVERBLOCKING_SUSPECTED", details=details)
        self._pass("ENTRY_DIAGNOSTICS_CHECKED", details=details)

    def _check_notional_sweep(self) -> None:
        payload = self.api.get("notional_sweep", {})
        if not isinstance(payload, dict) or not payload.get("ok", False):
            self._fail("NOTIONAL_SWEEP_API_FAIL", details={"payload": payload})
            return
        if payload.get("enabled") is False:
            self._info("NOTIONAL_SWEEP_DISABLED")
            return
        items = payload.get("items") or []
        if not isinstance(items, list) or not items:
            self._pass("NOTIONAL_SWEEP_EMPTY_OK")
            return
        missing_metrics = []
        zero_fee = []
        missing_slippage_source = []
        depth_limited = []
        for item in items:
            if not isinstance(item, dict):
                continue
            for row in item.get("rows", []) or []:
                if not isinstance(row, dict):
                    continue
                symbol = item.get("symbol")
                notional = row.get("notional_krw")
                if _to_number(row.get("total_fee_krw"), 0) <= 0:
                    zero_fee.append({"symbol": symbol, "notional_krw": notional})
                if row.get("slippage_source") != "ORDERBOOK_VWAP":
                    missing_slippage_source.append({"symbol": symbol, "notional_krw": notional})
                for field in ("expected_net_profit_krw", "total_fee_krw", "total_slippage_bp"):
                    if row.get(field) is None:
                        missing_metrics.append({"symbol": symbol, "notional_krw": notional, "field": field})
                if _to_number(notional, 0) >= 50000 and row.get("depth_ok") is False:
                    depth_limited.append({"symbol": symbol, "notional_krw": notional, "blocker": row.get("blocker")})
        if zero_fee:
            self._fail("NOTIONAL_SWEEP_FEE_ZERO", details={"rows": zero_fee[:5]})
        if missing_slippage_source:
            self._warn("NOTIONAL_SWEEP_SLIPPAGE_SOURCE_MISSING", details={"rows": missing_slippage_source[:5]})
        if missing_metrics:
            self._warn("NOTIONAL_SWEEP_METRIC_MISSING", details={"rows": missing_metrics[:5]})
        if depth_limited:
            self._warn("NOTIONAL_SWEEP_DEPTH_LIMITED", details={"rows": depth_limited[:5]})
        if not zero_fee:
            self._pass("NOTIONAL_SWEEP_OK", f"items={len(items)}", {"item_count": len(items)})

    def _check_trading_capital(self) -> None:
        payload = self.api.get("trading_capital", {})
        if not isinstance(payload, dict) or not payload.get("ok", False):
            self._fail("TRADING_CAPITAL_API_FAIL", details={"payload": payload})
            return
        settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        fixed = _to_number(settings.get("fixed_order_krw"), 0)
        max_order = _to_number(settings.get("max_order_krw"), 0)
        session_cap = _to_number(settings.get("session_cap_krw"), 0)
        balance_ratio = _to_number(settings.get("balance_ratio"), 0)
        reinvest_ratio = _to_number(settings.get("daily_profit_reinvest_ratio"), 0)
        compounding = str(settings.get("compounding_mode", "OFF"))
        if fixed > max_order:
            self._fail("CAPITAL_FIXED_EXCEEDS_MAX", details={"fixed_order_krw": fixed, "max_order_krw": max_order})
        if session_cap > 0 and session_cap < fixed:
            self._fail("CAPITAL_SESSION_CAP_TOO_SMALL", details={"session_cap_krw": session_cap, "fixed_order_krw": fixed})
        if balance_ratio > 0.2:
            self._fail("CAPITAL_BALANCE_RATIO_TOO_HIGH", details={"balance_ratio": balance_ratio})
        if reinvest_ratio > 1:
            self._fail("CAPITAL_REINVEST_RATIO_TOO_HIGH", details={"daily_profit_reinvest_ratio": reinvest_ratio})
        if compounding == "BALANCE_RATIO" and balance_ratio <= 0:
            self._warn("CAPITAL_BALANCE_RATIO_ZERO", details={"compounding_mode": compounding, "balance_ratio": balance_ratio})
        enabled = bool(settings.get("enabled", True))
        telemetry = self._telemetry()
        tiny_or_live_enabled = any(bool(x) for x in (
            telemetry.get("tiny_live_enabled"),
            telemetry.get("live_order_enabled"),
            telemetry.get("live_mode_enabled"),
        ))
        if tiny_or_live_enabled and not enabled:
            self._fail("TINY_LIVE_OR_LIVE_WITH_CAPITAL_DISABLED", details={"settings": settings})
        trades = self._extract_trades(self.api.get("trades", {}))
        over = []
        for trade in trades:
            mode = str(trade.get("mode") or trade.get("execution_mode") or "").lower()
            notional = _to_number(trade.get("selected_notional_krw"), 0)
            if mode in {"tiny_live", "live"} and notional > max_order:
                over.append({"trade_id": trade.get("trade_id"), "mode": mode, "notional": notional})
        if over:
            self._fail("LIVE_TRADE_OVER_CAPITAL_MAX", details={"trades": over[:5], "max_order_krw": max_order})
        max_trades = _to_number(settings.get("max_trades_per_session"), 0)
        if max_trades > 0 and _to_number(runtime.get("session_trade_count"), 0) > max_trades:
            self._fail("CAPITAL_SESSION_TRADE_LIMIT_EXCEEDED", details={"runtime": runtime, "settings": settings})
        if not any(item.code.startswith("CAPITAL_") or item.code == "TINY_LIVE_OR_LIVE_WITH_CAPITAL_DISABLED" for item in self.failures):
            self._pass("TRADING_CAPITAL_OK", details={"settings": settings, "runtime": runtime})

    def _build_summary(self) -> None:
        telemetry = self._telemetry()
        self.summary = {
            "base_url": self.base_url,
            "failure_count": len(self.failures),
            "warning_count": len(self.warnings),
            "pass_count": len(self.passes),
            "p95_loop_latency_ms": telemetry.get("p95_loop_latency_ms"),
            "paper_entry_attempt_count": telemetry.get("paper_entry_attempt_count"),
            "paper_entry_success_count": telemetry.get("paper_entry_success_count"),
        }

    def _print(self) -> None:
        if self.args.json:
            print(json.dumps({
                "ok": not self.failures,
                "failures": [item.as_dict() for item in self.failures],
                "warnings": [item.as_dict() for item in self.warnings],
                "passes": [item.as_dict() for item in self.passes],
                "summary": self.summary,
            }, ensure_ascii=False, indent=2, sort_keys=True))
            return
        print("KARB Acceptance Check")
        print(f"Base URL: {self.base_url}")
        print()
        for item in self.passes:
            print(_format_line("PASS", item))
        for item in self.warnings:
            print(_format_line("WARN", item))
        for item in self.failures:
            print(_format_line("FAIL", item))
        print()
        print(f"Result: {'FAIL' if self.failures else 'PASS'}")
        print(f"Failures: {len(self.failures)}")
        print(f"Warnings: {len(self.warnings)}")

    def _pass(self, code: str, message: str = "", details: dict[str, Any] | None = None) -> None:
        self.passes.append(CheckResult(code, message, details or {}))

    def _warn(self, code: str, message: str = "", details: dict[str, Any] | None = None) -> None:
        self.warnings.append(CheckResult(code, message, details or {}))

    def _info(self, code: str, message: str = "", details: dict[str, Any] | None = None) -> None:
        self.passes.append(CheckResult(code, message, details or {}))

    def _fail(self, code: str, message: str = "", details: dict[str, Any] | None = None) -> None:
        self.failures.append(CheckResult(code, message, details or {}))


def _format_line(level: str, item: CheckResult) -> str:
    suffix = f" {item.message}" if item.message else ""
    return f"[{level}] {item.code}{suffix}"


def _to_number(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _collect_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _bool_from_payload(payload: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    config = payload.get("config")
    if isinstance(config, dict):
        return _bool_from_payload(config, keys)
    return False


def _parse_bool_flag(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {value!r}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run KARB acceptance checks against a web API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--require-paper-trade", action="store_true")
    parser.add_argument("--min-paper-entry-success", type=int, default=0)
    parser.add_argument(
        "--fail-on-api-429",
        dest="fail_on_api_429",
        nargs="?",
        const=True,
        default=True,
        type=_parse_bool_flag,
    )
    parser.add_argument("--no-fail-on-api-429", dest="fail_on_api_429", action="store_false")
    parser.add_argument(
        "--fail-on-rest-direct",
        dest="fail_on_rest_direct",
        nargs="?",
        const=True,
        default=True,
        type=_parse_bool_flag,
    )
    parser.add_argument("--no-fail-on-rest-direct", dest="fail_on_rest_direct", action="store_false")
    parser.add_argument("--max-p95-loop-ms", type=float, default=300)
    parser.add_argument("--max-paper-entry-quote-age-ms", type=float, default=3000)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    return AcceptanceCheck(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
