"""
Read-only placeholder policy for future large-order Iceberg execution.

This module intentionally contains no order placement or slice execution code.
"""


class IcebergPlanner:
    @staticmethod
    def should_use_iceberg(order_krw, cfg):
        return float(order_krw or 0) >= float(cfg.iceberg_min_order_krw)

    def build_placeholder_plan(self, plan, cfg):
        order_krw = float((plan or {}).get('order_krw', 0) or 0)
        required = self.should_use_iceberg(order_krw, cfg)
        enabled = bool(cfg.iceberg_enabled)
        execution_enabled = bool(cfg.iceberg_execution_enabled)
        warnings = ['ICEBERG_REQUIRED'] if required else []
        blockers = []
        if required and enabled and not execution_enabled:
            blockers.extend([
                'BLOCK_LARGE_ORDER_WITHOUT_ICEBERG',
                'ICEBERG_EXECUTION_DISABLED',
            ])
        return {
            'enabled': enabled,
            'execution_enabled': execution_enabled,
            'iceberg_required': required,
            'order_krw': order_krw,
            'min_order_krw': float(cfg.iceberg_min_order_krw),
            'slice_count': int(cfg.iceberg_slice_count),
            'slice_interval_ms': int(cfg.iceberg_slice_interval_ms),
            'max_total_slippage_bp': float(cfg.iceberg_max_total_slippage_bp),
            'warnings': warnings,
            'blockers': blockers,
            'placeholder_only': True,
        }

    def get_status(self, cfg):
        return self.build_placeholder_plan({'order_krw': 0}, cfg)
