#!/usr/bin/env python3
"""Separate V4 diagnostic for existing cohort tokens with saved V4 pool keys.

Retains the original V2/V3 pair selection and reuses this round's provider
answers. A V4 result is supplementary, never a replacement in the main cohort.
"""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'data/validate/round3'


def main():
    rows = [json.loads(l) for l in (ROOT/'data/validate/round2/subset.jsonl').open()]
    cohort = {r['token'].lower(): (i//80, r) for i, r in enumerate(rows) if r['chain'] == 1}
    selected = {}
    source = ROOT/'data/tier2/v4-results/results.jsonl'
    for l in source.open():
        r = json.loads(l)
        t = r['token'].lower()
        if t in cohort:
            selected[t] = r
    dest = OUT/'local/v4'
    dest.mkdir(parents=True, exist_ok=True)
    done = set()
    deadline = time.time()+4*3600
    while len(done) < len(selected):
        if time.time() > deadline:
            raise RuntimeError('V4 supplement provider wait expired')
        for token, old in selected.items():
            if token in done:
                continue
            bi, meta = cohort[token]
            provider_path = OUT/'local/providers'/f'{bi:03d}'/'1'/token/'record.json'
            if not provider_path.exists():
                continue
            run = dest/token
            result = run/'results.jsonl'
            if not result.exists():
                run.mkdir(parents=True, exist_ok=True)
                pair = {'token': token, 'pool': old['pool'], 'dex': 'uniswap_v4',
                        'created_block': old.get('created_block'), **(old.get('extra') or {})}
                pairs = run/'pairs.csv'
                with pairs.open('w', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=list(pair)); w.writeheader(); w.writerow(pair)
                cmd = [sys.executable, '-B', str(ROOT/'scripts/sim/run.py'), '--pairs', str(pairs),
                       '--mode', 'latest', '--out', str(run), '--raw-dir', f'data/validate/round3/local/v4/raw/{token}',
                       '--rug', 'off', '--no-etherscan', '--concurrency', '1', '--rps-fresh', '1', '--rps-archive', '0.5']
                with (run/'sim.log').open('a') as log:
                    subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            rec = json.loads(provider_path.read_text())
            sr = json.loads(result.read_text().splitlines()[-1])
            from datetime import datetime
            stamps = [datetime.fromisoformat(x.replace('Z', '+00:00')).timestamp()
                      for x in (sr['retrieved_at'], rec['honeypot']['retrieved_at'], rec['goplus']['retrieved_at']) if x]
            rec.update(sim={'block': sr['block'], 'retrieved_at': sr['retrieved_at'], 'evidence': sr['evidence']},
                       threeway_window_seconds=max(stamps)-min(stamps), diagnostic='v4_existing_cohort_alternative_pool')
            (run/'timing.json').write_text(json.dumps([rec], indent=2)+'\n')
            assert rec['threeway_window_seconds'] <= 1800, 'V4 supplement exceeded alignment'
            done.add(token)
            print(json.dumps({'token': token, 'label': sr['primary_label'], 'reason': sr.get('label_reason'),
                              'window_seconds': rec['threeway_window_seconds']}), flush=True)
        if len(done) < len(selected):
            time.sleep(10)
    (OUT/'v4_complete.json').write_text(json.dumps({'n_existing_cohort_tokens': len(selected),
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'main_pair_selection_unchanged': True}, indent=2)+'\n')


if __name__ == '__main__':
    main()
