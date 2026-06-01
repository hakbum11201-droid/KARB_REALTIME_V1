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
import time

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
WEB_DIR     = os.path.normpath(os.path.join(BASE_DIR, '..', 'web'))
RUNTIME_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'runtime'))
LOGS_DIR    = os.path.normpath(os.path.join(BASE_DIR, '..', 'logs'))

sys.path.insert(0, BASE_DIR)
import secrets_manager
import control as ctrl_module
import process_manager
from config import cfg
from executors import TinyLiveExecutor, create_preflight_plan, get_inventory_summary, get_tiny_live_readiness
from venue_pair import venue_pair_payload

tiny_live_executor = TinyLiveExecutor()


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


def _live_readiness_payload():
    readiness = get_tiny_live_readiness()
    quotes = _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json'))
    newest_quote = max((float(item.get('timestamp', 0) or 0) for item in quotes.values()), default=0)
    quote_age_ms = max(0, (time.time() - newest_quote) * 1000) if newest_quote else None
    inventory = readiness.get('inventory') or {}
    blockers = readiness.get('blockers', [])
    readiness['live_guard'] = {
        'mode': cfg.mode,
        'enable_live_trading': cfg.enable_live_trading,
        'tiny_live_enabled': cfg.tiny_live_enabled,
        'live_order_enabled': cfg.live_order_enabled,
        'live_mode_enabled': cfg.live_mode_enabled,
        'withdrawals_enabled': cfg.withdrawals_enabled,
        'futures_hedge_enabled': cfg.futures_hedge_enabled,
        'manual_rebalance_only': cfg.manual_rebalance_only,
        'paper_pass': 'PAPER_PASS_REQUIRED' not in blockers,
        'key_status': readiness.get('key_status', {}),
        'inventory_status': 'OK' if inventory and not inventory.get('blockers') else 'BLOCKED',
        'quote_freshness': 'OK' if quote_age_ms is not None and quote_age_ms <= cfg.stale_quote_ms else 'STALE',
        'quote_age_ms': quote_age_ms,
        'min_order_status': 'OK' if cfg.tiny_live_order_krw >= 5000 else 'BLOCKED',
    }
    return readiness


def _with_plan_quote_source(payload):
    plan = payload.get('plan') if isinstance(payload, dict) else None
    if not plan or plan.get('quote_source'):
        return payload
    quotes = _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json'))
    quote = quotes.get(plan.get('symbol'), {})
    plan['quote_source'] = quote.get('source') or quote.get('upbit', {}).get('source') or quote.get('binance', {}).get('source') or 'unknown'
    return payload


def _preflight_payload():
    return _with_plan_quote_source(create_preflight_plan())


def _last_plan_payload():
    return _with_plan_quote_source(_read_json(os.path.join(RUNTIME_DIR, 'tiny_live_last_preflight.json')))


def _tiny_live_status_payload():
    status = tiny_live_executor.status()
    if status.get('last_preflight'):
        status['last_preflight'] = _with_plan_quote_source(status['last_preflight'])
    return status


def _telemetry_payload():
    telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
    return {'ok': True, 'error': '', 'blockers': [], 'telemetry': telemetry}


def _decisions_payload():
    snapshot = _read_json(os.path.join(RUNTIME_DIR, 'latest_decisions.json'))
    return {
        'ok': True, 'error': '', 'blockers': [],
        'updated_at': snapshot.get('updated_at'),
        'decisions': list(reversed(snapshot.get('decisions', [])))[:100],
    }


def _last_session_payload():
    return {
        'ok': True, 'error': '', 'blockers': [],
        **_read_json(os.path.join(RUNTIME_DIR, 'last_session_summary.json')),
    }


def _order_tracker_status_payload():
    return {'ok': True, 'error': '', 'blockers': [], 'order_tracker': tiny_live_executor.tracker.to_dict()}


def _order_tracker_recent_payload():
    return {'ok': True, 'error': '', 'blockers': [], 'events': tiny_live_executor.tracker.recent()}


def _emergency_status_payload():
    tracker = tiny_live_executor.tracker.to_dict()
    return {'ok': True, 'error': '', 'blockers': [], 'emergency': tiny_live_executor.emergency.status(tracker)}


def _strategy_pairs_payload():
    return {'ok': True, 'error': '', 'blockers': [], 'pairs': venue_pair_payload()}


def _opportunities_payload():
    return {
        'ok': True, 'error': '', 'blockers': [],
        **_read_json(os.path.join(RUNTIME_DIR, 'latest_opportunities.json')),
    }


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
        self._send_json({'ok': False, 'error': 'localhost only', 'blockers': [], 'warnings': []}, 403)

    def _send_guarded_json(self, action):
        try:
            data = action()
            if not isinstance(data, dict):
                data = {'result': data}
            data.setdefault('ok', bool(data.get('ready', True)))
            data.setdefault('error', '')
            data.setdefault('blockers', [])
            data.setdefault('warnings', [])
            self._send_json(data)
        except Exception as exc:
            self._send_json({
                'ok': False,
                'status': 'BLOCKED',
                'blockers': ['INTERNAL_ERROR'],
                'error': type(exc).__name__,
            }, 500)

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
                'strategy': _read_json(os.path.join(RUNTIME_DIR, 'latest_opportunities.json')),
                'strategy_pairs': venue_pair_payload(),
            })

        elif self.path == '/api/perf':
            perf = _read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json'))
            recent = _read_jsonl_tail(os.path.join(LOGS_DIR, 'paper_trades.jsonl'), 100)
            exits = [r for r in recent if r.get('event') == 'EXIT'][-20:]
            self._send_json({'performance': perf, 'recent_trades': exits})

        elif self.path == '/api/performance':
            self._send_json(_read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json')))

        elif self.path == '/api/telemetry':
            self._send_guarded_json(_telemetry_payload)

        elif self.path == '/api/decisions/recent':
            self._send_guarded_json(_decisions_payload)

        elif self.path == '/api/inventory':
            self._send_json(get_inventory_summary())

        elif self.path == '/api/live/readiness':
            self._send_guarded_json(_live_readiness_payload)

        elif self.path == '/api/execution/preflight':
            self._send_guarded_json(_preflight_payload)

        elif self.path == '/api/execution/last-plan':
            self._send_guarded_json(_last_plan_payload)

        elif self.path == '/api/tiny-live/status':
            self._send_guarded_json(_tiny_live_status_payload)

        elif self.path == '/api/order-tracker/status':
            self._send_guarded_json(_order_tracker_status_payload)

        elif self.path == '/api/order-tracker/recent':
            self._send_guarded_json(_order_tracker_recent_payload)

        elif self.path == '/api/emergency/status':
            self._send_guarded_json(_emergency_status_payload)

        elif self.path == '/api/strategy/pairs':
            self._send_guarded_json(_strategy_pairs_payload)

        elif self.path == '/api/opportunities':
            self._send_guarded_json(_opportunities_payload)

        elif self.path == '/api/trades/recent':
            recent = _read_jsonl_tail(os.path.join(LOGS_DIR, 'paper_trades.jsonl'), 100)
            exits = [r for r in recent if r.get('event') == 'EXIT'][-20:]
            self._send_json({'trades': exits})

        elif self.path == '/api/session/last':
            self._send_guarded_json(_last_session_payload)

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
        if self.path == '/api/execution/preflight':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_guarded_json(_preflight_payload)

        elif self.path == '/api/tiny-live/arm':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_guarded_json(tiny_live_executor.arm)

        elif self.path == '/api/tiny-live/disarm':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_guarded_json(tiny_live_executor.disarm)

        elif self.path == '/api/tiny-live/execute-once':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_guarded_json(tiny_live_executor.execute_once)

        elif self.path == '/api/emergency/manual-clear':
            if not self._is_localhost():
                self._send_403()
                return
            length = int(self.headers.get('Content-Length', 0))
            try:
                body = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
                reason = str(body.get('reason', '')).strip()
                if not reason:
                    self._send_json({'ok': False, 'error': 'CLEARING_REASON_REQUIRED', 'blockers': ['CLEARING_REASON_REQUIRED'], 'warnings': []}, 400)
                    return
                self._send_guarded_json(lambda: tiny_live_executor.manual_clear_partial_risk(reason))
            except Exception as exc:
                self._send_json({'ok': False, 'error': type(exc).__name__, 'blockers': ['MANUAL_CLEAR_FAILED'], 'warnings': []}, 400)

        elif self.path == '/api/engine/start':
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
            if mode == 'live':
                self._send_json({'ok': False, 'message': 'Full live mode is disabled. Use guarded tiny_live only.'}, 403)
                return
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
