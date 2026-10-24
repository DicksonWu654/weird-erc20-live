#!/usr/bin/env python3
"""Poll C17's readiness marker every ten minutes for at most ten hours."""
import hashlib
import json
from pathlib import Path
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'data/validate/round3'
MARKER = ROOT/'data/tier2/v2/RULES_V2_READY'


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    statepath = OUT/'rules_wait.json'
    state = json.loads(statepath.read_text()) if statepath.exists() else {'started_epoch': time.time(), 'started_at': now(), 'polls': []}
    deadline = state['started_epoch']+10*3600
    while True:
        ready = MARKER.exists()
        state['polls'].append({'checked_at': now(), 'ready': ready})
        state['status'] = 'ready' if ready else 'expired' if time.time() >= deadline else 'waiting'
        if ready:
            dest = OUT/'rules/v2'
            dest.mkdir(parents=True, exist_ok=True)
            (dest/'RULES_V2_READY').write_bytes(MARKER.read_bytes())
            # Rule source snapshot plus direct local imports, retained for review.
            for p in (ROOT/'scripts/sim').glob('*.py'):
                (dest/p.name).write_bytes(p.read_bytes())
            (dest/'manifest.json').write_text(json.dumps({'captured_at': now(), 'marker': str(MARKER.relative_to(ROOT)),
                'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir() if p.is_file()}}, indent=2)+'\n')
        statepath.write_text(json.dumps(state, indent=2)+'\n')
        print(json.dumps(state['polls'][-1] | {'status': state['status']}), flush=True)
        if state['status'] != 'waiting':
            return
        time.sleep(min(600, max(0, deadline-time.time())))


if __name__ == '__main__':
    main()
