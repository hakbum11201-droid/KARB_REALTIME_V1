"""
web_server.py - KARB Realtime V1 HTTP 서버.
엔드포인트:
  GET  /api/state          – state.json
  GET  /api/data           – state + quotes 통합
  GET  /api/perf           – performance_summary + recent_trades
  GET  /api/performance    – performance_summary
  GET  /api/trades/recent  – 최근 paper_trades.jsonl EXIT 20건
  GET  /api/keys/status    – 키 Set/Missing (localhost만)
  POST /api/keys/save      – 키 저장 (localhost만)
  GET  /api/session/last   – last_session_summary.json
  POST /api/stop           – stop_requested=true (localhost만)
  GET  /api/health         – ok
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import os
import sys
import argparse

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
WEB_DIR     = os.path.normpath(os.path.join(BASE_DIR, '..', 'web'))
RUNTIME_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'runtime'))
LOGS_DIR    = os.path.normpath(os.path.join(BASE_DIR, '..', 'logs'))

sys.path.insert(0, BASE_DIR)
import secrets_manager
import control as ctrl_module
import process_manager
from config import cfg


def _read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _read_jsonl_tail(path, n=50):
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        out = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out
    except Exception:
        return []


class KarbHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def _is_localhost(self):
        return self.client_address[0] in ('127.0.0.1', '::1', 'localhost')

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _send_403(self):
        self._send_json({'error': 'localhost only'}, 403)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/engine/status':
            self._send_json(process_manager.get_engine_status())

        elif self.path == '/api/state':
            self._send_json(_read_json(os.path.join(RUNTIME_DIR, 'state.json')))

        elif self.path == '/api/data':
            self._send_json({
                'state':   _read_json(os.path.join(RUNTIME_DIR, 'state.json')),
                'quotes':  _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json')),
                'performance': _read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json')),
                'limits': {
                    'min_net_surplus_bp': cfg.min_net_surplus_bp,
                    'daily_loss_limit_krw': cfg.daily_loss_limit_krw,
                },
                'control': ctrl_module.get_control_state(),
                'engine':  process_manager.get_engine_status(),
            })

        elif self.path == '/api/perf':
            perf = _read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json'))
            recent = _read_jsonl_tail(os.path.join(LOGS_DIR, 'paper_trades.jsonl'), 100)
            exits = [r for r in recent if r.get('event') == 'EXIT'][-20:]
            self._send_json({'performance': perf, 'recent_trades': exits})

        elif self.path == '/api/performance':
            self._send_json(_read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json')))

        elif self.path == '/api/trades/recent':
            recent = _read_jsonl_tail(os.path.join(LOGS_DIR, 'paper_trades.jsonl'), 100)
            exits = [r for r in recent if r.get('event') == 'EXIT'][-20:]
            self._send_json({'trades': exits})

        elif self.path == '/api/session/last':
            self._send_json(_read_json(os.path.join(RUNTIME_DIR, 'last_session_summary.json')))

        elif self.path == '/api/keys/status':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_json(secrets_manager.get_key_status())

        elif self.path == '/api/health':
            self._send_json({'status': 'ok'})

        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/engine/start':
            if not self._is_localhost():
                self._send_403()
                return
            length = int(self.headers.get('Content-Length', 0))
            body_bytes = self.rfile.read(length)
            try:
                body = json.loads(body_bytes.decode('utf-8'))
            except Exception:
                body = {}
            mode = body.get('mode', 'paper')
            result = process_manager.start_engine(mode)
            self._send_json(result)

        elif self.path == '/api/engine/stop':
            if not self._is_localhost():
                self._send_403()
                return
            result = process_manager.stop_engine()
            self._send_json(result)

        elif self.path == '/api/stop':
            if not self._is_localhost():
                self._send_403()
                return
            result = ctrl_module.request_stop()
            self._send_json({
                'ok': True,
                'message': 'Stop requested. Engine will finalize session report.',
                'run_id': result.get('run_id', ''),
            })

        elif self.path == '/api/keys/save':
            if not self._is_localhost():
                self._send_403()
                return
            length = int(self.headers.get('Content-Length', 0))
            body_bytes = self.rfile.read(length)
            try:
                body = json.loads(body_bytes.decode('utf-8'))
            except Exception:
                self._send_json({'ok': False, 'message': '잘못된 JSON'}, 400)
                return
            result = secrets_manager.save_keys(
                upbit_access  =body.get('upbit_access_key', ''),
                upbit_secret  =body.get('upbit_secret_key', ''),
                binance_api   =body.get('binance_api_key', ''),
                binance_secret=body.get('binance_api_secret', ''),
            )
            self._send_json(result)
        else:
            self.send_error(404)


def run(port=8000, once=False):
    httpd = HTTPServer(('', port), KarbHandler)
    print(f"[WebServer] http://localhost:{port}")
    if once:
        httpd.handle_request()
    else:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="KARB Web Server")
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    run(port=args.port, once=args.once)
