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
- If any scanner request fails or no eligible common market remains, the engine falls back to the existing `config.symbols` list.
- This monitoring change does not relax live-order guards or add any withdrawal, deposit, address, transfer, Futures, Margin, or P2P capability.

## Dynamic slippage and latency-aware paper fills

- Paper opportunities use a conservative dynamic-slippage estimate. When depth levels are available the model estimates a weighted fill price; top-of-book-only quotes use the configured fallback slippage.
- The paper fill simulator applies venue latency and bounded in-memory quote history. If an older latency-aligned quote is unavailable, it adds a configured slippage stress penalty.
- Use these fields during long paper runs: dynamic slippage, depth available, liquidity class, simulated fill latency, and paper edge quality.
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
- Pair-specific performance rollups are still a later reporting step. Live and tiny-live execution behavior is unchanged.

## REST fallback load guards

- Public Upbit, Binance, and Bithumb REST quote requests pass through a shared token-bucket rate limiter.
- HTTP `429` responses trigger a bounded backoff. WebSocket REST fallback also has a minimum interval and skips retries while an exchange is backing off.
- Dashboard telemetry shows throttle count, `429` count, active backoff exchanges, REST fallback count, and skipped fallback count.
- Loop and quote P95 percentiles are refreshed on a configured interval instead of sorting samples every loop.
- This monitoring change does not modify order execution, live-order gates, withdrawal policy, or risk thresholds.

## Emergency Close Once

- `PARTIAL_RISK` still disarms tiny-live and blocks new entries until operator review and manual clear.
- Emergency recovery is Spot-only and optional. `emergency_liquidation_enabled=false` and `emergency_auto_execute=false` remain the defaults.
- A preview can show either `COMPLETE_MISSING_LEG` or `REVERT_FILLED_LEG`, exposure, failed leg, filled leg, and the manual action without placing an order.
- An emergency Spot close is permitted only after separate approval enables both emergency gates and every freshness, inventory, order-size, slippage, and ledger guard passes.
- Each plan allows at most one emergency attempt. Failed or repeated recovery orders remain blocked.
