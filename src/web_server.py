"""
web_server.py - 경량 HTTP 서버.
정적 파일(/web)과 API 엔드포인트(/api/data, /api/perf)를 제공한다.
실행: python src/web_server.py [--port 8000]
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import os
import argparse

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
WEB_DIR   = os.path.normpath(os.path.join(BASE_DIR, '..', 'web'))
RUNTIME_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'runtime'))
LOGS_DIR  = os.path.normpath(os.path.join(BASE_DIR, '..', 'logs'))


def _read_json(path: str, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _read_jsonl_tail(path: str, n: int = 50) -> list:
    """jsonl 파일의 마지막 n행을 파싱해서 반환."""
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
        # 액세스 로그 억제 (터미널 노이즈 방지)
        pass

    def _send_json(self, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/data':
            self._send_json({
                'state':  _read_json(os.path.join(RUNTIME_DIR, 'state.json'), {}),
                'quotes': _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json'), {}),
            })

        elif self.path == '/api/perf':
            recent_trades  = _read_jsonl_tail(os.path.join(LOGS_DIR, 'paper_trades.jsonl'), 50)
            recent_decisions = _read_jsonl_tail(os.path.join(LOGS_DIR, 'decisions.jsonl'), 50)
            self._send_json({
                'recent_trades': recent_trades,
                'recent_decisions': recent_decisions,
            })

        elif self.path == '/api/health':
            self._send_json({'status': 'ok'})

        else:
            super().do_GET()


def run(port: int = 8000, once: bool = False) -> None:
    httpd = HTTPServer(('', port), KarbHandler)
    print(f"[WebServer] Serving on http://localhost:{port}")
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
