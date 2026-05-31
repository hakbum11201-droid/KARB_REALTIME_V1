# KARB_REALTIME_V1 보안 가이드

## Tiny-live safety boundary

- Public WebSocket quotes keep only the latest top-of-book snapshot; raw tick append storage is disabled.
- REST remains a fallback when WebSocket quotes are unavailable or stale.
- Tiny-live is disabled and disarmed by default.
- Only small Upbit Spot and Binance Spot order pairs are supported after explicit ARM.
- Full live mode, withdrawals, wallet-address storage, transfers, futures, margin, P2P,
  and internal transfers remain blocked.
- Use API keys without withdrawal permissions and restrict them by IP where possible.
- A one-sided fill is recorded as `PARTIAL_RISK`; the executor disarms and requires
  manual review instead of attempting an automatic unwind.

## 출금 금지 및 수동 리밸런싱 정책

이 프로젝트는 출금 API를 사용하지 않습니다.

- 출금 권한 사용 금지
- 지갑 주소 저장 금지
- 자동 전송 금지
- 자동 리밸런싱 금지
- 선물, 마진, P2P, internal transfer 사용 금지
- 부족 자산은 UI에서 안내하고 사용자가 직접 수동 리밸런싱

## 거래소별 API 키 권한

- Upbit 허용: 자산조회, 주문조회, 주문하기
- Upbit 금지: 출금하기, 출금조회, 출금주소 관리
- Binance 허용: Spot 계정 조회, Spot 거래, 주문조회
- Binance 금지: Withdrawals, Futures, Margin, P2P, Internal transfer
- 가능하면 IP 제한을 설정합니다.
- API 키는 `.env.local`에만 저장하며 GitHub에 업로드하지 않습니다.

## 핵심 원칙

1. **API 키는 `.env.local` 또는 `.env` 파일에만 저장한다.**
2. **`config.yaml`에는 절대 키를 넣지 않는다.**
3. **키 값은 절대 코드, 로그, UI에 출력하지 않는다.**
4. **`.env.local`, `.env.*`, `secrets*`, `*_key*`, `*_secret*`는 Git에 절대 포함하지 않는다.**
5. **`.env.example`은 실제 키 없이 형식만 제공하며 Git에 포함 가능하다.**

---

## API 키 설정 방법

### 1단계: .env.local 파일 생성

```powershell
copy .env.example .env.local
```

### 2단계: .env.local에 실제 키 입력

```env
UPBIT_ACCESS_KEY=실제_업비트_액세스키
UPBIT_SECRET_KEY=실제_업비트_시크릿키
BINANCE_API_KEY=실제_바이낸스_API키
BINANCE_API_SECRET=실제_바이낸스_시크릿
```

### 3단계: 대시보드 UI에서 키 관리 (선택)

브라우저에서 `http://localhost:8000` → **API Keys 탭**
- 키 설정 여부(Set/Missing)만 표시됨
- 키 값은 재표시되지 않음
- localhost 접속에서만 저장 가능
- 저장 후 입력 필드 자동 초기화

---

## 모드별 키 요구사항

| 모드 | Upbit 키 | Binance 키 | 설명 |
|---|---|---|---|
| `paper` | 불필요 | 불필요 | 가상 거래 |
| `tiny_live` | **필수** | **필수** | 소량 실거래 |
| `live` | **필수** | **필수** | 풀사이즈 실거래 |

`tiny_live` / `live` 모드에서 키가 없으면 엔진이 즉시 종료됩니다.

---

## .gitignore 필수 항목

```gitignore
# API 키 및 환경변수 파일
.env
.env.*
.env.local
!.env.example
secrets*
*_key*
*_secret*

# 런타임 / 로그
logs/
runtime/
*.log
*.jsonl
*.sqlite
*.db

# Python
__pycache__/
*.pyc
.venv/
```

---

## secrets_manager.py 보안 설계

```
secrets_manager.py
├── _get_env(key)          → 환경변수 읽기 (값 출력 금지)
├── _require_env(key)      → 없으면 RuntimeError (값 노출 금지)
├── get_upbit_credentials()→ (access_key, secret_key) 반환
├── get_binance_credentials()→ (api_key, api_secret) 반환
├── assert_live_credentials_available(mode) → paper 통과, live 검증
├── get_key_status()       → {KEY: "Set"/"Missing"} (값 없음)
└── save_keys(...)         → .env.local에 저장, 값 응답 없음
```

**절대 금지:**
- `print(access_key)` 형태의 키 출력
- 키 값을 `decisions.jsonl`, `errors.log`에 기록
- API 응답 JSON에 키 값 포함
- 키를 `config.yaml`에 저장

---

## web_server.py 보안 설계

- `/api/keys/status`: **127.0.0.1 / ::1 접속만 허용**
- `/api/keys/save`: **127.0.0.1 / ::1 접속만 허용**
- 저장 성공 응답에 키 값 포함하지 않음
- 저장 후 UI 입력 필드 자동 초기화
- 외부 접속 시 `403 Forbidden` 반환

---

## 5일 Paper 검증 시 주의사항

Paper 검증 기간 중:
- `config.yaml`의 `mode: paper` 유지
- `enable_live_trading: false` 유지
- 키를 입력하지 않아도 엔진 동작
- `logs/paper_trades.jsonl`에 ENTRY/EXIT 이벤트만 기록
- `runtime/performance_summary.json`으로 성과 확인
- `decisions.jsonl`은 조건부 기록 (폭증 없음)

---

## 보안 체크리스트

- [ ] `.env.local`이 `.gitignore`에 포함되어 있는가?
- [ ] `git status`에 `.env.local`이 표시되지 않는가?
- [ ] `config.yaml`에 키 관련 값이 없는가?
- [ ] 소스코드에 `YOUR_*` 이외의 실제 키가 없는가?
- [ ] `logs/`, `runtime/`이 Git에 포함되지 않는가?
- [ ] UI에서 키 값이 재표시되지 않는가?
