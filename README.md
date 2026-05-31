# KARB_REALTIME_V1

Upbit ↔ Binance 실시간 김프(Kimchi Premium) 차익 계산 및 Paper/Live 운용 엔진.

## 모드

| 모드 | 설명 | API 키 |
|---|---|---|
| `paper` | 가상 거래 (기본값) | 불필요 |
| `tiny_live` | 소량 실거래 | **필수** |
| `live` | 풀사이즈 실거래 | **필수** |

## 프로젝트 구조

```
C:\KARB_REALTIME_V1\
├── src/
│   ├── main.py               # 메인 루프 (entry/exit/riskguard 통합)
│   ├── config.py             # config.yaml 래퍼
│   ├── exchange_base.py      # Exchange 추상 기반
│   ├── upbit_public.py       # Upbit 공개 호가
│   ├── binance_public.py     # Binance 공개 호가
│   ├── upbit_private.py      # Upbit 주문 (미구현)
│   ├── binance_private.py    # Binance 주문 (미구현)
│   ├── fx_oracle.py          # KRW/USDT 환율 계산
│   ├── quote_engine.py       # 심볼별 호가 수집
│   ├── arb_calculator.py     # Direction A/B 차익 계산
│   ├── inventory_manager.py  # Paper 잔고 관리
│   ├── paper_engine.py       # Paper entry/exit/TP/SL/timeout
│   ├── risk_guard.py         # 10가지 실전 가드 조건
│   ├── event_logger.py       # 조건부 이벤트 로거 (폭증 방지)
│   ├── performance_tracker.py# 성과 집계 (win_rate, drawdown 등)
│   ├── secrets_manager.py    # API 키 보안 관리
│   ├── web_server.py         # 대시보드 HTTP 서버
│   ├── backtest_engine.py    # 인메모리 백테스트
│   ├── mining_engine.py      # 파라미터 그리드 탐색
│   └── bounded_collector.py  # 심볼별 bounded tick 수집
├── config/
│   └── config.yaml           # 운용 파라미터 (키 없음)
├── runtime/                  # overwrite 전용 (Git 제외)
│   ├── latest_state.json
│   ├── latest_quotes.json
│   └── performance_summary.json
├── logs/                     # entry/exit 이벤트만 (Git 제외)
│   ├── paper_trades.jsonl    # ENTRY/EXIT 이벤트 (최대 2000행)
│   ├── decisions.jsonl       # 조건부 기록 (최대 20MB)
│   └── errors.log
├── web/
│   ├── index.html            # 4탭 대시보드
│   ├── style.css             # 다크 글래스모피즘
│   └── app.js                # 3초 폴링, 탭 네비게이션
└── docs/
    ├── SECURITY_KR.md
    └── KARB_REALTIME_V1_PLAN_KR.md
```

## 빠른 시작

```powershell
# 1. 가상환경 생성 및 의존성 설치
python -m venv .venv
.venv\Scripts\activate
pip install requests pyyaml python-dotenv

# 2. API 키 설정 (.env.local, Git 제외)
copy .env.example .env.local
# .env.local에 실제 키 입력 (paper 모드는 불필요)

# 3. 문법 검사
python -m compileall src\ -q

# 4. 단일 실행 (--once)
python src/main.py --once

# 5. 대시보드 실행
python src/web_server.py
# → http://localhost:8000

# 6. Paper 엔진 연속 실행
python src/main.py --duration-sec 3600
```

## API 키 보안

> **API 키는 `.env.local` 또는 `.env` 파일에만 저장한다. 코드나 `config.yaml`에 절대 넣지 않는다.**

1. `.env.example`을 복사하여 `.env.local`로 저장한다.
2. `.env.local`에 실제 키를 입력한다.
3. `.env.local`과 `.env`는 `.gitignore`에 의해 Git에 포함되지 않는다.
4. `paper` 모드는 API 키 없이 동작한다.
5. `tiny_live` / `live` 모드는 키가 없으면 즉시 종료된다.
6. 대시보드 → API Keys 탭에서 키 상태(Set/Missing) 확인 가능.
7. 키 값은 어디에도 출력/로그하지 않는다.

자세한 보안 가이드: [docs/SECURITY_KR.md](docs/SECURITY_KR.md)

## Paper 엔진 동작 원리

### Entry 조건
- `RiskGuard.check_trade()` → `reason_no_trade == 'OK'`
- 같은 symbol/direction의 open trade가 없을 것
- Paper 잔고 충분 (InventoryManager 검사)

### Exit 조건 (매 루프 체크)
| 조건 | 설명 |
|---|---|
| **TP** | `realized_pnl_krw >= net_expected * (1 + take_profit_bp/10000)` |
| **SL** | `realized_pnl_krw <= -net_expected * (stop_loss_bp/10000)` |
| **TIMEOUT** | 보유 시간 > `paper_timeout_sec` |

### RiskGuard 사유 코드
| 코드 | 설명 |
|---|---|
| `OK` | 진입 가능 |
| `LOW_SURPLUS` | net surplus 부족 |
| `LOW_EXPECTED_PROFIT` | 예상 순익 부족 |
| `STALE_QUOTE` | 호가 타임스탬프 초과 |
| `WIDE_SPREAD` | 스프레드 과다 |
| `LOW_DEPTH` | 유동성 부족 |
| `FX_UNTRUSTED` | FX 환율 신뢰 불가 |
| `INVENTORY_SHORTAGE` | 잔고 부족 |
| `COOLDOWN` | 연속 실패 후 쿨다운 |
| `DAILY_LOSS_LIMIT` | 일일 손실 한도 초과 |
| `MODE_GUARD` | enable_live_trading=false |

## 절대 금지
- API 키 하드코딩 금지
- `logs/`, `runtime/`, `.env*` Git add 금지
- 대량 raw tick 무한 저장 금지 (BoundedCollector MAX_TICKS=500)
- `decisions.jsonl`에 매초 전체 상태 저장 금지 (조건부 기록)
- `paper_trades.jsonl`에 매초 저장 금지 (ENTRY/EXIT 이벤트만)
