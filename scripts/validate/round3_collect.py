#!/usr/bin/env python3
"""C18 aligned, resumable, read-only provider/simulator collection.

All provider response bodies stay in round3/local (gitignored). Each batch is
completed before the next starts; a failed/overlong batch is never called aligned.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/validate/round3'
LOCAL = OUT / 'local'
RPC = {1: 'https://ethereum-rpc.publicnode.com', 56: 'https://bsc-rpc.publicnode.com',
       8453: 'https://base-rpc.publicnode.com', 42161: 'https://arbitrum-one-rpc.publicnode.com',
       137: 'https://polygon-bor-rpc.publicnode.com', 10: 'https://optimism-rpc.publicnode.com'}


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2) + '\n')
    tmp.replace(path)


def jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


class Client:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers['User-Agent'] = 'weird-erc20-validation/round3 (read-only research)'
        self.last = {}

    def fetch(self, kind, url, path, params=None, payload=None, spacing=0.6):
        # Every attempt, including HTTP-200 GoPlus throttles, obeys pacing.
        for attempt in range(3):
            wait = self.last.get(kind, 0) + spacing - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last[kind] = time.monotonic()
            started = utc()
            env = {'url': url, 'method': 'POST' if payload else 'GET', 'params': params,
                   'request_json': payload, 'started_at': started, 'attempt': attempt}
            try:
                r = self.session.post(url, json=payload, timeout=25) if payload else self.session.get(url, params=params, timeout=25)
                body = r.content
                env.update(url=r.url, status=r.status_code, response_headers=dict(r.headers),
                           body_text=body.decode('utf-8', errors='replace'), body_sha256=hashlib.sha256(body).hexdigest())
            except requests.RequestException as e:
                env.update(status=None, body_text='', body_sha256=hashlib.sha256(b'').hexdigest(), error=repr(e))
            env.update(retrieved_at=utc(), retrieved_at_epoch_ms=int(time.time()*1000))
            attempt_path = path.with_name(path.stem + f'.attempt{attempt}.json')
            write(attempt_path, env)
            try:
                body = json.loads(env['body_text'])
            except (ValueError, TypeError):
                body = {}
            if not isinstance(body, dict):
                body = {}
            rpc_error = str(body.get('error') or '').lower() if isinstance(body, dict) else ''
            rpc_transient = kind.startswith('rpc') and any(s in rpc_error for s in ('timeout', 'rate limit', 'too many', 'capacity'))
            throttled = env['status'] == 429 or body.get('code') in (4029, 4290) or (rpc_transient and any(s in rpc_error for s in ('rate limit', 'too many')))
            retry = throttled or rpc_transient or env['status'] is None or env['status'] >= 500
            if not retry or attempt == 2:
                write(path, env)
                return env
            time.sleep(65 if throttled else 3 * (attempt + 1))

    def head(self, chain, path):
        env = self.fetch('rpc', RPC[chain], path, payload={'jsonrpc': '2.0', 'id': 1,
                        'method': 'eth_getBlockByNumber', 'params': ['latest', False]}, spacing=0.55)
        try:
            r = json.loads(env['body_text'])['result']
            return {'block': int(r['number'], 16), 'block_timestamp': int(r['timestamp'], 16),
                    'retrieved_at': env['retrieved_at'], 'raw': str(path.relative_to(ROOT)), 'sha256': env['body_sha256']}
        except (ValueError, TypeError, KeyError):
            return {'block': None, 'error': 'observed head unavailable', 'raw': str(path.relative_to(ROOT))}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--batch-size', type=int, default=80)
    ap.add_argument('--limit-batches', type=int)
    args = ap.parse_args()
    LOCAL.mkdir(parents=True, exist_ok=True)
    (LOCAL / '.gitignore').write_text('*\n!.gitignore\n')
    rows = jsonl(ROOT / 'data/validate/round2/subset.jsonl')
    assert len({(int(x['chain']), x['token'].lower()) for x in rows}) == len(rows)
    assert not any('trapdoor' in x.get('source', '').lower() or x.get('stratum', '').startswith('td_') for x in rows)
    with (ROOT / 'data/validate/round2/sim_pairs.csv').open() as f:
        pairs = list(csv.DictReader(f))
    pairmap = {x['token'].lower(): x for x in pairs}
    inputs = ['data/validate/round2/subset.jsonl', 'data/validate/round2/sim_pairs.csv',
              'data/validate/new_eth_candidates.jsonl', 'data/validate/round2/timing.csv']
    if not (OUT / 'input_manifest.json').exists():
        write(OUT / 'input_manifest.json', {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in inputs})
    snapshots = OUT / 'rules/current'
    if not snapshots.exists():
        snapshots.mkdir(parents=True)
        for name in ('classify.py', 'reclassify.py'):
            (snapshots/name).write_bytes((ROOT/'scripts/sim'/name).read_bytes())
        write(snapshots/'manifest.json', {'captured_at': utc(), 'files': {
            name: hashlib.sha256((snapshots/name).read_bytes()).hexdigest() for name in ('classify.py', 'reclassify.py')}})
    client = Client()
    for bi, start in enumerate(range(0, len(rows), args.batch_size)):
        if args.limit_batches is not None and bi >= args.limit_batches:
            break
        batch = OUT / 'batches' / f'{bi:03d}'
        if (batch/'complete.json').exists():
            continue
        batch.mkdir(parents=True, exist_ok=True)
        tokens = rows[start:start+args.batch_size]
        began = utc()
        print(f'{began} batch={bi} start n={len(tokens)}', flush=True)
        records = []
        for idx, row in enumerate(tokens):
            chain, token = int(row['chain']), row['token'].lower()
            dest = LOCAL / 'providers' / f'{bi:03d}' / str(chain) / token
            final = dest/'record.json'
            # A partial batch resumes from its fresh records. The strict span
            # check below still rejects a stale resumed batch.
            if final.exists():
                records.append(json.loads(final.read_text()))
                continue
            before = client.head(chain, dest/'observed_head.json')
            gp_path = dest/'goplus.json'
            gp = client.fetch('goplus', f'https://api.gopluslabs.io/api/v1/token_security/{chain}', gp_path,
                              params={'contract_addresses': token}, spacing=6.2)
            hp_path = dest/'honeypot.json'
            if chain in (1, 56, 8453):
                hp = client.fetch('honeypot', 'https://api.honeypot.is/v2/IsHoneypot', hp_path,
                                  params={'address': token, 'chainID': chain}, spacing=0.6)
            else:
                hp = {'retrieved_at': None, 'status': None, 'error': 'unsupported_chain'}
            rec = {'chain': chain, 'token': token, 'batch': bi, 'part': row.get('part'),
                   'stratum': row.get('stratum'), 'observed_head_before': before,
                   'goplus': {'raw': str(gp_path.relative_to(ROOT)), 'retrieved_at': gp['retrieved_at'],
                              'started_at': gp['started_at'], 'status': gp['status'], 'body_sha256': gp['body_sha256'],
                              'provider_block': None, 'provider_block_status': 'not_disclosed'},
                   'honeypot': {'raw': str(hp_path.relative_to(ROOT)) if hp_path.exists() else None,
                                'retrieved_at': hp['retrieved_at'], 'started_at': hp.get('started_at'),
                                'status': hp['status'], 'body_sha256': hp.get('body_sha256'),
                                'provider_block': None, 'provider_block_status': 'not_disclosed' if hp_path.exists() else 'unsupported_chain'}}
            write(final, rec)
            records.append(rec)
            if (idx+1) % 10 == 0:
                print(f'{utc()} batch={bi} providers={idx+1}/{len(tokens)}', flush=True)
        chosen = [pairmap[x['token'].lower()] for x in tokens if int(x['chain']) == 1 and x['token'].lower() in pairmap]
        simdir = batch/'sim'
        pairs_path = batch/'pairs.csv'
        with pairs_path.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(pairs[0])); w.writeheader(); w.writerows(chosen)
        command = [sys.executable, '-B', str(ROOT/'scripts/sim/run.py'), '--pairs', str(pairs_path), '--mode', 'latest',
                   '--out', str(simdir), '--raw-dir', f'data/validate/round3/local/sim/{bi:03d}', '--rug', 'off',
                   '--no-etherscan', '--rps-fresh', '1.5', '--rps-archive', '0.5', '--concurrency', '4']
        source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (ROOT/'scripts/sim').glob('*.py')}
        write(batch/'invocation.json', {'command': command, 'started_at': utc(), 'source_sha256': source_hashes})
        if chosen:
            with (batch/'sim.log').open('a') as log:
                proc = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            if proc.returncode:
                raise RuntimeError(f'batch {bi}: simulator exited {proc.returncode}; inspect sim.log')
        sim = {r['token'].lower(): r for r in jsonl(simdir/'results.jsonl')} if chosen else {}
        for r in records:
            sr = sim.get(r['token']) if r['chain'] == 1 else None
            r['sim'] = {'block': sr['block'], 'retrieved_at': sr['retrieved_at'], 'evidence': sr['evidence']} if sr else None
            stamps = [datetime.fromisoformat(x.replace('Z', '+00:00')).timestamp()
                      for x in [r['goplus']['started_at'], r['goplus']['retrieved_at'],
                                r['honeypot'].get('started_at'), r['honeypot']['retrieved_at'],
                                sr['retrieved_at'] if sr else None] if x]
            r['threeway_window_seconds'] = max(stamps)-min(stamps) if sr else None
        write(batch/'timing.json', records)
        times = [datetime.fromisoformat(r[p]['retrieved_at'].replace('Z', '+00:00')).timestamp()
                 for r in records for p in ('honeypot', 'goplus') if r[p]['retrieved_at']]
        times += [datetime.fromisoformat(r['retrieved_at'].replace('Z', '+00:00')).timestamp() for r in sim.values()]
        span = max(times)-min(times)
        complete = {'batch': bi, 'started_at': began, 'finished_at': utc(), 'n_tokens': len(tokens),
                    'n_sim': len(sim), 'batch_window_seconds': span, 'within_30_minutes': span <= 1800}
        write(batch/'complete.json', complete)
        print(json.dumps(complete), flush=True)
        if span > 1800:
            raise RuntimeError(f'batch {bi} exceeded alignment; preserved evidence needs a fresh smaller rerun')
    write(OUT/'collection_complete.json', {'finished_at': utc(), 'n_tokens': len(rows), 'batch_size': args.batch_size})


if __name__ == '__main__':
    main()
