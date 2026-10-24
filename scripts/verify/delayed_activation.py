#!/usr/bin/env python3
"""Locate an adjacent-block empty/code activation bracket for delayed recipients."""
import asyncio
import gzip
import json
from delayed_rpc import ROOT,endpoint,result
OUT=ROOT/'data/verify/delayed'
async def main():
 ep=endpoint('activation',rps=1.);fallback=endpoint('activation_fallback','https://gateway.tenderly.co/public/mainnet',rps=1.);rows=[]
 cache={}
 for p in (OUT/'raw/activation').rglob('*.json.gz'):
  env=json.load(gzip.open(p,'rt'));req=env['request'];resp=env.get('response') or {}
  if req['method']=='eth_getCode' and 'result' in resp:
   import hashlib
   body=gzip.open(p,'rb').read()
   cache[json.dumps([req['method'],req['params']])]=(resp['result'],dict(raw=str(p.relative_to(ROOT)),sha256=hashlib.sha256(body).hexdigest()))
 async def read(method,params):
  key=json.dumps([method,params])
  if key in cache:return cache[key]
  try:return await result(ep,method,params)
  except Exception:return await result(fallback,method,params)
 try:
  for code in json.loads((OUT/'dependency_code.json').read_text()):
   if code['code_early']!='0x' or code['code_late']=='0x':continue
   sid=code['sample_id'];p=OUT/'activation_details'/f'{sid}.json';p.parent.mkdir(exist_ok=True)
   if p.exists():rows.append(json.loads(p.read_text()));continue
   c=json.loads((OUT/'case_details'/f'{sid}.json').read_text());addr=code['dependency']
   lo,hi=c['early_block'],c['late_block'];evs=[]
   while hi-lo>1:
    mid=(lo+hi)//2;v,ev=await read('eth_getCode',[addr,hex(mid)]);evs.append(ev)
    if v=='0x':lo=mid
    else:hi=mid
   before,ev=await read('eth_getCode',[addr,hex(lo)]);evs.append(ev)
   after,ev=await read('eth_getCode',[addr,hex(hi)]);evs.append(ev)
   assert before=='0x' and after!='0x'
   block,ev=await read('eth_getBlockByNumber',[hex(hi),True]);evs.append(ev)
   auth_txs=[t for t in block['transactions'] if t.get('authorizationList')]
   # The canonical state proves activation; auth candidates are not signer-recovered.
   out=dict(sample_id=sid,recipient=addr,empty_code_block=lo,nonempty_code_block=hi,
       code_before=before,code_after=after,activation_offset=hi-c['first_liquidity_block'],
       eip7702_designator=after.startswith('0xef0100') and len(after)==48,
       delegate='0x'+after[8:] if after.startswith('0xef0100') and len(after)==48 else None,
       authorization_transactions=auth_txs,evidence=evs,
       qualification='An adjacent empty-to-code bracket, not a claim that this was the first ever activation. Authorization signers not independently recovered.')
   p.write_text(json.dumps(out,indent=2)+'\n');rows.append(out)
   print(sid,'activation bracket',lo,hi,'offset',out['activation_offset'],'7702',out['eip7702_designator'],'auth txs',len(auth_txs),flush=True)
 finally:await ep.close();await fallback.close()
 (OUT/'activation_summary.json').write_text(json.dumps(rows,indent=2)+'\n')
if __name__=='__main__':asyncio.run(main())
