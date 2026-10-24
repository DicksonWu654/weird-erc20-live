#!/usr/bin/env python3
"""Pin additional v2 liquidity evidence to each saved simulation's block.

Uses C17's frozen measurement function and quote-reference selection. Original
simulation records are never rewritten. This does not re-run trades or fetch
provider answers, and therefore does not change the recorded alignment window.
"""
import asyncio
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import duckdb

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'data/validate/round3'
sys.path.insert(0, str(ROOT/'scripts/validate'))
from round3_collect import Client, utc, write


class EvidenceEndpoint:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.client = Client()
        self.counter = time.time_ns()
        self.archive_before_block = 0

    async def request(self, method, params):
        assert method in ('eth_call', 'eth_getBalance')
        async with self.lock:
            self.counter += 1
            request = {'jsonrpc': '2.0', 'id': self.counter, 'method': method, 'params': params}
            path = OUT/'local/liquidity/rpc'/f'{self.counter}.json'
            archive = int(params[-1], 16) <= self.archive_before_block
            endpoint = 'https://eth.drpc.org' if archive else 'https://ethereum-rpc.publicnode.com'
            env = await asyncio.to_thread(self.client.fetch, 'rpc_archive' if archive else 'rpc', endpoint, path,
                                          payload=request, spacing=2.2 if archive else 0.65)
            try:
                response = json.loads(env['body_text'])
            except ValueError:
                response = {'error': {'message': env.get('error') or 'non_json_response'}}
            return {'request': request, 'response': response, 'retrieved_at': env['retrieved_at'],
                    'endpoint': endpoint, 'http_envelope': str(path.relative_to(ROOT)),
                    'body_sha256': env['body_sha256']}


class RawLog:
    def __init__(self):
        self.items = []

    def add(self, label, raw):
        self.items.append({'label': label, **raw})


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--retry-errors', action='store_true')
    args = ap.parse_args()
    while not (OUT/'rules/v2/engine.py').exists():
        state = json.loads((OUT/'rules_wait.json').read_text())
        if state['status'] == 'expired':
            print('v2 readiness expired; no v2 enrichment', flush=True)
            return
        await asyncio.sleep(20)
    sys.path.insert(0, str(OUT/'rules/v2'))
    import engine as E
    class Context:
        pass
    ctx = Context()
    ctx.archive = EvidenceEndpoint()
    weth = E.WETH.lower()
    # Same earliest WETH reference selection as C17, restricted to quote assets
    # actually present in the original validation traces.
    old = [json.loads(l) for l in (ROOT/'data/validate/round2/sim-latest/results.jsonl').open()]
    ctx.archive.archive_before_block = max(r['block'] for r in old)
    quotes = {str((r.get('pool_info') or {}).get('quote')).lower() for r in old}
    quotes.discard(weth)
    quotes.discard('none')
    poolfile = ROOT/'data/census/dex_pools.parquet'
    con = duckdb.connect()
    refs = con.execute("""SELECT *, CASE WHEN token0=? THEN token1 ELSE token0 END AS other
        FROM read_parquet(?) WHERE dex='uniswap_v2' AND (token0=? OR token1=?)
        AND (token0 IN (SELECT unnest(?)) OR token1 IN (SELECT unnest(?)))
        QUALIFY row_number() OVER (PARTITION BY other ORDER BY block, log_index)=1""",
        [weth, str(poolfile), weth, weth, sorted(quotes), sorted(quotes)]).fetchdf().to_dict('records')
    ctx.quote_refs = {r['other']: r for r in refs}
    write(OUT/'local/liquidity/reference_pools.json', {'source': str(poolfile.relative_to(ROOT)),
          'sha256': hashlib.sha256(poolfile.read_bytes()).hexdigest(), 'rows': refs})
    done = set()
    path = OUT/'liquidity_measurements.jsonl'
    if path.exists():
        previous = {json.loads(l)['key']: json.loads(l) for l in path.open()}
        done = {k for k, r in previous.items() if not args.retry_errors or r['measurement']['quote_liquidity_status'] == 'ok'}
    while True:
        sources = [('round2', old)]
        for p in sorted((OUT/'batches').glob('*/sim/results.jsonl')):
            # Never race an append-only simulator writer mid-record.
            if (p.parent.parent/'complete.json').exists():
                sources.append(('round3', [json.loads(l) for l in p.open()]))
        for p in sorted((OUT/'local/v4').glob('*/results.jsonl')):
            if (p.parent/'timing.json').exists():
                sources.append(('round3_v4', [json.loads(l) for l in p.open()]))
        n = 0
        with path.open('a') as f:
            for name, records in sources:
                for r in records:
                    key = f"{name}:{r['token'].lower()}:{r['pool'].lower()}:{r['block']}"
                    if key in done:
                        continue
                    log = RawLog()
                    pi = r.get('pool_info') or {}
                    pair = {'token': r['token'], 'pool': r['pool'], 'dex': r.get('dex'), 'extra': r.get('extra') or {}}
                    measured = await E.measure_quote_liquidity(ctx, log, r['token'], r['pool'], r['block'], pair, pi)
                    raw_bytes = (json.dumps(log.items, indent=2)+'\n').encode()
                    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
                    rawpath = OUT/'local/liquidity/traces'/f'{raw_sha}.json'
                    rawpath.parent.mkdir(parents=True, exist_ok=True)
                    if not rawpath.exists():
                        rawpath.write_bytes(raw_bytes)
                    rec = {'key': key, 'dataset': name, 'token': r['token'].lower(), 'pool': r['pool'],
                           'block': r['block'], 'measurement': measured, 'retrieved_at': utc(),
                           'evidence': {'raw': str(rawpath.relative_to(ROOT)), 'sha256': raw_sha},
                           'original_sim_evidence': r.get('evidence')}
                    f.write(json.dumps(rec)+'\n'); f.flush()
                    done.add(key); n += 1
                    if n % 50 == 0:
                        print(f'{utc()} measured={len(done)}', flush=True)
        print(f'{utc()} measured={len(done)} scan_complete', flush=True)
        if (OUT/'collection_complete.json').exists() and (OUT/'v4_complete.json').exists():
            write(OUT/'liquidity_complete.json', {'finished_at': utc(), 'n_measurements': len(done),
                  'measurement_source_sha256': hashlib.sha256((OUT/'rules/v2/engine.py').read_bytes()).hexdigest()})
            return
        await asyncio.sleep(20)


if __name__ == '__main__':
    asyncio.run(main())
