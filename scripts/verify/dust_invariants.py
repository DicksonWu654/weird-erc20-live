"""C16 frame/selection/run/raw invariants across the V2 boundary."""
import gzip
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from dust_rpc import ROOT,DUST,WETH,ZERO


def main():
    d=pd.read_csv(DUST/'v2_all_2025_2026.csv',dtype={'saved_raw_quote_wei':str})
    q=pd.read_parquet(ROOT/'data/census/dex_pools.parquet',columns=['pool_address','token0','token1','block','log_index','dex','quote_token','quote_symbol'])
    q=q[q.token0.isin(set(d.address))|q.token1.isin(set(d.address))]
    long=pd.concat([q.assign(token=q.token0),q.assign(token=q.token1)])
    long=long[long.token.isin(set(d.address))&long.quote_token.notna()]
    first=long.sort_values(['block','log_index']).drop_duplicates('token').set_index('token')
    order=['WETH','ETH','USDC','USDT','DAI','USDS','PYUSD','WBTC','cBBTC','BUSD','FRAX','LUSD','USDe','EURC']
    long['rank']=long.quote_symbol.map({k:i for i,k in enumerate(order)})
    priority=long.sort_values(['rank','block','log_index']).drop_duplicates('token').set_index('token')
    d['selected_matches_earliest_quoted']=d.address.map(first.pool_address).eq(d.selected_pool)
    d['selected_matches_documented_quote_priority']=d.address.map(priority.pool_address).eq(d.selected_pool)
    d['priority_pool']=d.address.map(priority.pool_address)
    d['selected_pool_creation_match']=d.address.map(first.block).eq(d.first_quoted_pool_block)
    d['sim_offset']=d.sim_block-d.first_quoted_pool_block
    d['token0_quote']=d.saved_token0.eq(WETH)
    d['creation_to_pool_blocks']=d.first_quoted_pool_block-d.creation_block
    grouped=[]
    for y,g in d.groupby('year'):
        grouped.append(dict(year=int(y),n=len(g),source_counts=g.source.value_counts().to_dict(),
                            frame_basis_counts=g.frame_basis.value_counts().to_dict(),stratum_counts=g.stratum.value_counts().to_dict(),
                            quote_counts=g.quote_symbol.value_counts().to_dict(),
                            selected_matches_earliest_quoted=int(g.selected_matches_earliest_quoted.sum()),
                            differs_documented_priority=int((~g.selected_matches_documented_quote_priority).sum()),
                            pool_block_mismatch=int((~g.selected_pool_creation_match).sum()),
                            sim_offset_counts=g.sim_offset.value_counts().to_dict(),token0_quote_fraction=float(g.token0_quote.mean()),
                            creation_pool_median_blocks=float(g.creation_to_pool_blocks.median()),
                            saved_run_prefix_counts=g.sim_raw.str.split('/').str[:4].str.join('/').value_counts().to_dict(),
                            retrieved_min=g.sim_retrieved_at.min(),retrieved_max=g.sim_retrieved_at.max()))
    (DUST/'boundary_invariants.json').write_text(json.dumps(grouped,indent=2))
    d[~d.selected_matches_documented_quote_priority].to_csv(DUST/'pool_selection_documentation_differences.csv',index=False)
    d[['sample_id','year','selected_matches_earliest_quoted','selected_matches_documented_quote_priority','priority_pool','selected_pool_creation_match','sim_offset','creation_to_pool_blocks']].to_csv(DUST/'selection_checks.csv',index=False)
    # Audit every selected raw sample; extract actual parent and reserve call ordering.
    rows=[]
    for r in pd.read_csv(DUST/'reserve_sample.csv').to_dict('records'):
        data=gzip.open(ROOT/r['sim_raw'],'rb').read();raw=json.loads(data)
        phases=[e for e in raw if e.get('request',{}).get('method')=='eth_simulateV1' and str(e.get('label','')).startswith('phase1')]
        info=[]
        for e in phases:
            req=e['request'];calls=req['params'][0]['blockStateCalls'][0]['calls']
            reserves=[i for i,c in enumerate(calls) if c.get('to','').lower()==r['selected_pool'].lower() and c.get('data')=='0x0902f1ac']
            info.append(dict(label=e.get('label'),parent=req['params'][1],reserve_call_indices=reserves,
                             other_calls=[c.get('data','')[:10] for c in calls],retrieved_at=e.get('retrieved_at')))
        rows.append(dict(sample_id=r['sample_id'],raw=r['sim_raw'],hash_matches=hashlib.sha256(data).hexdigest()==r['sim_raw_sha256'],
                         sim_block=int(r['sim_block']),phases=info))
    (DUST/'raw_sample_checks.json').write_text(json.dumps(rows,indent=2))
    # Pin code and saved run evidence; git history provides bounds, not an invented runtime commit.
    names=['scripts/sim/engine.py','scripts/sim/pipeline.py','scripts/sim/classify.py','scripts/census/frame.py','scripts/census/pool_frame.py',
           'scripts/census/dex_build.py','scripts/tier2/build_outputs.py','scripts/tier2/aggregate.py','scripts/tier2/quote_prices.py']
    pins=[]
    for name in names:
        raw=(ROOT/name).read_bytes();pins.append(dict(path=name,sha256=hashlib.sha256(raw).hexdigest(),
           last_commit=subprocess.check_output(['git','log','-1','--format=%H %aI','--',name],cwd=ROOT,text=True).strip()))
    (DUST/'code_manifest.json').write_text(json.dumps(pins,indent=2))
    print(json.dumps(grouped,indent=2));print('raw hashes',sum(r['hash_matches'] for r in rows),'/',len(rows))


if __name__=='__main__':main()
