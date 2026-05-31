import json
import time
import os

class EventLogger:
    def __init__(self):
        self.decisions_path = os.path.join(os.path.dirname(__file__), '..', 'logs', 'decisions.jsonl')
        
    def log_decision(self, calc_result):
        record = {
            'timestamp': time.time(),
            **calc_result
        }
        with open(self.decisions_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')
