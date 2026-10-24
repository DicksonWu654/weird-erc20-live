#!/usr/bin/env python3
"""Pin external failure-recipient runtime at both observation blocks."""
import asyncio
import hashlib
import json
from pathlib import Path
from delayed_rpc import ROOT,endpoint,result
OUT=ROOT/'data/verify/delayed'
async def main():
 ep=endpoint('dependency_code',rps=1.)
 rows=[]
 try:
  for p in sorted((OUT/'trace_review/origin_details').glob('*.json')):
   tr=json.loads(p.read_text());sid=tr['sample_id']
   if tr.get('origin') not in ('token_dependency','out_of_gas'):continue
   c=json.loads((OUT/'case_details'/f'{sid}.json').read_text());dep=tr['origin_address']
   out=dict(sample_id=sid,dependency=dep,evidence=[])
   for state,block in [('early',c['early_block']),('late',c['late_block'])]:
    code,ev=await result(ep,'eth_getCode',[dep,hex(block)]);out['evidence'].append(ev)
    out['code_'+state]=code;out['code_'+state+'_nbytes']=(len(code)-2)//2
    out['code_'+state+'_sha256']=hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()
   rows.append(out)
   print(sid,dep,out['code_early_nbytes'],out['code_late_nbytes'],'equal',out['code_early']==out['code_late'],flush=True)
 finally:await ep.close()
 (OUT/'dependency_code.json').write_text(json.dumps(rows,indent=2)+'\n')
if __name__=='__main__':asyncio.run(main())
