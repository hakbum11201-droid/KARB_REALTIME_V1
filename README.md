# KARB_REALTIME_V1

Upbit ↔ Binance 실시간 김프(Kimchi Premium) 차익 계산 및 Paper/Live 운용 엔진.

## 원클릭 Paper 운용

```
┌─────────────────────────────────────────────────────────┐
│  1. run_paper.bat 더블클릭 → Paper 엔진 연속 실행       │
│  2. run_ui.bat 실행 → http://localhost:8000 대시보드     │
│  3. 중단: STOP_PAPER.bat 또는 UI STOP 버튼 클릭         │
│  4. 종료 후 reports/sessions/{run_id}_summary.txt 확인   │
│  5. judgement가 PAPER_EDGE_PASS이면 tiny_live 검토       │
└─────────────────────────────────────────────────────────┘
```

### 실행 흐름

| 순서 | 방법 | 설명 |
|---|---|---|
| 1 | `run_paper.bat` 더블클릭 | `--until-stop` 모드로 paper 엔진 시작 |
| 2 | `run_ui.bat` 실행 (선택) | 대시보드 http://localhost:8000 |
| 3 | `STOP_PAPER.bat` 실행 또는 UI ⏹ STOP | graceful stop 요청 |
| 4 | 엔진이 다음 루프에서 감지 → 안전 종료 | |
| 5 | 세션 분석 리포트 자동 생성 | `reports/sessions/{run_id}_summary.txt` |
| 6 | judgement 확인 | PASS / WEAK / FAIL / NOT_ENOUGH / ERROR |

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
├── run_paper.bat          ← 원클릭 Paper 시작
├── STOP_PAPER.bat         ← Graceful 정지
├── run_ui.bat             ← 대시보드 시작
├── src/
│   ├── main.py            # --once / --duration-sec / --until-stop
│   ├── control.py         # 세션 제어 (runtime/control.json)
│   ├── session_analyzer.py# 종료 시 자동 분석 + judgement
│   ├── paper_engine.py    # entry/exit/TP/SL/timeout
│   ├── risk_guard.py      # 10가지 실전 가드
│   ├── inventory_manager.py
│   ├── performance_tracker.py
│   ├── event_logger.py    # 조건부 기록 (폭증 방지)
│   ├── arb_calculator.py  # Direction A/B
│   ├── web_server.py      # /api/stop, /api/session/last
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
pip install requests pyyaml python-dotenv

# 2. 문법 검사
python -m compileall src\ -q

# 3. Paper 원클릭 실행
# run_paper.bat 더블클릭

# 4. 대시보드
# run_ui.bat 더블클릭 → http://localhost:8000

# 5. 정지
# STOP_PAPER.bat 더블클릭 또는 UI STOP 버튼
```

## API 키 보안

> **API 키는 `.env.local` 또는 `.env`에만 저장. 코드/config.yaml에 절대 넣지 않는다.**

- `.env.example` → `.env.local`로 복사 후 키 입력
- 대시보드 → API Keys 탭에서 Set/Missing 확인
- 키 값은 어디에도 출력/로그 없음

가이드: [docs/SECURITY_KR.md](docs/SECURITY_KR.md)

## 데이터 저장 원칙

- `runtime/*.json` – **overwrite만** (append 금지)
- `logs/paper_trades.jsonl` – ENTRY/EXIT 이벤트만 (매초 저장 금지, 최대 2000행)
- `logs/decisions.jsonl` – OK/후보/상태변화/오류만 (LOW_SURPLUS 반복 저장 금지, 최대 20MB)
- `reports/sessions/` – 세션 요약만 (raw tick 저장 금지)
- sqlite / 대용량 파일 생성 금지
