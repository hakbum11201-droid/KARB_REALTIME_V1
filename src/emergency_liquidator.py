"""Default-disabled single-attempt Spot emergency recovery scaffold."""
import time
from datetime import date

from config import cfg
from exchange_clients import BinanceSpotPrivateClient, UpbitPrivateClient
from bithumb_private import BithumbPrivateClient


class EmergencyLiquidator:
    STRATEGIES = ('COMPLETE_MISSING_LEG', 'REVERT_FILLED_LEG')

    def build_emergency_plan(self, order_state: dict, original_plan: dict) -> dict:
        strategy = cfg.emergency_strategy
        if strategy not in self.STRATEGIES:
            strategy = 'COMPLETE_MISSING_LEG'
        legs = [
            order_state.get('left_leg') or order_state.get('upbit_leg', {}),
            order_state.get('right_leg') or order_state.get('binance_leg', {}),
        ]
        if strategy == 'COMPLETE_MISSING_LEG':
            leg = min(legs, key=lambda item: float(item.get('filled_qty', 0) or 0))
            qty = max(0, float(leg.get('requested_qty', 0) or 0) - float(leg.get('filled_qty', 0) or 0))
            side = leg.get('side')
            venue = leg.get('venue')
        else:
            leg = max(legs, key=lambda item: float(item.get('filled_qty', 0) or 0))
            qty = float(order_state.get('exposure_qty', 0) or 0)
            side = 'SELL' if leg.get('side') == 'BUY' else 'BUY'
            venue = leg.get('venue')
        requested_qty = float(leg.get('requested_qty', 0) or 0)
        qty_ratio = min(1.0, qty / requested_qty) if requested_qty > 0 else 0
        quote_age_ms = float(original_plan.get('quote_age_ms', 0) or 0)
        quote_timestamp = float(original_plan.get('quote_timestamp', 0) or 0)
        if quote_timestamp:
            quote_age_ms = max(quote_age_ms, max(0, time.time() - quote_timestamp) * 1000)
        return {
            'plan_id': order_state.get('plan_id', ''), 'strategy': strategy,
            'venue': venue, 'side': side, 'symbol': order_state.get('symbol', ''),
            'qty': qty, 'order_krw': float(original_plan.get('order_krw', 0) or 0) * qty_ratio,
            'order_usdt': float(original_plan.get('order_usdt', 0) or 0) * qty_ratio,
            'quote_age_ms': quote_age_ms,
            'estimated_slippage_bp': original_plan.get('emergency_estimated_slippage_bp'),
            'one_attempt_per_plan': cfg.emergency_one_attempt_per_plan,
            'created_at': time.time(),
        }

    def can_execute_emergency(self, order_state: dict, original_plan: dict, config=cfg) -> dict:
        blockers = []
        emergency_plan = self.build_emergency_plan(order_state, original_plan)
        if not order_state or not order_state.get('plan_id') or order_state.get('plan_id') != original_plan.get('plan_id'):
            blockers.append('ORDER_LEDGER_UNSYNCED')
        if not config.emergency_liquidation_enabled:
            blockers.append('EMERGENCY_DISABLED')
        if not config.emergency_auto_execute:
            blockers.append('EMERGENCY_AUTO_EXECUTE_DISABLED')
        if config.mode != 'tiny_live':
            blockers.append('MODE_GUARD')
        if not order_state.get('emergency_required'):
            blockers.append('EMERGENCY_NOT_REQUIRED')
        if config.emergency_one_attempt_per_plan and order_state.get('emergency_attempted'):
            blockers.append('EMERGENCY_ATTEMPTED_ALREADY')
        if order_state.get('emergency_status') == 'EMERGENCY_FAILED':
            blockers.append('EMERGENCY_FAILED')
        if emergency_plan['qty'] <= 0:
            blockers.append('EMERGENCY_QTY_UNAVAILABLE')
        if config.emergency_require_fresh_quote and emergency_plan['quote_age_ms'] > config.stale_quote_ms:
            blockers.append('STALE_QUOTE')
        if emergency_plan['order_krw'] > config.emergency_max_order_krw:
            blockers.append('EMERGENCY_LIMIT_EXCEEDED')
        slippage = original_plan.get('emergency_estimated_slippage_bp')
        if slippage is None:
            blockers.append('EMERGENCY_SLIPPAGE_UNVERIFIED')
        elif float(slippage or 0) > config.emergency_max_slippage_bp:
            blockers.append('EMERGENCY_SLIPPAGE_TOO_HIGH')
        if not original_plan.get('inventory_sufficient', True):
            blockers.append('INVENTORY_SHORTAGE')
        attempts_today = (
            int(order_state.get('emergency_attempts_today', 0) or 0)
            if order_state.get('emergency_attempt_date') == date.today().isoformat() else 0
        )
        if attempts_today >= config.emergency_max_attempts_per_day:
            blockers.append('EMERGENCY_LIMIT_EXCEEDED')
        blockers.extend(self._inventory_blockers(emergency_plan) if not blockers else [])
        return {'ready': not blockers, 'blockers': blockers, 'plan': emergency_plan}

    def execute_emergency_once(self, order_state: dict, original_plan: dict, check=None) -> dict:
        """Submit at most one guarded Spot recovery order for one plan."""
        check = check or self.can_execute_emergency(order_state, original_plan)
        if not check['ready']:
            return {
                'ok': False, 'status': 'EMERGENCY_PENDING', 'blockers': check['blockers'],
                'emergency_plan': check['plan'], 'suggested_manual_action': self.manual_action(order_state),
            }
        plan = check['plan']
        try:
            if plan['venue'] == 'UPBIT':
                client = UpbitPrivateClient()
                response = (client.place_market_buy_krw(plan['symbol'], plan['order_krw'])
                            if plan['side'] == 'BUY' else client.place_market_sell_qty(plan['symbol'], plan['qty']))
            elif plan['venue'] == 'BINANCE':
                client = BinanceSpotPrivateClient()
                response = (client.place_market_buy_quote(plan['symbol'], plan['order_usdt'])
                            if plan['side'] == 'BUY' else client.place_market_sell_qty(plan['symbol'], plan['qty']))
            else:
                client = BithumbPrivateClient()
                response = (client.place_market_buy_krw(plan['symbol'], plan['order_krw'])
                            if plan['side'] == 'BUY' else client.place_market_sell_qty(plan['symbol'], plan['qty']))
            order_id = str(response.get('uuid') or response.get('orderId') or '')
            if not order_id:
                raise RuntimeError('EMERGENCY_ORDER_ID_MISSING')
            fill = (
                client.wait_order_filled(plan['symbol'], order_id, cfg.order_ttl_sec)
                if plan['venue'] == 'BINANCE'
                else client.wait_order_filled(order_id, cfg.order_ttl_sec)
            )
            if not fill.get('filled'):
                return {
                    'ok': False, 'status': 'EMERGENCY_FAILED',
                    'blockers': ['EMERGENCY_FILL_INCOMPLETE'], 'emergency_plan': plan,
                    'response': {'exchange_order_id': order_id}, 'fill': fill,
                    'suggested_manual_action': self.manual_action(order_state),
                }
            return {
                'ok': True, 'status': 'EMERGENCY_DONE', 'blockers': [], 'emergency_plan': plan,
                'response': {'exchange_order_id': order_id}, 'fill': fill,
            }
        except Exception as exc:
            return {
                'ok': False, 'status': 'EMERGENCY_FAILED', 'blockers': ['EMERGENCY_ORDER_FAILED'],
                'error': f'{type(exc).__name__}: {exc}', 'emergency_plan': plan,
                'suggested_manual_action': self.manual_action(order_state),
            }

    def execute_emergency(self, order_state: dict, original_plan: dict, check=None) -> dict:
        """Compatibility wrapper for the explicitly single-attempt path."""
        return self.execute_emergency_once(order_state, original_plan, check=check)

    def preview(self, order_state: dict, original_plan: dict) -> dict:
        check = self.can_execute_emergency(order_state, original_plan)
        return {
            'ok': True, 'status': 'PREVIEW_ONLY', 'blockers': check['blockers'],
            'ready_for_auto_execute': check['ready'], 'emergency_plan': check['plan'],
            'manual_action': self.manual_action(order_state),
        }

    def manual_action(self, order_state: dict) -> str:
        return (
            'New entries are blocked. Inspect both venue fill histories and balances. '
            'Manually resolve the remaining Spot exposure, then use MANUAL CLEAR PARTIAL RISK with a reason. '
            'Automatic repeated orders are disabled.'
        )

    def status(self, order_state: dict) -> dict:
        today = date.today().isoformat()
        attempts_today = (
            int(order_state.get('emergency_attempts_today', 0) or 0)
            if order_state.get('emergency_attempt_date') == today else 0
        )
        return {
            'enabled': cfg.emergency_liquidation_enabled,
            'auto_execute': cfg.emergency_auto_execute,
            'strategy': cfg.emergency_strategy,
            'one_attempt_per_plan': cfg.emergency_one_attempt_per_plan,
            'require_fresh_quote': cfg.emergency_require_fresh_quote,
            'attempts_today': attempts_today,
            'attempt_date': today,
            'emergency_required': bool(order_state.get('emergency_required')),
            'emergency_attempted': bool(order_state.get('emergency_attempted')),
            'emergency_status': order_state.get('emergency_status', 'NOT_REQUIRED'),
            'failed_leg': order_state.get('failed_leg', ''),
            'filled_leg': order_state.get('filled_leg', ''),
            'exposure_qty': order_state.get('exposure_qty', 0),
            'exposure_side': order_state.get('exposure_side', 'FLAT'),
            'exposure_notional_krw': order_state.get('exposure_notional_krw', 0),
            'suggested_manual_action': self.manual_action(order_state) if order_state.get('emergency_required') else '',
        }

    @staticmethod
    def _inventory_blockers(plan: dict) -> list[str]:
        if plan['venue'] == 'UPBIT':
            balances = UpbitPrivateClient().get_balances()
            asset = 'KRW' if plan['side'] == 'BUY' else plan['symbol']
            needed = plan['order_krw'] if asset == 'KRW' else plan['qty']
        elif plan['venue'] == 'BINANCE':
            balances = BinanceSpotPrivateClient().get_balances()
            asset = 'USDT' if plan['side'] == 'BUY' else plan['symbol']
            needed = plan['order_usdt'] if asset == 'USDT' else plan['qty']
        else:
            balances = BithumbPrivateClient().get_balances()
            asset = 'KRW' if plan['side'] == 'BUY' else plan['symbol']
            needed = plan['order_krw'] if asset == 'KRW' else plan['qty']
        if not balances.get('ok'):
            return list(balances.get('blockers', ['INVENTORY_SHORTAGE']))
        return [] if float(balances.get('balances', {}).get(asset, 0) or 0) >= needed else ['INVENTORY_SHORTAGE']
