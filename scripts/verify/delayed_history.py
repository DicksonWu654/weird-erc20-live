#!/usr/bin/env python3
"""Resume capped histories; inspect external dependencies identified by sell traces."""
import asyncio
import gzip
import json
from collections import Counter
from pathlib import Path
from delayed_rpc import ROOT,endpoint,result
from delayed_cases import STANDARD
OUT=ROOT/'data/verify/delayed'

async def main():
 ep=endpoint('history_followup',rps=.6)
 async def history(addr,lo,hi,evs):
  try:
   page,ev=await result(ep,'trace_filter',[dict(fromBlock=hex(lo),toBlock=hex(hi),
                    toAddress=[addr.lower()],count=2000)])
   evs.append(ev)
   if len(page)<2000:return page,True
  except Exception:
   if lo==hi:raise
  if lo==hi:return page,False
  mid=(lo+hi)//2
  a,ac=await history(addr,lo,mid,evs);b,bc=await history(addr,mid+1,hi,evs)
  return a+b,ac and bc
 try:
  for p in sorted((OUT/'case_details').glob('*.json')):
   r=json.loads(p.read_text())
   if r.get('trace_filter_complete'):continue
   ts,complete=await history(r['token'],r['early_block']+1,r['late_block'],r['evidence'])
   r['trace_filter_complete']=complete
   r['token_traces']=ts;r['selector_counts']=dict(Counter(t.get('action',{}).get('input','0x')[:10] for t in ts))
   r['nonstandard_calls']=[t for t in ts if t.get('action',{}).get('input','0x')[:10] not in STANDARD]
   r['collection_complete']=bool(r.get('trace_filter_complete') and r.get('logs_complete'))
   p.write_text(json.dumps(r,indent=2)+'\n')
   print('resumed',r['sample_id'],len(ts),'complete',r['trace_filter_complete'],flush=True)
  for p in sorted((OUT/'trace_review/origin_details').glob('*.json')):
   tr=json.loads(p.read_text());sid=tr['sample_id']
   if tr.get('origin') not in ('token_dependency','out_of_gas'):continue
   c=json.loads((OUT/'case_details'/f'{sid}.json').read_text())
   dest=OUT/'dependency_details'/f'{sid}.json';dest.parent.mkdir(exist_ok=True)
   if dest.exists():continue
   dep=tr['origin_address'];evs=[]
   rows,complete=await history(dep,c['early_block']+1,c['late_block'],evs)
   # Record state changes, including emittedless owner setters. Exclude static reads.
   calls=[t for t in rows if t.get('action',{}).get('callType') in ('call','delegatecall') and not t.get('error')]
   txs=list(dict.fromkeys(t['transactionHash'] for t in calls if t['action'].get('from','').lower()!=c['token'].lower()))
   state=[]
   for tx in txs[:10]:
    try:
     diff,ev=await result(ep,'debug_traceTransaction',[tx,dict(tracer='prestateTracer',tracerConfig=dict(diffMode=True))])
     evs.append(ev);state.append(dict(tx=tx,diff=diff,evidence=ev))
    except Exception as exc:state.append(dict(tx=tx,error=repr(exc)))
   out=dict(sample_id=sid,dependency=dep,early_block=c['early_block'],late_block=c['late_block'],
     history_complete=complete,n_traces=len(rows),selector_counts=dict(Counter(t.get('action',{}).get('input','0x')[:10] for t in rows)),
     traces=rows,nonstatic_successful_calls=calls,state_changes=state,state_tx_limit_hit=len(txs)>10,evidence=evs)
   dest.write_text(json.dumps(out,indent=2)+'\n')
   print('dependency',sid,dep,len(rows),'complete',complete,'nonstatic',len(calls),'txs',len(txs),flush=True)
 finally:await ep.close()
if __name__=='__main__':asyncio.run(main())
