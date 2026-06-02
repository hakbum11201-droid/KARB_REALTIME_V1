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
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import os
import sys
import argparse
import time
from urllib.parse import parse_qs, urlparse

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
from bithumb_private import BithumbPrivateClient
from iceberg_planner import IcebergPlanner
from rate_limiter import rate_limiter

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


def _live_readiness_payload(pair_id='UPBIT_BINANCE'):
    readiness = get_tiny_live_readiness(pair_id)
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
        'min_order_status': 'OK' if (
            cfg.upbit_bithumb_order_krw >= cfg.bithumb_min_order_krw
            if pair_id == 'UPBIT_BITHUMB' else cfg.tiny_live_order_krw >= 5000
        ) else 'BLOCKED',
    }
    return readiness


def _with_plan_quote_source(payload):
    plan = payload.get('plan') if isinstance(payload, dict) else None
    if not plan:
        return payload
    calc = {}
    if not plan.get('quote_source'):
        quotes = _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json'))
        quote = quotes.get(plan.get('symbol'), {})
        plan['quote_source'] = quote.get('source') or quote.get('upbit', {}).get('source') or quote.get('binance', {}).get('source') or 'unknown'
        calc = quote.get('calc', {})
    if plan.get('pair_id') == 'UPBIT_BITHUMB':
        opportunities = _read_json(os.path.join(RUNTIME_DIR, 'latest_opportunities.json'))
        calc = next((
            row for row in opportunities.get('all_opportunities', [])
            if row.get('pair_id') == 'UPBIT_BITHUMB' and row.get('symbol') == plan.get('symbol')
        ), calc)
    elif not calc:
        quotes = _read_json(os.path.join(RUNTIME_DIR, 'latest_quotes.json'))
        calc = quotes.get(plan.get('symbol'), {}).get('calc', {})
    for key in (
        'selected_required_assets', 'order_krw_used', 'effective_qty',
        'max_fillable_qty_raw', 'selected_notional_krw', 'selected_qty',
        'selected_buy_price_krw', 'selected_sell_price_krw', 'notional_basis',
    ):
        if key in calc:
            plan[key] = calc[key]
    iceberg = IcebergPlanner().build_placeholder_plan(plan, cfg)
    plan.update({
        'iceberg_required': iceberg['iceberg_required'],
        'iceberg_enabled': iceberg['enabled'],
        'iceberg_execution_enabled': iceberg['execution_enabled'],
        'iceberg_slice_count': iceberg['slice_count'],
        'iceberg_warning': ' / '.join(iceberg['warnings']),
    })
    return payload


def _preflight_payload(pair_id='UPBIT_BINANCE'):
    return _with_plan_quote_source(create_preflight_plan(pair_id))


def _last_plan_payload():
    return _with_plan_quote_source(_read_json(os.path.join(RUNTIME_DIR, 'tiny_live_last_preflight.json')))


def _pair_performance_payload():
    perf = _read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json'))
    return {
        'ok': True,
        'pair_summary': perf.get('pair_summary', {}),
        'best_pair_by_pnl': perf.get('best_pair_by_pnl', ''),
        'best_pair_by_win_rate': perf.get('best_pair_by_win_rate', ''),
        'most_active_pair': perf.get('most_active_pair', ''),
        'updated_at': perf.get('updated_at', 0),
    }


def _tiny_live_status_payload():
    status = tiny_live_executor.status()
    if status.get('last_preflight'):
        status['last_preflight'] = _with_plan_quote_source(status['last_preflight'])
    return status


def _telemetry_payload():
    telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
    if not cfg.runtime_store_enabled:
        telemetry['runtime_store_warning'] = 'RUNTIME_STORE_DISABLED_WARNING'
    return {'ok': True, 'error': '', 'blockers': [], 'telemetry': telemetry}


def _rate_limit_status_payload():
    telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
    status = telemetry.get('rate_limit_status') or rate_limiter.get_status()
    return {
        'ok': True, 'error': '', 'blockers': [],
        'rate_limit': status,
        'rest_fallback_count': telemetry.get('rest_fallback_count', 0),
        'rest_fallback_skip_count': telemetry.get('rest_fallback_skip_count', 0),
    }


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


def _bithumb_status_payload():
    keys = secrets_manager.get_key_status()
    return {
        'ok': True, 'error': '', 'blockers': [],
        'public_enabled': cfg.bithumb_public_enabled,
        'private_enabled': cfg.bithumb_private_enabled,
        'upbit_bithumb_live_enabled': cfg.upbit_bithumb_live_enabled,
        'key_status': {
            'BITHUMB_ACCESS_KEY': keys['BITHUMB_ACCESS_KEY'],
            'BITHUMB_SECRET_KEY': keys['BITHUMB_SECRET_KEY'],
        },
        'withdrawals_enabled': False, 'deposits_enabled': False,
    }


def _bithumb_cache_status_payload():
    telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
    return {
        'ok': True, 'error': '', 'blockers': [],
        'cache': telemetry.get('bithumb_quote_cache_status', {}),
        'skipped_bithumb_symbol_count': telemetry.get('skipped_bithumb_symbol_count', 0),
        'skipped_bithumb_quote_reasons': telemetry.get('skipped_bithumb_quote_reasons', {}),
        'quote_history_key_count': telemetry.get('quote_history_key_count', 0),
        'quote_history_cleanup_count': telemetry.get('quote_history_cleanup_count', 0),
    }


def _bithumb_balances_payload():
    return BithumbPrivateClient().get_balances()


def _market_scanner_payload():
    return {
        'ok': True, 'error': '', 'blockers': [],
        **_read_json(os.path.join(RUNTIME_DIR, 'market_scanner.json')),
    }


def _runtime_store_status_payload():
    status = _read_json(os.path.join(RUNTIME_DIR, 'runtime_store_status.json'))
    last_snapshot_at = float(status.get('last_snapshot_at', 0) or 0)
    if last_snapshot_at:
        status['snapshot_age_sec'] = round(max(0.0, time.time() - last_snapshot_at), 2)
    return {
        'ok': True, 'error': '', 'blockers': [],
        'enabled': cfg.runtime_store_enabled,
        'snapshot_interval_sec': cfg.runtime_snapshot_interval_sec,
        **status,
    }


def _slippage_model_payload():
    return {
        'ok': True, 'error': '', 'blockers': [],
        'use_dynamic_slippage': cfg.use_dynamic_slippage,
        'base_slippage_bp': cfg.base_slippage_bp,
        'max_dynamic_slippage_bp': cfg.max_dynamic_slippage_bp,
        'depth_safety_multiplier': cfg.depth_safety_multiplier,
        'paper_latency_sim_enabled': cfg.paper_latency_sim_enabled,
        'paper_upbit_latency_ms': cfg.paper_upbit_latency_ms,
        'paper_bithumb_latency_ms': cfg.paper_bithumb_latency_ms,
        'paper_binance_latency_ms': cfg.paper_binance_latency_ms,
        'paper_latency_jitter_ms': cfg.paper_latency_jitter_ms,
        'paper_slippage_stress_bp': cfg.paper_slippage_stress_bp,
    }


def _iceberg_status_payload():
    return {
        'ok': True, 'error': '', 'blockers': [],
        'iceberg': IcebergPlanner().get_status(cfg),
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

    def _request_json(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length).decode('utf-8')) if length else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == '/api/engine/status':
            self._send_json(process_manager.get_engine_status())

        elif self.path == '/api/state':
            self._send_json(_read_json(os.path.join(RUNTIME_DIR, 'state.json')))

        elif path == '/api/data':
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
            for trade in exits:
                trade.setdefault('pair_id', 'UPBIT_BINANCE')
            self._send_json({'performance': perf, 'recent_trades': exits})

        elif self.path == '/api/performance':
            self._send_json(_read_json(os.path.join(RUNTIME_DIR, 'performance_summary.json')))

        elif self.path == '/api/performance/pairs':
            self._send_guarded_json(_pair_performance_payload)

        elif self.path == '/api/telemetry':
            self._send_guarded_json(_telemetry_payload)

        elif self.path == '/api/rate-limit/status':
            self._send_guarded_json(_rate_limit_status_payload)

        elif self.path == '/api/decisions/recent':
            self._send_guarded_json(_decisions_payload)

        elif path == '/api/inventory':
            pair_id = query.get('pair', ['UPBIT_BINANCE'])[0]
            self._send_json(get_inventory_summary(pair_id))

        elif path == '/api/live/readiness':
            pair_id = query.get('pair', ['UPBIT_BINANCE'])[0]
            self._send_guarded_json(lambda: _live_readiness_payload(pair_id))

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

        elif path == '/api/opportunities':
            self._send_guarded_json(_opportunities_payload)

        elif path == '/api/bithumb/status':
            self._send_guarded_json(_bithumb_status_payload)

        elif path == '/api/bithumb/cache-status':
            self._send_guarded_json(_bithumb_cache_status_payload)

        elif path == '/api/bithumb/balances':
            self._send_guarded_json(_bithumb_balances_payload)

        elif path == '/api/market/scanner':
            self._send_guarded_json(_market_scanner_payload)

        elif path == '/api/runtime-store/status':
            self._send_guarded_json(_runtime_store_status_payload)

        elif path == '/api/slippage/model':
            self._send_guarded_json(_slippage_model_payload)

        elif path == '/api/iceberg/status':
            self._send_guarded_json(_iceberg_status_payload)

        elif self.path == '/api/trades/recent':
            recent = _read_jsonl_tail(os.path.join(LOGS_DIR, 'paper_trades.jsonl'), 100)
            exits = [r for r in recent if r.get('event') == 'EXIT'][-20:]
            for trade in exits:
                trade.setdefault('pair_id', 'UPBIT_BINANCE')
            self._send_json({'trades': exits})

        elif self.path == '/api/session/last':
            self._send_guarded_json(_last_session_payload)

        elif self.path == '/api/keys/status':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_json(secrets_manager.get_key_status())

        elif self.path == '/api/health':
            telemetry = _read_json(os.path.join(RUNTIME_DIR, 'telemetry.json'))
            self._send_json({
                'status': 'ok',
                'ws_connected': telemetry.get('ws_connected', False),
                'upbit_last_msg_age_ms': telemetry.get('upbit_last_msg_age_ms'),
                'binance_last_msg_age_ms': telemetry.get('binance_last_msg_age_ms'),
                'reconnect_count': telemetry.get('ws_reconnect_count', 0),
                'last_error': telemetry.get('ws_last_error', ''),
                'out_of_order_drop_count': telemetry.get('out_of_order_drop_count', 0),
                'runtime_store_warning': (
                    'RUNTIME_STORE_DISABLED_WARNING' if not cfg.runtime_store_enabled else ''
                ),
            })

        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/execution/preflight':
            if not self._is_localhost():
                self._send_403()
                return
            body = self._request_json()
            self._send_guarded_json(lambda: _preflight_payload(body.get('pair_id', 'UPBIT_BINANCE')))

        elif self.path == '/api/tiny-live/arm':
            if not self._is_localhost():
                self._send_403()
                return
            body = self._request_json()
            self._send_guarded_json(lambda: tiny_live_executor.arm(body.get('pair_id', 'UPBIT_BINANCE')))

        elif self.path == '/api/tiny-live/disarm':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_guarded_json(tiny_live_executor.disarm)

        elif self.path == '/api/tiny-live/execute-once':
            if not self._is_localhost():
                self._send_403()
                return
            body = self._request_json()
            self._send_guarded_json(lambda: tiny_live_executor.execute_once(body.get('pair_id')))

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

        elif self.path == '/api/emergency/preview':
            if not self._is_localhost():
                self._send_403()
                return
            self._send_guarded_json(tiny_live_executor.preview_emergency)

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
                bithumb_access=body.get('bithumb_access_key', ''),
                bithumb_secret=body.get('bithumb_secret_key', ''),
            )
            self._send_json(result)
        else:
            self.send_error(404)


def run(port=8000, once=False):
    httpd = ThreadingHTTPServer(('', port), KarbHandler)
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
