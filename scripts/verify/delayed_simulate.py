#!/usr/bin/env python3
"""Targeted +1800/exact+7200 replays of case-selected rows; no prevalence claims."""
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
import pandas as pd
from delayed_rpc import ROOT,endpoint
from delayed_cases import IDS,load
sys.path.insert(0,str(ROOT/'scripts/sim'))
import run as SR
OUT=ROOT/'data/verify/delayed'

async def main():
 sel=load(ROOT/'data/tier2/v2/liquidity_selection.jsonl')
 d=pd.read_parquet(ROOT/'data/tier2/v2/token_results.parquet').set_index('sample_id')
 ctx=SR.Ctx();ep=endpoint('diagnostic_sims',rps=1.)
 ctx.mode='historical';ctx.offset=1800;ctx.head_pinned=26245798
 ctx.dust_floor_wei=10**16;ctx.run='c23-delayed';ctx.raw_dir='data/verify/delayed/raw/sims'
 ctx.amts=[10**16,5*10**14];ctx.rug=False;ctx.etherscan=None
 ctx.archive=ep;ctx.fresh=ep;ctx.sim_endpoints=[ep];ctx.measure_liquidity=True;ctx.unique_raw=True
 pp=pd.read_parquet(ROOT/'data/census/dex_pools.parquet');weth=SR.WETH.lower()
 pp=pp[pp.dex.eq('uniswap_v2')&(pp.token0.eq(weth)|pp.token1.eq(weth))].copy()
 pp['other']=pp.token1.where(pp.token0.eq(weth),pp.token0)
 ctx.quote_refs=pp.sort_values(['block','log_index']).drop_duplicates('other').set_index('other').to_dict('index')
 path=OUT/'diagnostic_sim_results.jsonl';done=set()
 if path.exists():
  for line in path.read_text().splitlines():
   r=json.loads(line)
   if not SR.needs_redo(r):done.add((r['sample_id'],r['offset_blocks']))
 sem=asyncio.Semaphore(3)
 async def one(sid,offset):
  if (sid,offset) in done:return
  s=sel[sid];block=int(s['first_liquidity_block'])+offset
  assert block<=ctx.head_pinned
  pair=dict(token=s['token'],pool=s['pool'],dex=s['dex'],created_block=int(s['created_block']),block=block,
            extra={**s,'sample_id':str(sid)})
  async with sem:
   r=await SR.process(ctx,pair);r.update(sample_id=sid,offset_blocks=offset,scope='case-selected diagnostic; not population sample')
   with path.open('a') as f:f.write(json.dumps(r,separators=(',',':'))+'\n')
   print(sid,offset,r['primary_label'],r.get('taxes'),flush=True)
 try:
  await asyncio.gather(*(one(sid,off) for sid in IDS for off in [1800,7200]))
 finally:await ep.close()
 (OUT/'diagnostic_sim_manifest.json').write_text(json.dumps(dict(
   ids=IDS,offsets=[1800,7200],rules=SR.CL.RULES,observation_head=ctx.head_pinned,
   scope='purposive diagnostic cases; excluded from prevalence calculations',
   source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
       [Path(__file__)]+[ROOT/'scripts/sim'/f'{s}.py' for s in ['run','pipeline','engine','v4','classify']]}
 ),indent=2)+'\n')
if __name__=='__main__':asyncio.run(main())
