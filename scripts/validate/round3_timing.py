#!/usr/bin/env python3
"""Flatten timing and attach saved or re-fetched pinned simulator headers."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path

from round3_collect import Client, write

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'data/validate/round3'


def epoch(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp() if s else None


def main():
    if not (OUT/'collection_complete.json').exists():
        raise SystemExit('Collection not complete')
    paths = sorted((OUT/'batches').glob('*/timing.json'))
    v4_paths = sorted((OUT/'local/v4').glob('*/timing.json'))
    headers = {}
    for p in paths:
        for r in json.loads(p.read_text()):
            h = r['observed_head_before']
            if r['chain'] == 1 and h.get('block') is not None:
                headers[h['block']] = h
    client = Client()
    for group, group_paths in [('main', paths), ('v4', v4_paths)]:
        flat = []
        for path in group_paths:
            records = json.loads(path.read_text())
            for r in records:
                sim = r.get('sim')
                if sim:
                    block = sim['block']
                    if block not in headers:
                        rawpath = OUT/'local/headers'/f'{block}.json'
                        if rawpath.exists():
                            env = json.loads(rawpath.read_text())
                        else:
                            env = client.fetch('rpc_header', 'https://eth.drpc.org', rawpath,
                                payload={'jsonrpc':'2.0', 'id':1, 'method':'eth_getBlockByNumber', 'params':[hex(block), False]}, spacing=2.2)
                        body = json.loads(env['body_text'])
                        header = body.get('result') or {}
                        headers[block] = {'block': block, 'block_timestamp': int(header['timestamp'], 16) if header else None,
                            'raw': str(rawpath.relative_to(ROOT)), 'sha256': env['body_sha256'],
                            'retrieved_at': env['retrieved_at']}
                    h = headers[block]
                    ts = h.get('block_timestamp')
                    sim['block_timestamp'] = ts
                    sim['block_timestamp_utc'] = datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts is not None else None
                    sim['block_header_evidence'] = {'raw': h['raw'], 'sha256': h['sha256']}
                observed = r['observed_head_before']
                row = {'chain': r['chain'], 'token': r['token'], 'batch': r['batch'], 'part': r['part'], 'stratum': r['stratum'],
                    'sim_block': sim['block'] if sim else None, 'sim_block_timestamp': sim.get('block_timestamp') if sim else None,
                    'sim_block_timestamp_utc': sim.get('block_timestamp_utc') if sim else None,
                    'sim_retrieved_at': sim['retrieved_at'] if sim else None,
                    'sim_raw': (sim.get('evidence') or {}).get('raw') if sim else None,
                    'sim_sha256': (sim.get('evidence') or {}).get('sha256') if sim else None,
                    'sim_header_raw': (sim.get('block_header_evidence') or {}).get('raw') if sim else None,
                    'sim_header_sha256': (sim.get('block_header_evidence') or {}).get('sha256') if sim else None,
                    'observed_head_before_block': observed['block'], 'observed_head_before_timestamp': observed.get('block_timestamp'),
                    'observed_head_before_retrieved_at': observed.get('retrieved_at'), 'observed_head_raw': observed['raw'],
                    'observed_head_sha256': observed.get('sha256'), 'threeway_window_seconds': r['threeway_window_seconds']}
                stamps = []
                for p in ('honeypot', 'goplus'):
                    for field in ('started_at', 'retrieved_at', 'provider_block', 'provider_block_status', 'raw', 'body_sha256'):
                        row[p+'_'+field] = r[p].get(field)
                    stamps += [epoch(r[p].get(x)) for x in ('started_at', 'retrieved_at') if r[p].get(x)]
                if sim:
                    stamps += [epoch(sim['retrieved_at'])]
                    if sim.get('block_timestamp') is not None:
                        stamps.append(sim['block_timestamp'])
                    row['state_and_request_window_seconds'] = max(stamps)-min(stamps)
                    row['sim_block_age_seconds_at_result'] = epoch(sim['retrieved_at'])-sim['block_timestamp'] if sim.get('block_timestamp') is not None else None
                else:
                    row['state_and_request_window_seconds'] = None
                    row['sim_block_age_seconds_at_result'] = None
                flat.append(row)
            write(path, records)
        if flat:
            with (OUT/('timing.csv' if group == 'main' else 'v4_timing.csv')).open('w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=list(flat[0])); w.writeheader(); w.writerows(flat)
            print(group, len(flat), 'rows; max state/request span', max(r['state_and_request_window_seconds'] or 0 for r in flat))


if __name__ == '__main__':
    main()
