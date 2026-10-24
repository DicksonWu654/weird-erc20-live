#!/usr/bin/env python3
"""C26 real position liquidity and first-event/+300 reserve evidence.

Read-only, resumable; every new byte is inside data/tier2/v2. V4 position keys
include sender, both ticks AND salt. Removed/duplicate logs are excluded.
"""
import argparse
import asyncio
from collections import Counter, defaultdict
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

import pandas as pd
from eth_abi import encode
from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/tier2/v2'
sys.path.insert(0, str(ROOT/'scripts/sim'))
from v4 import position_amounts, V4_MANAGER, V4_STATE
from abi import WETH, ZERO
from c17_rpc import EvidenceEndpoint, result
from early_liquidity import load_jsonl, append, TOPICS

NATIVE = {WETH.lower(), ZERO.lower()}
def topic(signature): return '0x'+keccak(text=signature).hex()
V3_INIT = topic('Initialize(uint160,int24)')
V3_SWAP = topic('Swap(address,address,int256,int256,uint160,uint128,int24)')
V3_BURN = topic('Burn(address,int24,int24,uint128,uint256,uint256)')
V4_SWAP = topic('Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)')
V4_INIT = topic('Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)')
SYNC = topic('Sync(uint112,uint112)')

def words(data):
    data = data.removeprefix('0x')
    if len(data) % 64: raise ValueError('incomplete ABI words')
    return [int(data[i:i+64], 16) for i in range(0,len(data),64)]

def signed(x, bits=256):
    x = int(x) & ((1 << bits)-1)
    return x-(1 << bits) if x & (1 << (bits-1)) else x

def log_order(log):
    return int(log['blockNumber'],16), int(log['logIndex'],16)

@lru_cache(maxsize=256)
def raw_envelopes(path, sha):
    body=gzip.open(ROOT/path,'rb').read()
    if hashlib.sha256(body).hexdigest()!=sha: raise ValueError('raw hash mismatch: '+path)
    env=json.loads(body)
    if isinstance(env,list): return env
    requests=env['request'] if isinstance(env['request'],list) else [env['request']]
    responses=env.get('response')
    if responses is None and env.get('response_body'): responses=json.loads(env['response_body'])
    responses=responses if isinstance(responses,list) else [responses]
    byid={v['id']:v for v in responses if isinstance(v,dict) and 'id' in v}
    return [dict(request=r,response=byid.get(r['id'],{}),retrieved_at=env['retrieved_at']) for r in requests]

def saved_logs(refs, address, pool=None):
    found={}
    for ref in refs:
        for env in raw_envelopes(ref['raw'],ref['sha256']):
            req=env['request'];response=env['response'].get('result')
            logs=(response or {}).get('logs',[]) if req['method']=='eth_getTransactionReceipt' else (
                response if req['method']=='eth_getLogs' and isinstance(response,list) else [])
            for log in logs:
                if log.get('removed') or log.get('address','').lower()!=address.lower(): continue
                if pool and (len(log.get('topics',[]))<2 or log['topics'][1].lower()!=pool.lower()): continue
                found[(log['transactionHash'],log['logIndex'])]=log
    return sorted(found.values(),key=log_order)

def reconstruct(logs, kind, first_event=None, initial_price=None, final_price=None):
    """Exact principal snapshots; V3 uses owner/range and V4 owner/range/salt."""
    positions=defaultdict(int);price=int(initial_price) if initial_price else None
    first=None;changes=0
    for log in sorted(logs,key=log_order):
        if log.get('removed'): continue
        t=log['topics'];w=words(log['data']);sig=t[0]
        if kind=='v4':
            if sig==V4_INIT: price=w[-2]
            elif sig==V4_SWAP: price=w[2]
            elif sig==TOPICS['uniswap_v4']:
                lo,hi,delta=signed(w[0],24),signed(w[1],24),signed(w[2])
                key=('0x'+t[2][-40:].lower(),lo,hi,'0x'+f'{w[3]:064x}')
                positions[key]+=delta;changes+=1
                if positions[key]<0: raise ValueError('negative position liquidity; incomplete log history')
        else:
            if sig==V3_INIT: price=w[0]
            elif sig==V3_SWAP: price=w[2]
            elif sig in (TOPICS['uniswap_v3'],V3_BURN):
                lo,hi=signed(int(t[2],16),24),signed(int(t[3],16),24)
                key=('0x'+t[1][-40:].lower(),lo,hi,'0x'+'00'*32)
                delta=w[1] if sig==TOPICS['uniswap_v3'] else -w[0]
                positions[key]+=delta;changes+=1
                if positions[key]<0: raise ValueError('negative V3 position liquidity')
        if first_event is not None and log_order(log)==first_event:
            if not price: raise ValueError('first-event price not observed')
            first=position_snapshot(positions,price)
    if final_price: price=int(final_price)
    if not price: raise ValueError('final price not observed')
    final=position_snapshot(positions,price)
    final['n_modify_events']=changes
    return first,final

def position_snapshot(positions,price):
    a0=a1=active=0;rows=[]
    for (owner,lo,hi,salt),liquidity in sorted(positions.items()):
        x,y=position_amounts(liquidity,lo,hi,price)
        a0+=x;a1+=y
        # Match core tick convention at an exact upper boundary using price.
        from v4 import sqrt_ratio_at_tick
        if sqrt_ratio_at_tick(lo)<=price<sqrt_ratio_at_tick(hi): active+=liquidity
        rows.append(dict(owner=owner,tick_lower=lo,tick_upper=hi,salt=salt,
                         liquidity=str(liquidity),amount0=str(x),amount1=str(y)))
    return dict(amount0=str(a0),amount1=str(a1),sqrt_price_x96=str(price),
                active_liquidity=str(active),positions=rows,n_positions=len(rows))

def reserve_direction(first_quote,first_token,last_quote,last_token):
    if any(v is None for v in (first_quote,first_token,last_quote,last_token)):return 'unknown'
    fq,ft,lq,lt=map(int,(first_quote,first_token,last_quote,last_token))
    if lq<fq and lt<ft:return 'withdrawal'
    if lq<fq and lt>ft:return 'dumping'
    return 'other'


@lru_cache(maxsize=1)
def quote_references():
    """Same immutable public V2 pool ordering used by the original collector."""
    pools=pd.read_parquet(ROOT/'data/census/dex_pools.parquet',columns=['dex','pool_address','token0','token1','block','log_index'])
    weth=WETH.lower()
    pools=pools[pools.dex.eq('uniswap_v2') & (pools.token0.eq(weth)|pools.token1.eq(weth))].copy()
    pools['other']=pools.token1.where(pools.token0.eq(weth),pools.token0)
    return pools.sort_values(['block','log_index']).drop_duplicates('other').set_index('other').to_dict('index')

class Collector:
    def __init__(self,logs,archive):
        self.logs,self.archive=logs,archive
        self.calls={}

    async def call(self,to,data,block,evidence):
        key=(to.lower(),data,block)
        if key not in self.calls:
            self.calls[key]=asyncio.create_task(result(self.archive,'eth_call',[{'to':to,'data':data},hex(block)]))
        value,ref=await self.calls[key];evidence.append(ref)
        return words(value)

    async def logs_range(self,address,topics,lo,hi,evidence):
        if lo>hi:return []
        try:
            logs,ref=await result(self.logs,'eth_getLogs',[dict(address=address,topics=topics,fromBlock=hex(lo),toBlock=hex(hi))])
            evidence.append(ref)
            if not isinstance(logs,list):raise ValueError('non-list logs')
            if len(logs)<19990:
                return [l for l in logs if not l.get('removed')]
        except Exception as exc:
            if not any(t in str(exc).lower() for t in ('more than','exceed','too many results','response size','query limit')):raise
        if lo==hi:raise ValueError('log cap at single block')
        mid=(lo+hi)//2
        left=await self.logs_range(address,topics,lo,mid,evidence)
        right=await self.logs_range(address,topics,mid+1,hi,evidence)
        return left+right

    async def convert(self,quote,amount,block,pi,evidence):
        if amount is None:return None
        if int(amount)==0:return '0'
        if quote.lower() in NATIVE:return str(amount)
        refpool=pi.get('quote_conversion_reference_pool')
        if not refpool:
            ref=quote_references().get(quote.lower())
            if not ref or ref['block']>block:raise ValueError('no saved historical WETH conversion reference')
            refpool=ref['pool_address']
        r=await self.call(refpool,'0x0902f1ac',block,evidence)
        token0=await self.call(refpool,'0x0dfe1681',block,evidence)
        if len(r)<2 or not token0:raise ValueError('empty conversion state')
        weth0=token0[0]==int(WETH,16)
        rw,rq=(r[0],r[1]) if weth0 else (r[1],r[0])
        if rw<=0 or rq<=0:raise ValueError('empty historical WETH conversion reserves')
        return str(int(amount)*rw//rq)

    async def concentrated(self,rec,sel,legacy=False):
        sid=int(rec['sample_id']);pool=rec['pool'].lower();kind='v4' if rec['dex']=='uniswap_v4' else 'v3'
        pi=rec.get('pool_info') or {};extra=rec.get('extra') or {}
        created=int(rec.get('created_block') or sel.get('created_block') or 0)
        if not created:raise ValueError('pool creation block absent')
        block=int(rec['block']);first_event=None if legacy else (int(sel['first_liquidity_block']),int(sel['liquidity_log_index']))
        evidence=[]
        address=V4_MANAGER if kind=='v4' else pool
        refs=sel.get('evidence',[]) if not legacy else []
        saved=saved_logs(refs,address,pool if kind=='v4' else None)
        if kind=='v4':
            # Selection queries often contain the entire short window. Reuse
            # their ModifyLiquidity logs only when the filter proves coverage.
            covered=[]
            for ref in refs:
                for env in raw_envelopes(ref['raw'],ref['sha256']):
                    req=env['request'];response=env['response']
                    if req['method']!='eth_getLogs' or 'result' not in response or len(response['result'])>=19990:continue
                    f=req['params'][0];tt=f.get('topics',[])
                    if f.get('address','').lower()==address and len(tt)>1 and tt[0]==TOPICS['uniswap_v4'] and tt[1].lower()==pool:
                        covered.append((int(f['fromBlock'],16),int(f['toBlock'],16)))
            cursor=created;mods=[l for l in saved if created<=log_order(l)[0]<=block and l['topics'][0]==TOPICS['uniswap_v4']]
            for lo,hi in sorted(covered):
                if hi<cursor or lo>block:continue
                if cursor<lo:mods+=await self.logs_range(address,[TOPICS['uniswap_v4'],pool],cursor,min(lo-1,block),evidence)
                cursor=max(cursor,hi+1)
            if cursor<=block:mods+=await self.logs_range(address,[TOPICS['uniswap_v4'],pool],cursor,block,evidence)
            events=mods
            initial=extra.get('sqrt_price_x96') or sel.get('sqrt_price_x96')
            # All swaps are needed to value the first activation if it was
            # delayed beyond Initialize. PoolKey's initial price is evidence
            # from the saved Initialize event, not an inferred tick.
            if not legacy and first_event[0]>created:
                events+=await self.logs_range(address,[V4_SWAP,pool],created,first_event[0],evidence)
            finalprice=pi.get('sqrt_price_x96')
            if not finalprice:
                state=await self.call(V4_STATE,'0x'+keccak(text='getSlot0(bytes32)')[:4].hex()+pool[2:],block,evidence)
                finalprice=state[0]
        else:
            events=await self.logs_range(address,[[V3_INIT,TOPICS['uniswap_v3'],V3_BURN,V3_SWAP]],created,block,evidence)
            initial=finalprice=None
        unique={(l['transactionHash'],l['logIndex']):l for l in events if log_order(l)[0]<=block}
        first,final=reconstruct(list(unique.values()),kind,first_event,initial,finalprice)
        if not legacy and first is None:raise ValueError('selected first event absent from complete log window')
        token0=(pi.get('token0') or extra.get('token0') or sel.get('token0')).lower()
        token1=(extra.get('token1') or sel.get('token1')).lower()
        quote=(pi.get('quote') or (token1 if token0==rec['token'].lower() else token0)).lower()
        q0=quote==token0
        for snap,at in ((first,first_event[0] if first_event else None),(final,block)):
            if snap is None:continue
            snap['reserve_quote']=snap['amount0' if q0 else 'amount1']
            snap['reserve_token']=snap['amount1' if q0 else 'amount0']
            try:snap['quote_eth_equiv_wei']=await self.convert(quote,int(snap['reserve_quote']),at,pi,evidence)
            except Exception as exc:snap.update(quote_eth_equiv_wei=None,conversion_error=repr(exc))
        active_saved=pi.get('liquidity')
        if kind=='v4' and active_saved is not None and int(active_saved)!=int(final['active_liquidity']):
            # Core's zeroForOne crossing sets tick=boundary-1 at an exact
            # sqrt boundary. Price alone cannot disambiguate active L there.
            state=await self.call(V4_STATE,'0x'+keccak(text='getSlot0(bytes32)')[:4].hex()+pool[2:],block,evidence)
            tick=signed(state[1],24)
            active=sum(int(p['liquidity']) for p in final['positions'] if p['tick_lower']<=tick<p['tick_upper'])
            if active!=int(active_saved):
                raise ValueError(f'active L mismatch: events={active}, StateView={active_saved}')
            final.update(active_liquidity=str(active),tick=tick,active_boundary_convention='saved_StateView_tick')
        return dict(sample_id=sid,pool=pool,block=block,kind=kind,status='ok',quote=quote,
                    first=first,final=final,first_reserve_method='position_principal_after_first_positive_event',
                    final_reserve_method='all_position_principal_at_sim_price',
                    reserve_direction=reserve_direction(first.get('reserve_quote') if first else None,
                        first.get('reserve_token') if first else None,final['reserve_quote'],final['reserve_token']),
                    virtual_quote_eth_equiv_wei=pi.get('quote_liquidity_eth_equiv_wei'),
                    evidence=refs+evidence,simulation_evidence=rec.get('evidence'),
                    n_logs=len(unique),history_from_block=created,history_to_block=block,
                    scope='position principal; excludes uncollected fees; includes out-of-range positions')

    async def ordinary(self,rec,sel):
        pi=rec.get('pool_info') or {};dex=sel['dex'];pool=rec['pool'].lower();evidence=list(sel.get('evidence',[]))
        direction_error=None
        logs=saved_logs(evidence,pool)
        first=next((l for l in logs if log_order(l)==(int(sel['first_liquidity_block']),int(sel['liquidity_log_index']))),None)
        if first is None:raise ValueError('first liquidity log missing from saved selection evidence')
        w=words(first['data']);quote=(pi.get('quote') or ZERO).lower()
        if dex=='uniswap_v1':
            t=first['topics'];fq,ft=(int(t[2],16),int(t[3],16)) if len(t)>=4 else w[-2:]
            lq=pi.get('reserve_quote');lt=None
            # Token balance is necessary only to classify an actual depleted
            # pool; the other rows keep an explicit unobserved direction.
            if lq is not None and int(lq)<10**16 and fq>=10**16:
                try:
                    val=await self.call(rec['token'],'0x70a08231'+pool[2:].zfill(64),int(rec['block']),evidence)
                    lt=val[0] if val else None
                    if lt is None:direction_error='token balanceOf returned empty data'
                except Exception as exc:
                    direction_error=repr(exc)
            method='first_AddLiquidity_contributions'
        else:
            q0=bool(pi.get('quote_is_token0')) if pi.get('quote_is_token0') is not None else sel['token0'].lower()==quote
            sync=[l for l in logs if l['topics'][0]==SYNC and l['transactionHash']==first['transactionHash'] and log_order(l)<log_order(first)]
            amounts=words(sync[-1]['data']) if sync else w[:2]
            fq,ft=(amounts[0],amounts[1]) if q0 else (amounts[1],amounts[0])
            lq,lt=pi.get('reserve_quote'),pi.get('reserve_token')
            method='first_Mint_preceding_Sync' if sync else 'first_Mint_contributions'
        try:feth=await self.convert(quote,fq,int(sel['first_liquidity_block']),pi,evidence)
        except Exception as exc:feth=None;conversion_error=repr(exc)
        row=dict(sample_id=int(rec['sample_id']),pool=pool,block=int(rec['block']),kind='v1' if dex=='uniswap_v1' else 'v2',status='ok',quote=quote,
                 first=dict(reserve_quote=str(fq),reserve_token=str(ft),quote_eth_equiv_wei=feth),
                 final=dict(reserve_quote=str(lq) if lq is not None else None,reserve_token=str(lt) if lt is not None else None,
                            quote_eth_equiv_wei=pi.get('quote_liquidity_eth_equiv_wei') if pi.get('quote_liquidity_eth_equiv_wei') is not None else ('0' if lq is not None and int(lq)==0 else None)),
                 first_reserve_method=method,final_reserve_method=pi.get('quote_liquidity_method'),
                 reserve_direction=reserve_direction(fq,ft,lq,lt),evidence=evidence,simulation_evidence=rec.get('evidence'))
        if direction_error:row['final']['token_balance_error']=direction_error
        if feth is None:row['first']['conversion_error']=conversion_error
        return row


async def repair_conversions(collector):
    selections=load_jsonl(OUT/'liquidity_selection.jsonl')
    for legacy in (False,True):
        path=OUT/('liquidity_v21_legacy.jsonl' if legacy else 'liquidity_v21_early.jsonl')
        records=load_jsonl(OUT/('legacy_results.jsonl' if legacy else 'early_results.jsonl'))
        saved=load_jsonl(path);changed=0;resolved=0
        for sid,row in saved.items():
            if row.get('status')!='ok':continue
            refs=[];updated=False
            for name in ('first','final'):
                snap=row.get(name)
                if not snap or snap.get('quote_eth_equiv_wei') is not None or snap.get('reserve_quote') is None:continue
                block=row['block'] if name=='final' else selections[sid]['first_liquidity_block']
                try:value=await collector.convert(row['quote'],int(snap['reserve_quote']),block,records[sid].get('pool_info') or {},refs)
                except Exception:continue
                snap['quote_eth_equiv_wei']=value;snap.pop('conversion_error',None)
                updated=True;resolved+=1
            if updated:
                row['evidence'].extend(refs)
                row['conversion_repaired_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
                append(path,row);changed+=1
        print(json.dumps(dict(legacy=legacy,rows_updated=changed,quote_amounts_resolved=resolved)),flush=True)

async def validate(collector,limit=24):
    saved=load_jsonl(OUT/'liquidity_v21_early.jsonl')
    rows=[v for v in saved.values() if v.get('kind')=='v4' and v.get('status')=='ok']
    # Diverse deterministic sample: native/non-native, floor crossing, active
    # and empty, multiple positions, both creation years.
    rows=sorted(rows,key=lambda r:hashlib.sha256(str(r['sample_id']).encode()).hexdigest())
    rows=list({r['pool']:r for r in reversed(rows)}.values())[::-1]
    selected={}
    for predicate in (
        lambda r:r['quote'] in NATIVE, lambda r:r['quote'] not in NATIVE,
        lambda r:len(r['final']['positions'])>1, lambda r:int(r['final']['active_liquidity'])==0,
        lambda r:r['final'].get('quote_eth_equiv_wei') is not None and int(r['final']['quote_eth_equiv_wei'])>=10**16,
        lambda r:r['final'].get('quote_eth_equiv_wei') is not None and int(r['final']['quote_eth_equiv_wei'])<10**16):
        match=next((r for r in rows if predicate(r)),None)
        if match:selected[match['pool']]=match
    for r in rows:
        if len(selected)>=limit:break
        selected.setdefault(r['pool'],r)
    output=[]
    for row in list(selected.values())[:limit]:
        evidence=[];pool=row['pool'];block=row['block'];snap=row['final'];checks=[]
        state=await collector.call(V4_STATE,'0x'+keccak(text='getSlot0(bytes32)')[:4].hex()+pool[2:],block,evidence)
        active=await collector.call(V4_STATE,'0x'+keccak(text='getLiquidity(bytes32)')[:4].hex()+pool[2:],block,evidence)
        for pos in snap['positions']:
            data='0x'+keccak(text='getPositionInfo(bytes32,address,int24,int24,bytes32)')[:4].hex()+encode(
                ['bytes32','address','int24','int24','bytes32'],
                [bytes.fromhex(pool[2:]),pos['owner'],pos['tick_lower'],pos['tick_upper'],bytes.fromhex(pos['salt'][2:])]).hex()
            val=await collector.call(V4_STATE,data,block,evidence)
            if not val:raise ValueError('StateView empty position response')
            checks.append(dict(**pos,stateview_liquidity=str(val[0]),matches=int(pos['liquidity'])==val[0]))
        quote=row['quote']
        if quote==ZERO.lower():
            balance,ref=await result(collector.archive,'eth_getBalance',[V4_MANAGER,hex(block)]);evidence.append(ref);balance=int(balance,16)
        else:
            balance=(await collector.call(quote,'0x70a08231'+V4_MANAGER[2:].zfill(64),block,evidence))[0]
        record=dict(sample_id=row['sample_id'],pool=pool,block=block,n_positions=len(checks),
                    position_liquidity_matches=all(p['matches'] for p in checks),
                    price_matches=int(snap['sqrt_price_x96'])==state[0],
                    active_liquidity_matches=int(snap['active_liquidity'])==active[0],
                    real_quote_wei=snap['reserve_quote'],manager_shared_balance_wei=str(balance),
                    principal_le_shared_manager_balance=int(snap['reserve_quote'])<=balance,
                    manager_balance_scope='shared singleton balance: sanity bound only, never per-pool validation',
                    positions=checks,evidence=evidence,reconstruction_evidence=row['evidence'])
        output.append(record);append(OUT/'v4_stateview_validation.jsonl',record)
        print('StateView validation',row['sample_id'],all(record[k] for k in ('position_liquidity_matches','price_matches','active_liquidity_matches','principal_le_shared_manager_balance')),flush=True)
    pd.DataFrame([{k:v for k,v in r.items() if k not in ('positions','evidence','reconstruction_evidence')} for r in output]).to_csv(OUT/'v4_stateview_validation.csv',index=False)

async def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode',choices=['early','legacy','validate','conversions'],default='early')
    ap.add_argument('--limit',type=int);ap.add_argument('--retry-errors',action='store_true')
    ap.add_argument('--rps-logs',type=float,default=1.2);ap.add_argument('--rps-archive',type=float,default=1.)
    ap.add_argument('--concurrency',type=int,default=8)
    args=ap.parse_args()
    if not 0<args.rps_logs<=5 or not 0<args.rps_archive<=5:raise ValueError('invalid read-only rate')
    logs=EvidenceEndpoint('https://gateway.tenderly.co/public/mainnet','c26_logs',OUT/'raw',rps=args.rps_logs,max_batch=1,inflight=args.concurrency,max_retries=4)
    archive=EvidenceEndpoint('https://eth.drpc.org','c26_archive',OUT/'raw',rps=args.rps_archive,max_batch=1,inflight=args.concurrency,max_retries=4)
    collector=Collector(logs,archive)
    try:
        if args.mode=='validate':await validate(collector,args.limit or 24);return
        if args.mode=='conversions':await repair_conversions(collector);return
        path=OUT/f'liquidity_v21_{args.mode}.jsonl';done=load_jsonl(path)
        if args.retry_errors:done={k:v for k,v in done.items() if v.get('status')=='ok'}
        records=load_jsonl(OUT/('early_results.jsonl' if args.mode=='early' else 'legacy_results.jsonl'))
        selections=load_jsonl(OUT/'liquidity_selection.jsonl')
        if args.mode=='legacy':
            pools=pd.read_parquet(ROOT/'data/census/dex_pools.parquet').set_index('pool_address')
            for rec in records.values():
                if rec.get('dex')=='uniswap_v4':
                    rec['created_block']=int(pools.loc[rec['pool'].lower(),'block'])
        todo=[r for k,r in records.items() if k not in done and r.get('pool') and (args.mode=='early' or r.get('dex')=='uniswap_v4')]
        todo.sort(key=lambda r:({'uniswap_v4':0,'uniswap_v3':1}.get(r.get('dex'),2),int(r['sample_id'])))
        if args.limit:todo=todo[:args.limit]
        sem=asyncio.Semaphore(args.concurrency)
        ordinary_sem=asyncio.Semaphore(args.concurrency)
        counts=Counter();n=0;t0=time.time()
        async def worker(rec):
            nonlocal n
            # Independent log and saved-reserve work can progress together;
            # both still share the same strictly paced endpoint instances.
            gate=sem if rec.get('dex') in ('uniswap_v3','uniswap_v4') else ordinary_sem
            async with gate:
                try:
                    if rec.get('dex') in ('uniswap_v3','uniswap_v4'):r=await collector.concentrated(rec,selections.get(rec['sample_id'],{}),args.mode=='legacy')
                    else:r=await collector.ordinary(rec,selections[rec['sample_id']])
                except Exception as exc:r=dict(sample_id=rec['sample_id'],pool=rec['pool'],block=rec['block'],status='unprobeable',reason=repr(exc))
                append(path,r);counts[r['status']]+=1;n+=1
                if n%25==0 or n==len(todo):print(json.dumps(dict(done=n,total=len(todo),status=counts,elapsed_s=round(time.time()-t0),logs=logs.stats,archive=archive.stats)),flush=True)
        await asyncio.gather(*(worker(r) for r in todo))
    finally:
        await logs.close();await archive.close()

if __name__=='__main__':asyncio.run(main())
