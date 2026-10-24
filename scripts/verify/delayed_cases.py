#!/usr/bin/env python3
"""Collect between-state logs and trace_filter for C23's 24 hand-review cases."""
import argparse
import asyncio
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
import pandas as pd
from eth_utils import keccak
from delayed_rpc import ROOT,endpoint,result
OUT=ROOT/'data/verify/delayed'
IDS=[29157,29393,29656,32374,33231,33621,34079,34481,36200,37184,38374,38574,
     40123,42282,42358,42373,42447,43283,44188,44245,44387,44547,44809,45070]
STANDARD={'0x70a08231':'balanceOf','0xa9059cbb':'transfer','0x23b872dd':'transferFrom',
          '0x095ea7b3':'approve','0xdd62ed3e':'allowance','0x18160ddd':'totalSupply',
          '0x06fdde03':'name','0x95d89b41':'symbol','0x313ce567':'decimals','0x8da5cb5b':'owner'}
TOPICS={'0x'+keccak(text=s).hex():s for s in [
 'Transfer(address,address,uint256)','Approval(address,address,uint256)',
 'Sync(uint112,uint112)','Mint(address,uint256,uint256)','Burn(address,uint256,uint256,address)',
 'Swap(address,uint256,uint256,uint256,uint256,address)','OwnershipTransferred(address,address)']}

def load(p):
 out={}
 for l in Path(p).read_text().splitlines():
  r=json.loads(l);out[int(r['sample_id'])]=r
 return out

def read_evidence(ev):
 b=gzip.open(ROOT/ev['raw'],'rb').read()
 assert hashlib.sha256(b).hexdigest()==ev['sha256']
 return json.loads(b)

async def collect(ids):
 early=load(ROOT/'data/tier2/v2/early_results.jsonl');late=load(ROOT/'data/tier2/v2/legacy_results.jsonl')
 d=pd.read_parquet(ROOT/'data/tier2/v2/token_results.parquet').set_index('sample_id')
 archive=endpoint(rps=1.);logs=endpoint('logs','https://gateway.tenderly.co/public/mainnet',rps=1.)
 sem=asyncio.Semaphore(3)
 async def one(sid):
  path=OUT/'case_details'/f'{sid}.json';path.parent.mkdir(exist_ok=True)
  if path.exists() and json.loads(path.read_text()).get('collection_complete'):return
  e,l=early[sid],late[sid];r=d.loc[sid]
  out=dict(sample_id=sid,year=r.analysis_month[:4],token=e['token'],pool=e['pool'],
           early_block=e['block'],late_block=l['block'],first_liquidity_block=int(r.first_liquidity_block),
           late_offset=l['block']-int(r.first_liquidity_block),early_category=r.category,late_category=l['primary_label'],
           early_quote_wei=e['pool_info']['quote_liquidity_eth_equiv_wei'],late_quote_wei=l['pool_info']['quote_liquidity_eth_equiv_wei'],
           early_taxes=e.get('taxes'),late_taxes=l.get('taxes'),early_buy=e.get('buy'),late_buy=l.get('buy'),
           early_sells=e.get('sells'),late_sells=l.get('sells'),
           early_evidence=e.get('evidence'),late_evidence=l.get('evidence'),evidence=[],errors=[])
  # Check that both trade states really have positive-output buys/quote exits.
  for rec in [e,l]:read_evidence(rec['evidence'])
  async with sem:
   traces=[];history_complete=False
   try:
    for after in range(0,10000,1000):
     page,ev=await result(archive,'trace_filter',[dict(fromBlock=hex(e['block']+1),toBlock=hex(l['block']),
                           toAddress=[e['token'].lower()],after=after,count=1000)])
     out['evidence'].append(ev);traces+=page
     if len(page)<1000:history_complete=True;break
    out['trace_filter_complete']=history_complete
    out['token_traces']=traces
    out['selector_counts']=dict(Counter(t.get('action',{}).get('input','0x')[:10] for t in traces))
    fn=(e.get('control') or {}).get('functions') or {}
    known={ '0x'+keccak(text=s).hex()[:8]:s for ss in fn.values() for s in ss }
    out['selector_names']={**STANDARD,**known}
    out['nonstandard_calls']=[t for t in traces if t.get('action',{}).get('input','0x')[:10] not in STANDARD]
   except Exception as exc:out['errors'].append('trace_filter:'+repr(exc))
   try:
    ls,ev=await result(logs,'eth_getLogs',[dict(address=[e['token'].lower(),e['pool'].lower()],
                                fromBlock=hex(e['block']+1),toBlock=hex(l['block']))])
    out['evidence'].append(ev);out['interval_logs']=ls
    out['log_counts']=dict(Counter(TOPICS.get(t.get('topics',[''])[0],t.get('topics',[''])[0]) for t in ls))
    out['logs_complete']=len(ls)<19990
   except Exception as exc:out['errors'].append('getLogs:'+repr(exc))
   # Exact successful nonstandard calls are reviewed as candidate state changes.
   out['state_change_transactions']=[]
   txs=list(dict.fromkeys(t.get('transactionHash') for t in out.get('nonstandard_calls',[])
                         if t.get('action',{}).get('callType') in ('call','delegatecall') and not t.get('error')))
   for tx in txs[:20]:
    item=dict(tx=tx)
    try:
     tr,ev=await result(archive,'debug_traceTransaction',[tx,dict(tracer='prestateTracer',tracerConfig=dict(diffMode=True))])
     out['evidence'].append(ev);item.update(diff=tr,evidence=ev)
     receipt,ev=await result(archive,'eth_getTransactionReceipt',[tx]);out['evidence'].append(ev)
     item.update(receipt=receipt,receipt_evidence=ev)
    except Exception as exc:item['error']=repr(exc)
    out['state_change_transactions'].append(item)
   out['state_change_tx_limit_hit']=len(txs)>20
   out['collection_complete']=bool(out.get('trace_filter_complete') and out.get('logs_complete'))
   path.write_text(json.dumps(out,indent=2)+'\n')
   print(json.dumps(dict(sample_id=sid,year=out['year'],traces=len(traces),
                   selectors=out.get('selector_counts'),log_counts=out.get('log_counts'),
                   nonstandard=len(out.get('nonstandard_calls',[])),state_transactions=len(txs),errors=out['errors'])),flush=True)
 try:await asyncio.gather(*(one(i) for i in ids))
 finally:await archive.close();await logs.close()

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--ids',nargs='*',type=int);args=ap.parse_args()
 asyncio.run(collect(args.ids or IDS))
