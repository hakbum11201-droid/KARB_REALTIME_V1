# KARB_REALTIME_V1 보안 가이드

## 1. API 키 관리 원칙

| 항목 | 규칙 |
|---|---|
| 키 저장 위치 | `.env` 파일 또는 시스템 환경변수 |
| `config.yaml` | API 키 절대 포함 금지 |
| 소스 코드 | 하드코딩 절대 금지 |
| Git 커밋 | 실제 키가 포함된 파일 커밋 절대 금지 |
| 로그/print | 키 값 노출 절대 금지 |

---

## 2. .env 파일 설정 방법

```bash
# Windows
copy .env.example .env

# Linux / macOS
cp .env.example .env
```

복사 후 `.env` 파일을 편집하여 실제 API 키를 입력한다.  
`.env` 파일은 `.gitignore`에 의해 Git 추적에서 제외된다.

---

## 3. GitHub 업로드 금지 항목

아래 파일들은 절대로 GitHub(또는 다른 원격 저장소)에 푸시하지 않는다.

```
.env
.env.*           # .env.example 제외
secrets*
*_key*
*_secret*
logs/
runtime/
*.log
*.jsonl
*.sqlite
*.db
```

> **[!CAUTION]**  
> `git add .` 을 사용할 경우 실수로 키 파일이 포함될 수 있다.  
> 항상 `git add <파일명>` 으로 개별 추가하거나, `git status`로 목록을 먼저 확인한다.

---

## 4. 모드별 키 요구사항

| 모드 | Upbit 키 | Binance 키 | 설명 |
|---|---|---|---|
| `paper` | 불필요 | 불필요 | 실제 주문 없음, 시뮬레이션만 |
| `tiny_live` | 필수 | 필수 | 소액 실거래, 키 없으면 즉시 종료 |
| `live` | 필수 | 필수 | 실거래, 키 없으면 즉시 종료 |

`src/secrets_manager.py`의 `assert_live_credentials_available(mode)` 함수가  
`tiny_live` / `live` 진입 시 자동으로 키 존재 여부를 검증한다.

---

## 5. 키 유출 시 즉시 조치 방법

키가 GitHub 등 외부에 노출된 것이 확인된 경우 아래 순서로 즉시 조치한다.

### Step 1 — 거래소에서 즉시 키 폐기
- **Upbit**: [https://upbit.com/mypage/open_api_management](https://upbit.com/mypage/open_api_management) → 해당 키 삭제
- **Binance**: [https://www.binance.com/en/my/settings/api-management](https://www.binance.com/en/my/settings/api-management) → 해당 키 삭제

### Step 2 — 새 키 발급
기존 키를 삭제한 후 새 키를 발급하여 `.env`에 업데이트한다.

### Step 3 — Git 히스토리 정리
커밋에 키가 포함된 경우 `git filter-repo` 또는 `BFG Repo-Cleaner`로 히스토리를 정리한다.

```bash
# BFG 예시 (jar 파일 별도 다운로드 필요)
java -jar bfg.jar --replace-text secrets.txt my-repo.git
git reflog expire --expire=now --all
git gc --prune=now --aggressive
git push --force
```

### Step 4 — 원격 저장소 확인
GitHub의 경우 Secret Scanning 알림을 확인하고, 필요 시 저장소를 Private으로 전환한다.

---

## 6. 코드 리뷰 체크리스트

PR/커밋 전 아래 항목을 반드시 확인한다.

- [ ] `config.yaml`에 API 키가 없는가?
- [ ] 소스 코드에 하드코딩된 키 문자열이 없는가?
- [ ] `git diff --cached`에 키 값이 포함되지 않는가?
- [ ] `.env` 파일이 `git status`에 표시되지 않는가?
- [ ] 로그나 print 구문에서 키 변수를 출력하지 않는가?
