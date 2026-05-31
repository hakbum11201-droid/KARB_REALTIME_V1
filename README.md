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
python -m py_compile src/*.py

# Run one iteration
python src/main.py --once

# Run for 60 seconds
python src/main.py --duration-sec 60

# Run UI
python src/web_server.py
```
