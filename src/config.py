import yaml
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.yaml')

class Config:
    def __init__(self):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            self._cfg = yaml.safe_load(f)
            
    def get(self, name, default=None):
        return self._cfg.get(name, default)

    @property
    def mode(self):
        return self.get('mode', 'paper')
        
    @property
    def symbols(self):
        return self.get('symbols', [])

    @property
    def loop_interval_sec(self):
        return self.get('loop_interval_sec', 1)

    @property
    def upbit_fee_bp(self):
        return self.get('upbit_fee_bp', 5)

    @property
    def binance_fee_bp(self):
        return self.get('binance_fee_bp', 10)

    @property
    def slippage_bp(self):
        return self.get('slippage_bp', 5)

    @property
    def fx_error_bp(self):
        return self.get('fx_error_bp', 5)

    @property
    def risk_buffer_bp(self):
        return self.get('risk_buffer_bp', 10)

    @property
    def min_net_surplus_bp(self):
        return self.get('min_net_surplus_bp', 35)

    @property
    def min_expected_profit_krw(self):
        return self.get('min_expected_profit_krw', 1000)

cfg = Config()
