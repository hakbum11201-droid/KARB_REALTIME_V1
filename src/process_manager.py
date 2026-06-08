"""
process_manager.py - UI에서 엔진 프로세스를 제어하기 위한 모듈.
"""
import os
import subprocess
import time
import psutil
import control
from config import cfg

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'runtime'))
PID_FILE = os.path.join(RUNTIME_DIR, 'engine.pid')

def _ensure_runtime():
    os.makedirs(RUNTIME_DIR, exist_ok=True)

def _read_pid() -> int | None:
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE, 'r', encoding='utf-8') as f:
            pid_str = f.read().strip()
            return int(pid_str) if pid_str else None
    except Exception:
        return None

def _write_pid(pid: int):
    _ensure_runtime()
    with open(PID_FILE, 'w', encoding='utf-8') as f:
        f.write(str(pid))

def _remove_pid():
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

def is_engine_running() -> bool:
    pid = _read_pid()
    if pid is None:
        return False
    try:
        proc = psutil.Process(pid)
        if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
            return True
    except psutil.NoSuchProcess:
        pass
    _remove_pid()
    return False

def start_engine(mode: str) -> dict:
    if mode not in ('paper', 'tiny_live', 'live'):
        return {'ok': False, 'message': f'Invalid mode: {mode}. Must be paper/tiny_live/live'}

    if is_engine_running():
        return {'ok': False, 'message': 'Engine is already running.'}

    main_script = os.path.join(BASE_DIR, 'main.py')
    cmd = ['python', main_script, '--until-stop', '--mode', mode]
    
    try:
        # Spawn in background
        if os.name == 'nt':
            proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            proc = subprocess.Popen(cmd)
            
        _write_pid(proc.pid)
        
        try:
            proc.wait(timeout=2.5)
            _remove_pid()
            return {'ok': False, 'message': f'Engine process exited prematurely with code {proc.returncode}.'}
        except subprocess.TimeoutExpired:
            pass
            
        ctrl = control.get_control_state()
        ctrl['mode'] = mode
        control.set_control_state(ctrl)
        
        return {'ok': True, 'message': f'Engine started in {mode} mode.', 'pid': proc.pid}
    except Exception as e:
        return {'ok': False, 'message': f'Failed to start engine: {e}'}

def stop_engine() -> dict:
    pid = _read_pid()
    if not pid:
        return {'ok': False, 'message': 'Engine is not running.'}
    
    try:
        proc = psutil.Process(pid)
        if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
            control.request_stop()
            try:
                proc.wait(timeout=3.0)
            except psutil.TimeoutExpired:
                proc.terminate()
            _remove_pid()
            return {'ok': True, 'message': 'Stop requested and engine terminated.'}
    except psutil.NoSuchProcess:
        pass
    
    _remove_pid()
    return {'ok': False, 'message': 'Engine was not running.'}

def get_engine_status() -> dict:
    running = is_engine_running()
    ctrl = control.get_control_state()
    stop_req = ctrl.get('stop_requested', False)
    
    status = 'STOPPED'
    if running:
        status = 'STOP_PENDING' if stop_req else 'RUNNING'
        
    return {
        'running': running,
        'status': status,
        'run_id': ctrl.get('run_id', ''),
        'mode': ctrl.get('mode') or cfg.mode
    }
