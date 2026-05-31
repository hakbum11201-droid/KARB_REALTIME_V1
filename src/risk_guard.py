from config import cfg

class RiskGuard:
    def check_trade(self, calc_result):
        if calc_result['direction_a_net_surplus_bp'] < cfg.min_net_surplus_bp and \
           calc_result['direction_b_net_surplus_bp'] < cfg.min_net_surplus_bp:
            calc_result['reason_no_trade'] = 'LOW_SURPLUS'
            return False
            
        if calc_result['expected_profit_krw'] < cfg.min_expected_profit_krw:
            calc_result['reason_no_trade'] = 'LOW_PROFIT'
            return False
            
        return True
