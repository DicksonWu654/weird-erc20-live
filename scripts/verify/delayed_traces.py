#!/usr/bin/env python3
"""Diagnose the saved failed sells; primary classifications are never changed."""
import asyncio
import json
import sys
from pathlib import Path
from delayed_rpc import ROOT,endpoint
sys.path.insert(0,str(ROOT/'scripts/tier2'))
import origin_audit as OA
from delayed_cases import IDS,load
OUT=ROOT/'data/verify/delayed'
# The original auditor writes only these detail files; point its owned output here.
OA.OUT=OUT/'trace_review'
OA.OUT.mkdir(parents=True,exist_ok=True)

async def main():
 records=load(ROOT/'data/tier2/v2/legacy_results.jsonl')
 ep=endpoint('selltrace',rps=1.)
 sem=asyncio.Semaphore(3)
 async def one(sid):
  rec=records[sid]
  if not any('TRANSFER_FROM_FAILED' in (s.get('reason') or '') for s in rec.get('sells') or []):return
  path=OA.OUT/'origin_details'/f'{sid}.json'
  if path.exists() and json.loads(path.read_text()).get('matched_saved_balance'):return
  async with sem:
   r=await OA.audit(ep,rec)
   print(json.dumps({k:v for k,v in r.items() if k not in ('evidence','failures')}),flush=True)
 try:await asyncio.gather(*(one(i) for i in IDS))
 finally:await ep.close()
if __name__=='__main__':asyncio.run(main())
