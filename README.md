# KARB_REALTIME_V1

Real-time Kimchi Premium arbitrage engine.

## Modes
- `paper`: Default mode. Simulates trades without connecting to private APIs.
- `tiny_live`: Executes small trades using live private APIs.
- `live`: Executes full-size trades.

## Structure
- `src/`: Core logic and exchange wrappers.
- `config/`: YAML configurations.
- `runtime/`: Transient state and latest quotes.
- `logs/`: High-level events and paper trade logs.
- `web/`: Lightweight UI for monitoring.
- `docs/`: Documentation.

## Quick Start
```bash
# Compile and test
python -m compileall src/

# Run one iteration
python src/main.py --once

# Run for 60 seconds
python src/main.py --duration-sec 60

# Run UI
python src/web_server.py
```

## API 키 보안

> **API 키는 `.env` 파일에만 저장한다. 코드나 config.yaml에 절대 넣지 않는다.**

1. `.env.example`을 복사하여 `.env`로 저장한다.
2. `.env` 파일에 실제 키를 입력한다.
3. `.env`는 `.gitignore`에 의해 Git에 포함되지 않는다.
4. `paper` 모드는 API 키 없이 동작한다.
5. `tiny_live` / `live` 모드는 키가 없으면 즉시 종료된다.

자세한 보안 가이드는 [docs/SECURITY_KR.md](docs/SECURITY_KR.md)를 참조한다.
