"""Thread-safe in-memory runtime state with bounded overwrite-only snapshots."""
import copy
import json
import os
import tempfile
import threading
import time


class RuntimeStore:
    def __init__(self, enabled=True, max_failures=5):
        self.enabled = bool(enabled)
        self.max_failures = int(max_failures)
        self._lock = threading.RLock()
        self._state = {
            'latest_quotes': {},
            'telemetry': {},
            'latest_decisions': {'decisions': []},
            'performance_summary': {},
            'last_execution_plan': {},
            'order_tracker_state': {},
            'tiny_live_status': {},
            'market_scanner': {},
        }
        self._stop_event = threading.Event()
        self._writer_thread = None
        self._stats = {
            'enabled': self.enabled,
            'snapshot_write_count': 0,
            'snapshot_fail_count': 0,
            'last_snapshot_at': 0.0,
            'last_snapshot_error': '',
            'max_failures': self.max_failures,
        }

    def set_state(self, key, value):
        with self._lock:
            self._state[key] = copy.deepcopy(value)

    def get_state(self, key, default=None):
        with self._lock:
            return copy.deepcopy(self._state.get(key, default))

    def append_bounded_list(self, key, item, max_items):
        with self._lock:
            values = list(self._state.get(key, []))
            values.append(copy.deepcopy(item))
            self._state[key] = values[-int(max_items):]

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._state)

    def status(self):
        with self._lock:
            stats = copy.deepcopy(self._stats)
        last_snapshot_at = float(stats.get('last_snapshot_at', 0) or 0)
        stats['snapshot_age_sec'] = (
            round(max(0.0, time.time() - last_snapshot_at), 2)
            if last_snapshot_at else None
        )
        return stats

    def hydrate(self, path_map):
        for key, path in path_map.items():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self.set_state(key, json.load(f))
            except Exception:
                pass

    @staticmethod
    def _atomic_write(path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(path), prefix=os.path.basename(path) + '.', suffix='.tmp'
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def save_snapshot(self, path_map, status_path=None):
        if not self.enabled:
            return self.status()
        snapshot = self.snapshot()
        try:
            for key, path in path_map.items():
                if key in snapshot:
                    self._atomic_write(path, snapshot[key])
            with self._lock:
                self._stats['snapshot_write_count'] += 1
                self._stats['last_snapshot_at'] = time.time()
                self._stats['last_snapshot_error'] = ''
        except Exception as exc:
            with self._lock:
                self._stats['snapshot_fail_count'] += 1
                self._stats['last_snapshot_error'] = f'{type(exc).__name__}: {exc}'
        status = self.status()
        if status_path:
            try:
                self._atomic_write(status_path, status)
            except Exception:
                pass
        return status

    def background_snapshot_writer(self, interval_sec, path_map, status_path=None):
        interval_sec = max(0.1, float(interval_sec))
        while not self._stop_event.wait(interval_sec):
            self.save_snapshot(path_map, status_path)

    def start_background_writer(self, interval_sec, path_map, status_path=None):
        if not self.enabled or (self._writer_thread and self._writer_thread.is_alive()):
            return
        self._writer_thread = threading.Thread(
            target=self.background_snapshot_writer,
            args=(interval_sec, path_map, status_path),
            name='runtime-snapshot-writer',
            daemon=True,
        )
        self._writer_thread.start()

    def stop_background_writer(self, path_map=None, status_path=None):
        self._stop_event.set()
        if self._writer_thread:
            self._writer_thread.join(timeout=1.0)
        if path_map is not None:
            self.save_snapshot(path_map, status_path)
