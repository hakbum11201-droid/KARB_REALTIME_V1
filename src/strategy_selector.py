"""Select the best visible opportunity across configured venue pairs."""
from venue_pair import venue_pair_payload


class StrategySelector:
    def select(self, opportunities: list[dict]) -> dict:
        pairs = venue_pair_payload()
        pair_lookup = {pair['pair_id']: pair for pair in pairs}
        rows = []
        for item in opportunities:
            row = dict(item)
            pair = pair_lookup.get(row.get('pair_id'), {})
            row.setdefault('enabled', bool(pair.get('enabled')))
            row.setdefault('paper_only', bool(pair.get('paper_enabled')) and not pair.get('tiny_live_enabled'))
            rows.append(row)

        selectable = [row for row in rows if row.get('enabled')]
        best = max(selectable, key=lambda row: row.get('best_net_surplus_bp', -9999), default={})
        return {
            'best_pair': best.get('pair_id', ''),
            'best_symbol': best.get('symbol', ''),
            'best_direction': best.get('best_direction', ''),
            'best_net_surplus_bp': best.get('best_net_surplus_bp', 0),
            'expected_profit_krw': best.get('net_expected_profit_krw', 0),
            'reason_no_trade': best.get('reason_no_trade', ''),
            'all_opportunities': rows,
            'pairs': pairs,
        }
