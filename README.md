# KARB_REALTIME_V1

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
