#!/usr/bin/env python3
"""Seeded 50-row trace audit of v2 TRANSFER_FROM_FAILED honeypots.

Replays the exact saved phase-2 call prefix with diagnostic prestate diffs,
then traces the matching failed sell. Those state overrides are never used
for the primary buy/sell classification. All failures stay undetermined.
"""
import argparse
import asyncio
import copy
import gzip
import hashlib
import json
from pathlib import Path
import pandas as pd
from c17_rpc import EvidenceEndpoint, result
from early_liquidity import ROOT,OUT,load_jsonl,append
import run as SR


def saved_calls(rec):
    path=ROOT/rec['evidence']['raw']
    body=gzip.open(path,'rb').read()
    if hashlib.sha256(body).hexdigest()!=rec['evidence']['sha256']:
        raise ValueError('primary raw hash mismatch')
    envs=json.loads(body)
    env=next(e for e in envs if e.get('label') in ('phase2','phase2:v4'))
    plans=env['request']['params'][0]['blockStateCalls']
    blocks=[b for b in env['response']['result'] if b.get('calls')]
    prefix=[];expected_balance=None;sell_index=-1
    router=rec['pool_info']['router'].lower()
    for plan,block in zip(plans,blocks):
        bo=dict(plan.get('blockOverrides') or {})
        bo.update(number=block['number'],time=block['timestamp'],baseFeePerGas='0x0')
        for call,resp in zip(plan['calls'],block['calls']):
            reason=SR.PL.E.R(resp).reason or ''
            is_sell=(call['to'].lower()==router and int(call.get('gas','0x0'),16)==2500000
                     and int(call.get('value','0x0'),16)==0)
            if is_sell:sell_index+=1
            quoted=is_sell and sell_index<len(rec.get('sells') or []) and (rec['sells'][sell_index].get('expected_eth') or 0)>0
            if quoted and 'TRANSFER_FROM_FAILED' in reason:
                return plans[0].get('stateOverrides',{}),prefix,(call,bo),resp,expected_balance
            if call['to'].lower()==rec['token'].lower() and call.get('data','').lower()==SR.enc('balanceOf(address)',SR.BUYER).lower():
                expected_balance=SR.PL.E.R(resp).u()
            prefix.append((call,bo,resp))
    raise ValueError('matching failed call not found')


def apply_diff(state,diff):
    if not isinstance(diff,dict) or 'pre' not in diff or 'post' not in diff:
        raise ValueError('prestate tracer omitted diff')
    for addr in set(diff['pre'])|set(diff['post']):
        pre,post=diff['pre'].get(addr,{}),diff['post'].get(addr,{})
        target=state.setdefault(addr,{})
        for key in ('balance','nonce','code'):
            if key in post:
                v=post[key]
                target[key]=hex(v) if isinstance(v,int) else v
        slots=set(pre.get('storage',{}))|set(post.get('storage',{}))
        for slot in slots:
            target.setdefault('stateDiff',{})[slot]=post.get('storage',{}).get(slot,'0x'+'00'*32)


def origin(root,token,pool,router):
    failures=[];false_returns=[]
    def visit(node,depth=0,ancestors=()):
        if (node.get('to','').lower()==token.lower() and node.get('input','')[:10]=='0x23b872dd'
            and not node.get('error') and len(node.get('output','0x'))>=66
            and int(node['output'][2:66],16)==0):
            false_returns.append(node)
        if node.get('error'):
            failures.append(dict(depth=depth,address=node.get('to','').lower(),error=node['error'],
                                 output=node.get('output','0x'),input=node.get('input','')[:10],
                                 reason=node.get('revertReason'),ancestors=list(ancestors),
                                 gas=node.get('gas'),gasUsed=node.get('gasUsed')))
        for child in node.get('calls',[]):
            visit(child,depth+1,ancestors+(node.get('to','').lower(),))
    visit(root)
    # Follow the last failing child on each failed ancestor. Earlier failed
    # children may have been caught successfully by the token/router.
    node=root;path=[root]
    while True:
        bad=[c for c in node.get('calls',[]) if c.get('error')]
        if not bad:break
        node=bad[-1];path.append(node)
    addr=node.get('to','').lower();error=str(node.get('error','')).lower()
    if 'out of gas' in error:kind='out_of_gas'
    elif addr==token.lower():kind='token'
    elif any(n.get('to','').lower()==token.lower() for n in path[:-1]):kind='token_dependency'
    elif addr==router.lower():kind='router'
    elif addr==pool.lower():kind='pool'
    else:kind='other'
    # TransferHelper also reverts when a successful token call returns false.
    # No token frame has an EVM error in that case; the restriction is still
    # token-side, not an infrastructure failure at the router wrapper.
    if kind=='router' and false_returns:
        kind='token_returned_false';addr=token.lower()
    return kind,addr,failures


async def audit(ep,rec):
    out=dict(sample_id=rec['sample_id'],token=rec['token'],pool=rec['pool'],block=rec['block'],
             status='unprobeable',origin='undetermined',evidence=[],seed=1720261022,audit_version=2)
    try:
        state,prefix,(failed,bo),response,expected_balance=saved_calls(rec)
        # Geth override address maps are case-insensitive; normalize before
        # merging lowercase prestate addresses to avoid duplicate aliases.
        state={a.lower():copy.deepcopy(v) for a,v in state.items()}
        for j,(call,cbo,old) in enumerate(prefix):
            opts=dict(tracer='prestateTracer',tracerConfig=dict(diffMode=True),
                      stateOverrides=state,blockOverrides=cbo)
            diff,ev=await result(ep,'debug_traceCall',[call,hex(rec['block']),opts])
            out['evidence'].append(ev)
            apply_diff(state,diff)
        tree,ev=await result(ep,'debug_traceCall',[failed,hex(rec['block']),
                               dict(tracer='callTracer',stateOverrides=state,blockOverrides=bo)])
        out['evidence'].append(ev)
        expected=(response.get('error') or {}).get('data') or response.get('returnData','0x')
        if not tree.get('error') or tree.get('output','0x')!=expected:
            raise ValueError('trace does not reproduce saved outer revert')
        # Check acquired balance in the exact diagnostic post-prefix state.
        bal,ev=await result(ep,'debug_traceCall',[
            dict(to=rec['token'],data=SR.enc('balanceOf(address)',SR.BUYER),gas=hex(400000),gasPrice='0x0'),
            hex(rec['block']),dict(tracer='callTracer',stateOverrides=state,blockOverrides=bo)])
        out['evidence'].append(ev)
        balance=int(bal.get('output','0x0'),16)
        if expected_balance is None or balance!=expected_balance:
            raise ValueError('diagnostic balance does not match saved prefix')
        kind,addr,failures=origin(tree,rec['token'],rec['pool'],failed['to'])
        out.update(status='ok',origin=kind,origin_address=addr,failures=failures,
                   reproduced_outer_revert=True,balance_before_sell=balance,matched_saved_balance=True,
                   prefix_calls=len(prefix),diagnostic_state_only=True)
    except Exception as exc:
        out['error']=repr(exc)[:1000]
    (OUT/'origin_details').mkdir(exist_ok=True)
    (OUT/'origin_details'/f'{rec["sample_id"]}.json').write_text(json.dumps(out,indent=2)+'\n')
    return out


async def main():
    ap=argparse.ArgumentParser();ap.add_argument('--preview',action='store_true');a=ap.parse_args()
    records=load_jsonl(OUT/'early_results.jsonl')
    if len(records)!=27091 and not a.preview:
        raise SystemExit('final seeded sample requires all 27091 pooled outcomes')
    from reestimate_v2 import classify_record
    eligible=[r for r in records.values() if classify_record(r)[0]=='honeypot' and
              any('TRANSFER_FROM_FAILED' in (s.get('reason') or '') for s in r.get('sells') or [])]
    sample=sorted(eligible,key=lambda r:hashlib.sha256(f'1720261022:{r["sample_id"]}'.encode()).hexdigest())[:50]
    if len(sample)<50 and not a.preview:
        raise SystemExit(f'only {len(sample)} eligible honeypots')
    pd.DataFrame([dict(sample_id=r['sample_id'],token=r['token'],pool=r['pool'],block=r['block']) for r in sample]).to_csv(
        OUT/('origin_sample_preview.csv' if a.preview else 'origin_sample.csv'),index=False)
    ep=EvidenceEndpoint('https://eth.drpc.org','origin',ROOT/'data/raw/sim/c17-origin',
                        rps=.7,max_batch=1,inflight=3,timeout=90,max_retries=3)
    sem=asyncio.Semaphore(3)
    async def one(rec):
        path=OUT/'origin_details'/f'{rec["sample_id"]}.json'
        if path.exists():
            old=json.loads(path.read_text())
            if old.get('status')=='ok' and old.get('matched_saved_balance') and old.get('audit_version')==2:
                return old
        async with sem:return await audit(ep,rec)
    try:
        rows=await asyncio.gather(*(one(r) for r in sample))
    finally:await ep.close()
    pd.DataFrame([{k:v for k,v in r.items() if k not in ('failures','evidence')} for r in rows]).to_csv(OUT/'origin_attribution.csv',index=False)
    print(pd.Series([r['origin'] for r in rows]).value_counts().to_string())


if __name__=='__main__':asyncio.run(main())
