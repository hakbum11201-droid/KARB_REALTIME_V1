"""
control.py - 세션 제어 (runtime/control.json 관리).
- START: run_id 생성, status: RUNNING, stop_requested: false
- STOP: stop_requested: true 기록 → 엔진이 다음 루프에서 감지
- graceful stop (강제 kill 아님)
"""
import json
import os
import time
import uuid


BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'runtime'))
CONTROL_PATH = os.path.join(RUNTIME_DIR, 'control.json')


def _ensure_runtime():
    os.makedirs(RUNTIME_DIR, exist_ok=True)


def _read_control() -> dict:
    _ensure_runtime()
    if not os.path.exists(CONTROL_PATH):
        return {}
    try:
        with open(CONTROL_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_control(data: dict) -> None:
    _ensure_runtime()
    with open(CONTROL_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def start_session() -> dict:
    """새 세션을 시작한다. run_id를 반환한다."""
    run_id = time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    ctrl = {
        'run_id':         run_id,
        'started_at':     time.time(),
        'started_at_iso': time.strftime('%Y-%m-%d %H:%M:%S'),
        'status':         'RUNNING',
        'stop_requested': False,
    }
    _write_control(ctrl)
    return ctrl


def request_stop() -> dict:
    """stop_requested를 true로 설정한다. 엔진이 다음 루프에서 감지."""
    ctrl = _read_control()
    ctrl['stop_requested'] = True
    ctrl['stop_requested_at'] = time.time()
    _write_control(ctrl)
    return ctrl


def is_stop_requested() -> bool:
    """엔진이 매 루프에서 호출. stop_requested가 true면 True 반환."""
    ctrl = _read_control()
    return ctrl.get('stop_requested', False)


def finalize_session(ended_at: float | None = None) -> dict:
    """세션 종료 상태를 기록한다."""
    ctrl = _read_control()
    ctrl['status']     = 'STOPPED'
    ctrl['ended_at']   = ended_at or time.time()
    ctrl['ended_at_iso'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ctrl['ended_at']))
    _write_control(ctrl)
    return ctrl


def get_control() -> dict:
    """현재 control.json 읽기."""
    return _read_control()


def get_run_id() -> str:
    """현재 run_id 반환. 없으면 빈 문자열."""
    return _read_control().get('run_id', '')


# ── CLI: STOP_PAPER.bat에서 호출 ─────────────────────────────────────────
if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'stop':
        ctrl = request_stop()
        print(f"Stop requested. run_id={ctrl.get('run_id', '?')}")
        print("Engine will finalize session report on next loop.")
    elif len(sys.argv) > 1 and sys.argv[1] == 'status':
        ctrl = get_control()
        print(json.dumps(ctrl, indent=2, ensure_ascii=False))
    else:
        print("Usage: python src/control.py stop|status")
