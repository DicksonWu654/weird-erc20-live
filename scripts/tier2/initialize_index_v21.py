"""Link every selected V4 Initialize price to its existing full raw envelope."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/tier2/v2'


def build():
    pools=set()
    for name in ('early_results.jsonl','legacy_results.jsonl'):
        for line in (OUT/name).open():
            row=json.loads(line)
            if row.get('dex')=='uniswap_v4':pools.add(row['pool'].lower())
    source=ROOT/'data/raw/census/dexlogs_uniswap_v4.jsonl.gz'
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    expected=json.loads((ROOT/'data/census/evidence/dex_raw_files.json').read_text())['uniswap_v4']['sha256']
    assert digest==expected,'census Initialize raw changed'
    found={}
    with gzip.open(source,'rt') as f:
        for line_number,line in enumerate(f,1):
            env=json.loads(line)
            for log in env.get('response',{}).get('result',[]):
                pool=log['topics'][1].lower()
                if pool not in pools or log.get('removed'):continue
                data=log['data'][2:];price=int(data[3*64:4*64],16)
                found[pool]=dict(pool=pool,block=int(log['blockNumber'],16),sqrt_price_x96=str(price),
                                 log_index=int(log['logIndex'],16),transaction_hash=log['transactionHash'],
                                 raw_file=str(source.relative_to(ROOT)),raw_file_sha256=digest,
                                 raw_line_1based=line_number,raw_response_sha256=env['sha256'],
                                 url=env['url'],retrieved_at=env['retrieved_at'])
    missing=pools-set(found)
    if missing:raise ValueError(f'{len(missing)} V4 Initialize events missing from saved raw')
    with (OUT/'v4_initialize_index.jsonl').open('w') as f:
        for pool,row in sorted(found.items()):f.write(json.dumps(row,separators=(',',':'))+'\n')
    manifest=dict(n_pools=len(found),raw_file=str(source.relative_to(ROOT)),raw_file_sha256=digest,
                  index_sha256=hashlib.sha256((OUT/'v4_initialize_index.jsonl').read_bytes()).hexdigest(),
                  scope='index only; full original HTTP/RPC request and response remain in referenced raw JSONL line')
    (OUT/'v4_initialize_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':build()
