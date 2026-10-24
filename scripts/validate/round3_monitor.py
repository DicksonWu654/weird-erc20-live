#!/usr/bin/env python3
"""Foreground C18 progress loop; exits when collection and dependencies finish."""
import json
from pathlib import Path
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'data/validate/round3'


def main():
    while True:
        batches = [json.loads(p.read_text()) for p in sorted((OUT/'batches').glob('*/complete.json'))]
        state = json.loads((OUT/'rules_wait.json').read_text())
        provider_n = sum(1 for _ in (OUT/'local/providers').glob('*/*/*/record.json'))
        finished = (OUT/'collection_complete.json').exists()
        liquidity = (OUT/'liquidity_complete.json').exists()
        v4 = (OUT/'v4_complete.json').exists()
        snapshot = {'utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    'provider_tokens': provider_n, 'completed_batch_tokens': sum(b['n_tokens'] for b in batches),
                    'completed_batches': len(batches), 'max_batch_window_minutes': round(max((b['batch_window_seconds'] for b in batches), default=0)/60, 2),
                    'rules_status': state['status'], 'collection_complete': finished, 'liquidity_complete': liquidity,
                    'v4_complete': v4, 'collection_log_age_s': round(time.time()-(OUT/'collection.log').stat().st_mtime)}
        (OUT/'monitor_status.json').write_text(json.dumps(snapshot, indent=2)+'\n')
        print(json.dumps(snapshot), flush=True)
        if finished and v4 and (liquidity or state['status'] == 'expired'):
            return
        time.sleep(55)


if __name__ == '__main__':
    main()
