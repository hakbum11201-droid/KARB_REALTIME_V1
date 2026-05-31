"""
web_server.py - KARB Realtime V1 경량 HTTP 서버.
엔드포인트:
  GET  /api/state         – latest_state.json overwrite 읽기
  GET  /api/data          – state + quotes 통합 (대시보드 폴링용)
  GET  /api/perf          – performance_summary + recent_trades
  GET  /api/trades/recent – 최근 paper_trades.jsonl 50건
  GET  /api/keys/status   – 키 Set/Missing 여부 (localhost만)
  POST /api/keys/save     – 키 저장 (localhost만, 키 값 재표시 금지)
  GET  /api/health        – 헬스체크

보안:
  - /api/keys/* 는 127.0.0.1 접속만 허용
  - 키 값 자체는 절대 응답에 포함하지 않음
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

# secrets_manager를 src 경로에서 임포트
sys.path.insert(0, BASE_DIR)
import secrets_manager


def _read_json(path: str, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _read_jsonl_tail(path: str, n: int = 50) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        result = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except Exception:
                    pass
        return result
    except Exception:
        return []


class KarbHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass  # 액세스 로그 억제

    def _is_localhost(self) -> bool:
        return self.client_address[0] in ('127.0.0.1', '::1', 'localhost')

    def _send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', 'http://localhost:8000')
        self.end_headers()
        self.wfile.write(body)

    def _send_403(self) -> None:
        self._send_json({'error': 'localhost only'}, 403)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/state':
            self._send_json(_read_json(os.path.join(RUNTIME_DIR, 'latest_state.json')))

        elif self.path == '/api/data':
            self._send_json({
                'state':  _read_json(os.path.join(RUNTIME_DIR, 'latest_state.json')),
                'quotes': _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json')),
            })

        elif self.path == '/api/perf':
            perf = _read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json'))
            recent = _read_jsonl_tail(os.path.join(LOGS_DIR, 'paper_trades.jsonl'), 50)
            # EXIT 이벤트만 필터
            recent_exits = [r for r in recent if r.get('event') == 'EXIT']
            self._send_json({'performance': perf, 'recent_trades': recent_exits[-20:]})

        elif self.path == '/api/trades/recent':
            recent = _read_jsonl_tail(os.path.join(LOGS_DIR, 'paper_trades.jsonl'), 100)
            exits = [r for r in recent if r.get('event') == 'EXIT'][-20:]
            self._send_json({'trades': exits})

        elif self.path == '/api/performance':
            self._send_json(_read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json')))

        elif self.path == '/api/keys/status':
            if not self._is_localhost():
                self._send_403()
                return
            # 키 값은 절대 포함하지 않음 – Set/Missing만 반환
            self._send_json(secrets_manager.get_key_status())

        elif self.path == '/api/health':
            self._send_json({'status': 'ok'})

        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/keys/save':
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
                upbit_access  = body.get('upbit_access_key', ''),
                upbit_secret  = body.get('upbit_secret_key', ''),
                binance_api   = body.get('binance_api_key', ''),
                binance_secret= body.get('binance_api_secret', ''),
            )
            # 저장 후에도 키 값은 절대 응답에 포함하지 않음
            self._send_json(result)
        else:
            self.send_error(404)


def run(port: int = 8000, once: bool = False) -> None:
    httpd = HTTPServer(('', port), KarbHandler)
    print(f"[WebServer] http://localhost:{port}  (API Keys: localhost only)")
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
    parser = argparse.ArgumentParser(description="KARB Realtime V1 Web Server")
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    run(port=args.port, once=args.once)
