# KARB_REALTIME_V1

## Guarded tiny-live Spot execution

- Exchange API keys must be limited to balance-read, order-read, and Spot order permissions.
- Withdrawal permissions are forbidden. Futures and margin permissions are not used.
- Adding keys alone never enables orders: the safe default switches remain `false`.
- Public top-of-book market data prefers reconnecting Upbit and Binance WebSocket streams.
- Until both WebSocket sides are fresh, the existing REST quote engine remains the fallback.
- Only the latest top-of-book snapshot is kept. Raw tick append storage is not used.
- Tiny-live supports one explicitly armed Upbit Spot and Binance Spot order pair at a time.
- The default configuration is blocked: `enable_live_trading`, `tiny_live_enabled`, and
  `live_order_enabled` are all `false`.
- Full `live` mode is blocked. Only `tiny_live` may reach guarded order methods.
- Every execution requires a fresh preflight, a non-stale quote, paper-pass checks,
  inventory checks, exchange minimum-order checks, and an explicit ARM action.
- If only one exchange order succeeds, the status becomes `PARTIAL_RISK`, the executor
  disarms immediately, and new entries remain blocked for manual review.
- Withdrawals, wallet addresses, automatic transfers, futures, margin, P2P, and internal
  transfers are intentionally not implemented.
- Runtime state uses overwrite-only files: `runtime/tiny_live_status.json`,
  `runtime/tiny_live_last_preflight.json`, and `runtime/tiny_live_last_order.json`.

## 재고 기반 Spot 차익 구조

이 프로젝트의 tiny-live 준비 구조는 **재고 기반 Upbit ↔ Binance spot 차익**만 다룹니다.

- 사용자가 양 거래소에 자산을 미리 배치합니다.
- 프로그램은 Direction A/B 가능 여부와 부족 자산을 표시합니다.
- 부족 자산은 사용자가 직접 수동 리밸런싱합니다.
- 출금 API, 자동 전송, 자동 리밸런싱은 구현하지 않습니다.
- Futures, Margin, P2P, internal transfer는 사용하지 않습니다.

| 방향 | Upbit | Binance | 필요한 재고 |
|---|---|---|---|
| `A_KIMCHI` | SELL | BUY | Upbit coin, Binance USDT |
| `B_REVERSE_KIMCHI` | BUY | SELL | Upbit KRW, Binance coin |

`GET /api/live/readiness`와 `POST /api/execution/preflight`는 blocker와 수동 조치만 반환합니다.
실제 주문 실행은 구현하지 않습니다.

## API 키 최소 권한 정책

- Upbit 허용: 자산조회, 주문조회, 주문하기
- Upbit 금지: 출금하기, 출금조회, 출금주소 관리
- Binance 허용: Spot 계정 조회, Spot 거래, 주문조회
- Binance 금지: Withdrawals, Futures, Margin, P2P, Internal transfer
- 가능하면 IP 제한을 설정합니다.
- 키는 `.env.local`에만 저장하며 GitHub에 업로드하지 않습니다.

Upbit ↔ Binance 실시간 김프(Kimchi Premium) 차익 계산 및 Paper/Live 운용 엔진.

## 공식 실행 방법 (UI 중심)

```
┌─────────────────────────────────────────────────────────┐
│  1. LAUNCH_KARB.bat 더블클릭                            │
│  2. 브라우저 UI 자동 실행 (http://localhost:8000)       │
│  3. START PAPER 클릭                                    │
│  4. 원하는 시간 운용                                    │
│  5. STOP 클릭                                           │
│  6. Session Summary에서 PAPER_EDGE_PASS/FAIL 확인       │
│  7. tiny_live/live는 조건 충족 전 차단됨                │
└─────────────────────────────────────────────────────────┘
```

> **주의**: 기존 `run_paper.bat`, `run_ui.bat`, `stop.bat` 등은 더 이상 직접 사용하지 않습니다. 모든 조작은 브라우저 대시보드 UI에서 수행합니다.

### Judgement 기준

| 판정 | 의미 |
|---|---|
| `PAPER_EDGE_PASS` | paper 기준 유의미한 엣지 확인 → tiny_live 검토 가능 |
| `PAPER_EDGE_WEAK` | 일부 조건만 충족 → 파라미터 조정 후 재검증 권장 |
| `PAPER_EDGE_FAIL` | 순익 음수 → 전략 재설계 필요 |
| `NOT_ENOUGH_TRADES` | 거래 수 < 10건 → 더 긴 시간 검증 필요 |
| `RUNTIME_ERROR` | 런타임 에러 → 로그 확인 |

PASS 조건: net_pnl > 0, win_rate ≥ 65%, avg_pnl > 0, max_drawdown < daily_loss_limit, P95 latency ≤ max_latency, slippage +5bp 스트레스에서도 순익 양수

## 모드

| 모드 | 설명 | API 키 |
|---|---|---|
| `paper` | 가상 거래 (기본값) | 불필요 |
| `tiny_live` | 소량 실거래 | **필수** |
| `live` | 풀사이즈 실거래 | **필수** |

## 프로젝트 구조

```
C:\KARB_REALTIME_V1\
├── LAUNCH_KARB.bat        ← 공식 실행 진입점
├── app_launcher.py        ← 웹서버 구동 및 브라우저 오픈
├── build_exe.bat          ← PyInstaller 빌드 스크립트
├── src/
│   ├── process_manager.py # 엔진 프로세스 제어 (UI -> 엔진)
│   ├── web_server.py      # /api/engine/start, /api/engine/stop
│   ├── main.py            # 실제 엔진 로직
│   ├── control.py         # 세션 제어 (runtime/control.json)
│   ├── session_analyzer.py# 종료 시 자동 분석 + judgement
│   ├── paper_engine.py    # entry/exit/TP/SL/timeout
│   ├── risk_guard.py      # 10가지 실전 가드
│   ├── inventory_manager.py
│   ├── performance_tracker.py
│   ├── event_logger.py    # 조건부 기록 (폭증 방지)
│   ├── arb_calculator.py  # Direction A/B
│   └── ...
├── config/config.yaml
├── runtime/               # overwrite 전용 (Git 제외)
├── logs/                   # 이벤트만 (Git 제외)
├── reports/sessions/       # 세션 리포트
├── web/                    # 5탭 대시보드
└── docs/
```

## 빠른 시작

```powershell
# 1. 의존성
pip install requests pyyaml python-dotenv psutil

# 2. 문법 검사
python -m compileall src\ -q
python -m py_compile app_launcher.py

# 3. 앱 실행
# LAUNCH_KARB.bat 더블클릭
```

## API 키 보안

> **API 키는 `.env.local` 또는 `.env`에만 저장. 코드/config.yaml에 절대 넣지 않는다.**

- 대시보드 → API Keys 탭에서 키를 안전하게 입력하고 저장할 수 있습니다.
- 키 값은 어디에도 평문으로 출력되거나 다시 표시되지 않습니다.

가이드: [docs/SECURITY_KR.md](docs/SECURITY_KR.md)

## 데이터 저장 원칙

- `runtime/*.json` – **overwrite만** (append 금지)
- `logs/paper_trades.jsonl` – ENTRY/EXIT 이벤트만 (매초 저장 금지, 최대 2000행)
- `logs/decisions.jsonl` – OK/후보/상태변화/오류만 (LOW_SURPLUS 반복 저장 금지, 최대 20MB)
- `reports/sessions/` – 세션 요약만 (raw tick 저장 금지)
- sqlite / 대용량 파일 생성 금지

## Guarded tiny-live execution

- Tiny-live uses the same inspectable `ExecutionPlan` shape as paper evaluation.
- Orders remain blocked unless live trading, tiny-live, and live-order gates are enabled and the executor is explicitly armed.
- Upbit and Binance Spot orders are submitted concurrently, then both fills are checked before a result is accepted.
- A one-sided failure or partial fill sets `PARTIAL_RISK`, disarms the executor, and blocks new entries.
- Automatic unwind orders are intentionally disabled. Resolve partial exposure manually.
- Withdrawal, wallet, transfer, Futures, Margin, and P2P features are intentionally absent.

### Tiny Live panel

1. Open `Inventory / Rebalance` and review the `Live Guard` blockers.
2. Enable all three configuration gates only after paper validation: `enable_live_trading=true`, `tiny_live_enabled=true`, and `live_order_enabled=true`.
3. Click `ARM TINY LIVE`, review the generated `Execution Plan`, then use `EXECUTE ONCE` only for an intentional single guarded Spot order pair.
4. Click `DISARM` after the check. `DISARM` is always available.

API keys alone never enable an order. If `PARTIAL_RISK` appears, stop new entries, inspect both exchange fills and balances manually, then disarm. Automatic unwind is intentionally absent.

## Long paper-run monitoring

- The trade log shows actual paper `ENTRY` and `EXIT` events only.
- The `Decision Log` tab shows the latest 100 GO and NO-GO evaluations from `runtime/latest_decisions.json`. The file is overwritten; raw ticks are not appended.
- `WS OK` means fresh WebSocket top-of-book quotes are available. `REST FALLBACK` means REST quotes were used because WS coverage was incomplete. `STALE` means a quote crossed the configured freshness limit.
- During a long paper run, watch runtime, loop count, quote count, decision count, candidate count, OK signals, top NO-GO reasons, maximum surplus, best symbol, quote age, and P95 loop/quote latency.
- Session summaries include WS ratio, REST fallback count, stale quote count, signal counts, surplus statistics, network health, trading quality, and final judgement.
- Tiny-live ordering remains blocked by default. Monitoring changes do not enable live orders.

## Order tracker and emergency scaffold

- `OrderTracker` records each tiny-live plan and its Upbit/Binance Spot legs in overwrite-only runtime snapshots. Duplicate fill updates replace leg values instead of adding them again.
- `OrderTracker` and paper `InventoryManager` protect in-memory state changes with
  `RLock`. Order ledger snapshot file writes occur after the state snapshot is
  captured, outside the lock.
- Any one-sided failure, timeout, or partial fill becomes `PARTIAL_RISK`. The executor disarms and blocks new entries until an operator reviews both exchanges and resolves any remaining Spot exposure.
- `EmergencyLiquidator` is a guarded scaffold only. `emergency_liquidation_enabled=false` and `emergency_auto_execute=false` are the defaults, so it returns a manual action guide instead of placing an order.
- `MANUAL CLEAR PARTIAL RISK` records an operator-provided reason and returns the tracker to `DISARMED`. It never places an order.
- Withdrawal, wallet-address, transfer, Futures, Margin, and P2P features remain intentionally absent.

## Strategy and venue pairs

- KARB remains one program. The dashboard compares enabled venue pairs through one paper-monitoring flow.
- `UPBIT_BINANCE` keeps the existing cross-border Spot calculation and guarded tiny-live execution path.
- `UPBIT_BITHUMB` adds domestic KRW spread monitoring and a guarded tiny-live preparation path. Both venues use KRW quotes, so this calculation does not carry FX risk.
- `BITHUMB_BINANCE` is a disabled quote-structure placeholder. `BINANCE_MAKER_DOMESTIC_TAKER` is a disabled display-only placeholder.
- Bithumb private integration is limited to balance read, order read, and Spot market orders. Withdrawals, deposits, wallet addresses, automatic transfers, Futures, Margin, and P2P are intentionally absent.
- Bithumb orders remain blocked by default. In addition to the global live gates and explicit ARM action, `bithumb_private_enabled` and `upbit_bithumb_live_enabled` must be enabled deliberately.

## RuntimeStore and Dynamic Top20 scanner

- `RuntimeStore` keeps loop state in memory and writes bounded overwrite-only compatibility snapshots at a configured interval. Raw tick append storage is not added.
- The Dynamic Top20 scanner reads Upbit KRW, Bithumb KRW, and Binance USDT Spot public markets, intersects supported symbols, excludes thin or blacklisted symbols, and selects the highest-volume symbols.
- Startup is cache-first: a fresh `runtime/market_scanner.json` snapshot is used immediately, otherwise the engine starts immediately with the existing `config.symbols` list.
- Dynamic REST scans run in a bounded background timeout path. A successful scan refreshes QuoteEngine, WebSocket symbols, and the Bithumb quote cache; a failed scan preserves the current active symbols and records blockers.
- If any scanner request fails or no eligible common market remains, paper monitoring continues without waiting for scanner recovery.
- This monitoring change does not relax live-order guards or add any withdrawal, deposit, address, transfer, Futures, Margin, or P2P capability.

## Dynamic slippage and latency-aware paper fills

- Paper opportunities use a conservative dynamic-slippage estimate. When depth levels are available the model estimates a weighted fill price; top-of-book-only quotes use the configured fallback slippage.
- The paper fill simulator applies per-leg Upbit, Binance, and Bithumb venue latency against bounded in-memory quote history. If an older latency-aligned quote is unavailable, it adds a configured slippage stress penalty.
- Quote history stores lightweight top-of-book snapshots only. It excludes calculation results, full orderbook depth, and raw tick append storage.
- During long paper runs, watch quote-history row count and process memory telemetry alongside dynamic slippage, simulated fill latency, and paper edge quality.
- This refinement intentionally avoids a full asyncio rewrite or Redis dependency.
- This is paper evaluation only. It does not relax live-order guards or add Iceberg execution.
## Iceberg placeholder

- Iceberg is placeholder only, for future large-order execution.
- Actual split order execution is not implemented.
- `iceberg_enabled` and `iceberg_execution_enabled` default to `false`.
- Large orders require separate validation before any future enablement. Early small-capital operation does not need Iceberg execution.
- Current 24-hour paper runs use Dynamic Top20 selection, dynamic slippage, and latency-aware paper fill. Live order behavior is unchanged.

## Dashboard venue-pair sections

- The `Upbit ↔ Binance` section shows KRW/USDT cross-border Spot opportunities with FX enabled. Direction `A` means Upbit SELL / Binance BUY; direction `B` means Upbit BUY / Binance SELL.
- The `Upbit ↔ Bithumb` section shows domestic KRW Spot opportunities with FX disabled. `UPBIT_BITHUMB_A` means Upbit SELL / Bithumb BUY; `UPBIT_BITHUMB_B` means Bithumb SELL / Upbit BUY.
- Pair badges and the Decision Log distinguish the two strategies visually. Disabled venue-pair placeholders remain gray.
- This dashboard-only change does not modify calculation logic, order logic, RiskGuard, Executor behavior, or live-order defaults.

## Direction-specific required inventory

- `UPBIT_BINANCE` direction `A` requires Upbit coin and Binance USDT. Direction `B` requires Upbit KRW and Binance coin.
- `UPBIT_BITHUMB_A` requires Upbit coin and Bithumb KRW. `UPBIT_BITHUMB_B` requires Bithumb coin and Upbit KRW.
- Opportunities expose `direction_a_required_assets`, `direction_b_required_assets`, and `selected_required_assets`. Inventory checks prefer `selected_required_assets` and fall back to legacy fields only when needed.
- This correction does not change order execution, live-order gates, withdrawal policy, or risk thresholds.

## Effective order quantity

- `max_fillable_qty_raw` is the raw top-of-book depth limit.
- `effective_qty` is the final calculation quantity after applying both raw depth and the configured one-order KRW amount.
- Expected profit, selected notional, and selected required assets use `effective_qty`.
- Long paper-run opportunity evaluation now reports order-sized quantities.

## Domestic KRW paper execution

- `UPBIT_BITHUMB` opportunities now enter and exit through the paper engine when their existing paper guards return `OK`.
- Paper inventory is tracked separately for Upbit, Binance, and Bithumb. Each paper entry and exit records venue balance deltas.
- Domestic exits use the matching Upbit and Bithumb quotes and preserve `pair_id` in recent closed trades.
- Live and tiny-live execution behavior is unchanged.

## Pair-level paper performance

- Review 24-hour paper results by venue pair, not only as one combined PnL number.
- `UPBIT_BINANCE` and `UPBIT_BITHUMB` have different profit structures, quote paths, and inventory requirements.
- Runtime and session summaries expose `pair_summary` so the next tuning decision can be made per pair.

## REST fallback load guards

- Public Upbit, Binance, and Bithumb REST quote requests pass through a shared token-bucket rate limiter.
- HTTP `429` responses trigger a bounded backoff. WebSocket REST fallback also has a minimum interval and skips retries while an exchange is backing off.
- Dashboard telemetry shows throttle count, `429` count, active backoff exchanges, REST fallback count, and skipped fallback count.
- Loop and quote P95 percentiles are refreshed on a configured interval instead of sorting samples every loop.
- This monitoring change does not modify order execution, live-order gates, withdrawal policy, or risk thresholds.

## Bithumb quote cache

- Domestic `UPBIT_BITHUMB` paper monitoring reads Bithumb public quotes from a bounded in-memory background cache instead of synchronously waiting for Bithumb REST on every engine loop.
- Bithumb timestamps are normalized to seconds and fall back to refresh time when missing or implausible. Dashboard telemetry exposes stale/quote counts, timestamp fallbacks, and last-success age.
- A failed refresh retains the last successful snapshot and marks aged quotes as stale. Missing, unavailable, or stale Bithumb quotes skip only domestic calculations; `UPBIT_BINANCE` monitoring continues independently.
- Dynamic symbol refresh removes inactive cross-border and `UPBIT_BITHUMB:{symbol}` quote-history keys.
- Dashboard telemetry and `GET /api/bithumb/cache-status` expose cache health, skipped domestic symbols, and quote-history key count.
- This monitoring change does not alter paper entry/exit logic, order execution, or live-order safety defaults.

### Paper smoke checks

- Expect Bithumb `stale_count` to stay below `quote_count` after successful refreshes.
- Upbit `api_429_count` should stop increasing during backoff. `rest_fallback_skip_count` may increase while REST fallback is intentionally skipped.
- Verify stable process memory and confirm WebSocket error or reconnect counts are not rising rapidly.

## WebSocket monitoring stability

- WebSocket collectors record connection errors, reconnect counts, last message age, and per-exchange status without printing sensitive data.
- The in-memory Upbit/Binance orderbook cache rejects out-of-order timestamps and exposes a bounded drop counter.
- The local dashboard server uses `ThreadingHTTPServer` so polling and control requests do not block each other.
- Keep `runtime_store_enabled=true` for long paper or live monitoring. The dashboard warns when RuntimeStore is disabled.
- This stability patch intentionally avoids a full asyncio rewrite or Redis dependency and does not change order or paper-trading logic.

## Emergency Close Once

- `PARTIAL_RISK` still disarms tiny-live and blocks new entries until operator review and manual clear.
- Emergency recovery is Spot-only and optional. `emergency_liquidation_enabled=false` and `emergency_auto_execute=false` remain the defaults.
- A preview can show either `COMPLETE_MISSING_LEG` or `REVERT_FILLED_LEG`, exposure, failed leg, filled leg, and the manual action without placing an order.
- An emergency Spot close is permitted only after separate approval enables both emergency gates and every freshness, inventory, order-size, slippage, and ledger guard passes.
- Each plan allows at most one emergency attempt. Failed or repeated recovery orders remain blocked.
# Background REST fallback cache

Upbit/Binance REST fallback quotes are refreshed by a background
`RestFallbackQuoteCache`. The engine loop reads memory snapshots only under the
default configuration. The cache uses an `RLock`, keeps only the latest
top-of-book snapshot, and rejects an older REST fallback before it can replace a
newer WebSocket quote. Direct REST fallback from the engine loop is disabled by
default with `rest_direct_fallback_enabled: false`.

The KRW/USDT FX rate uses a 60 second cache by default, so the engine loop does
not request BTC FX quotes on every iteration. A failed refresh retains the last
known value, while an FX value older than `fx_cache_max_age_sec` is marked
`FX_STALE`. The dashboard and `/api/rest-fallback-cache/status` expose the
background REST cache state. During smoke tests, verify direct REST calls stay
at zero, cache hits can increase, older-than-WS drops can increase, and
`fx_cache_age_sec` refreshes normally.

For long paper smoke tests, the recommended checks are:

- `Api429Delta` stays zero or nearly zero over a long interval.
- `DirectRest` stays zero.
- When the Bithumb cache reports zero stale quotes, `Bithumb Skipped Last Loop`
  should also stay zero.
- `P95Loop` should preferably remain below 100 ms.
- `p95_quote_age_ms` reports freshness age, while
  `p95_quote_fetch_latency_ms` reports REST fetch latency.
- Repeated `BithumbStale 20/20` is a smoke-test failure. A temporary
  `stale_grace_count` is acceptable near a refresh boundary, but continuously
  increasing `Bithumb Skipped Last Loop` is not.
- Start observing `P95QuoteAge` above 3000 ms. It remains an optimization target
  before live use.

## Staged live quote freshness

- `P95QuoteAge` is an observation metric across paper quotes. Live and tiny-live
  readiness use the stricter per-opportunity `max_leg_quote_age_ms`.
- Stale-grace Bithumb quotes remain available for paper observation, but are
  blocked from live readiness by default.
- Paper mode keeps the dynamic Top20 scanner. Live readiness uses the separate
  `live_active_symbols` watchlist unless `live_use_dynamic_symbols` is enabled.
- On the current local PC baseline, tiny-live starts with a 1500 ms
  cross-border threshold in observe-only mode. Live starts with a 1000 ms gate.
- Tighten the live threshold toward 500 ms only after server placement or
  network improvements show that the lower age is sustainable.
- Only sufficiently liquid watchlist symbols are live candidates. Dynamic paper
  symbols outside that watchlist remain visible for observation.

## Paper-only stale opportunity recheck

- Profitable `STALE_QUOTE` paper opportunities are not promoted to orders.
  They can request a bounded background priority refresh from the quote cache.
- The main loop never performs direct REST calls for this recheck. Existing
  background caches process priority requests ahead of normal refresh work.
- `RECHECK_PASS` means the edge still existed after a fresh quote recheck. It is
  a paper observation result, not permission to place live or tiny-live orders.
- `RECHECK_FAIL` means the edge disappeared or no longer met the trigger
  threshold. `RECHECK_TIMEOUT` means no fresh quote arrived within the TTL.
- Passing rechecks are split into `RECHECK_FAST_PASS` and `RECHECK_LATE_PASS`.
  Only fast passes are useful for live-candidate analysis. Late passes mean the
  edge existed, but current refresh speed makes it weak for live use.
- `RECHECK_ACTIONABLE_FAST_PASS` is a stricter analysis tag on top of fast pass:
  the refreshed opportunity stayed fresh, liquid, positive, and close enough to
  its original surplus. It is still not an order signal and does not connect to
  live or tiny-live execution.
- Priority recheck requests wake the background cache worker immediately and
  target only the requested symbol when the public client supports symbol-only
  fetch. Duplicate in-flight `(pair_id, symbol)` requests are deduped.
- Upbit priority REST is skipped when a fresh WebSocket quote already exists,
  or when cooldown/backoff/in-flight guards say the refresh should wait.
- This stage improves measurement only. Recheck outcomes are not connected to
  live, tiny-live, or market-order execution.
- For 24-hour paper runs, review the recheck pass ratio and average surplus to
  separate real stale-hidden opportunities from stale-cache noise.
- Before deciding any next step after a long paper run, review
  `actionable_fast_pass_count`, `actionable_fast_pass_ratio`,
  `avg_elapsed_ms`, `timeout_ratio`, `avg_new_surplus_bp`, and whether the same
  symbols repeat in the top actionable list. The next decision should be based
  on that evidence, not a direct jump from a single fast pass to tiny-live.
- Completed priority fetches are handed off from the background quote caches to
  the matching pending stale recheck in the main loop. This records actual
  `refresh_started_at`, `refreshed_at`, and `fetch_ms` so fetch latency and
  decision-wait latency can be separated. The handoff is measurement-only and
  remains disconnected from live, tiny-live, or market-order execution.
- `RECHECK_ACTIONABLE_FAST_PASS` is connected to `PaperEngine` entries in
  `paper` mode only, with `entry_reason=RECHECK_ACTIONABLE`. This is still
  disconnected from live and tiny-live orders; the purpose is to produce real
  paper open/closed trades so win rate and PnL can be measured.
- `WIDE_SPREAD` remains dangerous for live execution because the spread itself
  can erase the edge. In `paper` mode only, a sufficiently profitable and
  liquid `WIDE_SPREAD` domestic candidate can request the same priority recheck;
  if the refreshed edge is still actionable it enters `PaperEngine` with
  `entry_reason=WIDE_SPREAD_RECHECK_ACTIONABLE`. This is not connected to live
  or tiny-live orders.
- Paper performance should also be reviewed by `entry_reason`. Compare
  `NORMAL_GO`, `RECHECK_ACTIONABLE`, and
  `WIDE_SPREAD_RECHECK_ACTIONABLE` separately because they represent different
  entry paths. This summary is for paper analysis only and remains disconnected
  from live and tiny-live order execution.
- Entry signals now pass through a common `route_signal_to_execution` gate.
  In `paper` mode, eligible `NORMAL_GO`, `RECHECK_ACTIONABLE`, and
  `WIDE_SPREAD_RECHECK_ACTIONABLE` signals enter `PaperEngine` only after
  per-signal quote freshness, stale, duplicate, liquidity, and positive-net
  checks. In `tiny_live` or `live`, the same gate can only produce a guarded
  execution-plan candidate; it does not call exchange order functions or bypass
  the existing disabled-by-default live safety settings.
- For recheck-based paper entries, entry freshness is measured from the
  completed handoff `refreshed_at` timestamp first. The original stale quote age
  is used only as a fallback, and the telemetry records
  `entry_quote_age_source` so `ENTRY_QUOTE_TOO_OLD` blocks can be diagnosed
  without weakening the quote-age guard.
- Completed recheck handoffs are consumed after the in-memory quote snapshots are
  available and are routed in the same loop through `route_signal_to_execution`.
  Fresh handoffs can become paper entries immediately; stale handoffs are
  skipped before routing with `COMPLETED_HANDOFF_TOO_OLD`, preserving the normal
  quote-age and stale-quote guards.
- In `paper` mode only, active dynamic symbols can receive virtual inventory
  seed balances for UPBIT, BITHUMB, and BINANCE using current quote prices.
  This is simulator inventory for measuring paper win rate and PnL; it does not
  move assets, does not call exchange order functions, and does not change
  live/tiny-live inventory guards or disabled-by-default safety settings.
- Paper, tiny-live, and live signals use the same `route_signal_to_execution`
  gate. Paper creates `PaperEngine` open trades only. Tiny-live can continue
  into the existing guarded `TinyLiveExecutor` after config, key, freshness,
  inventory, duplicate, loss-limit, tracker, and emergency guards pass. No
  separate live executor is bundled yet, so live remains blocked with an explicit
  executor blocker unless one exists. Live and tiny-live defaults remain off.
- Before trusting a long paper run, execute
  `python tools/acceptance_check.py --base-url http://127.0.0.1:8000` against
  the running dashboard API. The checker classifies healthy and unhealthy paper
  routing, mock-trade filtering, entry-reason summaries, stale recheck health,
  direct REST/429 telemetry, and live/tiny-live guard readiness. If it reports
  `FAIL`, do not add features first; fix the printed failure reason.
- Paper exits use a small minimum hold window before SL/TIMEOUT evaluation so a
  trade is not opened and stopped out on the same quote tick. This does not
  remove SL or improve PnL artificially; it keeps paper evaluation closer to a
  real unwind flow and reports deferred SL/min-hold counters in telemetry.
- Paper entry freshness is capped by `entry_reason`, not by aggregate quote-age
  percentiles. `WIDE_SPREAD_RECHECK_ACTIONABLE` uses the strictest paper cap and
  must also pass post-recheck edge, net-profit, liquidity, notional, and dynamic
  slippage gates before a paper entry is allowed.
