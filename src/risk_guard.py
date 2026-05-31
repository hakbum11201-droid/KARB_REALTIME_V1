from config import cfg

class RiskGuard:
    def check_trade(self, calc_result):
        """
        net_expected_profit_krw / best_net_surplus_bp 기준으로 진입 여부 판단.
        expected_profit_krw는 참조하지 않는다.
        """
        # 1. best_net_surplus_bp 검사 (양방향 중 최선이 기준 미달이면 거부)
        if calc_result['best_net_surplus_bp'] < cfg.min_net_surplus_bp:
            calc_result['reason_no_trade'] = 'LOW_SURPLUS'
            return False

        # 2. net_expected_profit_krw 검사 (순수익 기준, gross 아님)
        if calc_result['net_expected_profit_krw'] < cfg.min_expected_profit_krw:
            calc_result['reason_no_trade'] = 'LOW_EXPECTED_PROFIT'
            return False

        return True
