from config import cfg

class ExecutionEngine:
    def execute(self, calc_result):
        if cfg.mode == 'paper':
            raise Exception("Cannot call actual execution engine in paper mode")
        
        # Guarded live execution logic
        pass
