#!/usr/bin/env python3
"""Select launch pools from positive liquidity events; stream resumable +300 runs.

Only writes C17-owned paths. Existing v1 simulation files are never changed.
"""
import argparse
import asyncio
import bisect
import gzip
import hashlib
import json
import sys
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/tier2/v2'
sys.path.insert(0, str(ROOT / 'scripts/sim'))
import run as SR
from c17_rpc import EvidenceEndpoint, result

DAY180 = 180 * 86400
V4 = '0x000000000004444c5dc75cb358380d2e3de08a90'
TOPICS = {d: '0x' + keccak(text=s).hex() for d, s in {
    'uniswap_v1': 'AddLiquidity(address,uint256,uint256)',
    'uniswap_v2': 'Mint(address,uint256,uint256)',
    'sushiswap_v2': 'Mint(address,uint256,uint256)',
    'uniswap_v3': 'Mint(address,address,int24,int24,uint128,uint256,uint256)',
    'uniswap_v4': 'ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)',
}.items()}
BM = pd.read_csv(ROOT / 'data/census/blockmap.csv')
LAST = BM.last_block.to_numpy()
TS = BM.ts_first.to_numpy()
ENDS = np.r_[TS[1:], int(pd.Timestamp('2026-10-01', tz='UTC').timestamp())]
POST_MAP_TIMESTAMP = None


def load_jsonl(path, key='sample_id'):
    ans = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                ans[int(r[key])] = r
    return ans


def append(path, row):
    with Path(path).open('a') as f:
        f.write(json.dumps(row, separators=(',', ':')) + '\n')


def month_bounds(block):
    i = bisect.bisect_left(LAST, int(block))
    if i >= len(BM):
        if POST_MAP_TIMESTAMP is not None:
            return int(ENDS[-1]), int(POST_MAP_TIMESTAMP)+1
        raise ValueError('block outside saved month map')
    return int(TS[i]), int(ENDS[i])


def search_upper(creation,head):
    deadline=month_bounds(creation)[1]+DAY180
    if deadline>=ENDS[-1]:return int(head)
    ix=bisect.bisect_right(TS,deadline)-1
    return min(int(head),int(LAST[min(ix,len(LAST)-1)]))


def positive(dex, log):
    if log.get('removed'):
        return False
    data = log.get('data', '0x')[2:]
    w = [int(data[i:i+64], 16) for i in range(0, len(data), 64)]
    if dex == 'uniswap_v4':
        return len(w) >= 3 and 0 < w[2] < 2**255
    if dex == 'uniswap_v3':
        return len(w) >= 4 and w[1] > 0 and (w[2] > 0 or w[3] > 0)
    if dex == 'uniswap_v1':
        # Vyper V1 indexes the provider and both contributed amounts.
        t = log.get('topics', [])
        return (len(t) >= 4 and int(t[2], 16) > 0 and int(t[3], 16) > 0) or (len(w) >= 2 and min(w[-2:]) > 0)
    return len(w) >= 2 and min(w[:2]) > 0


class Selector:
    def __init__(self, ep, head, receipt_ep=None):
        self.ep, self.head, self.receipt_ep = ep, head, receipt_ep
        self.headers = {}

    async def timestamp(self, block):
        if block not in self.headers:
            val, ev = await result(self.ep, 'eth_getBlockByNumber', [hex(int(block)), False])
            if not val:
                raise ValueError('missing block header')
            self.headers[block] = int(val['timestamp'], 16), ev
        return self.headers[block]

    async def eligible(self, creation, event_block):
        if event_block < creation:
            return False, 'liquidity_predates_recorded_token_creation', []
        cl, cu = month_bounds(creation)
        el, eu = month_bounds(event_block)
        if eu - cl <= DAY180:
            return True, 'saved_month_timestamp_bounds', []
        if el - cu >= DAY180:
            return False, 'saved_month_timestamp_bounds', []
        (ct, ce), (et, ee) = await asyncio.gather(self.timestamp(creation), self.timestamp(event_block))
        return 0 <= et-ct <= DAY180, 'exact_block_timestamps', [ce, ee]

    async def logs(self, p, lo, hi, evidence):
        filt = dict(address=V4 if p['dex'] == 'uniswap_v4' else p['pool_address'],
                    topics=[TOPICS[p['dex']]] + ([p['pool_address']] if p['dex'] == 'uniswap_v4' else []),
                    fromBlock=hex(lo), toBlock=hex(hi))
        try:
            val, ev = await result(self.ep, 'eth_getLogs', [filt])
        except Exception as exc:
            msg=str(exc).lower()
            if hi>lo and any(x in msg for x in ('more than','exceed','too many results','response size','query limit')):
                mid=(lo+hi)//2
                a=await self.logs(p,lo,mid,evidence)
                return a if a else await self.logs(p,mid+1,hi,evidence)
            raise
        evidence.append(ev)
        if not isinstance(val, list):
            raise ValueError('non-list log response')
        if len(val) >= 19990:
            if lo == hi:
                raise ValueError('log result cap at a single block')
            mid = (lo+hi)//2
            a = await self.logs(p, lo, mid, evidence)
            return a if a else await self.logs(p, mid+1, hi, evidence)
        return sorted((l for l in val if positive(p['dex'], l)),
                      key=lambda l: (int(l['blockNumber'],16), int(l['logIndex'],16)))

    async def select(self, row, pools):
        sid, creation = int(row['sample_id']), int(row['creation_block'])
        out = dict(sample_id=sid, token=row['address'], status='unprobeable',
                   unprobeable_reason=None, evidence=[], candidates_checked=0,
                   first_pool_block=int(row['first_pool_block']) if pd.notna(row['first_pool_block']) else None)
        upper=search_upper(creation,self.head)
        out['search_to_block']=upper
        best = None
        try:
            for p in pools:
                start = int(p['block'])
                if start > upper or (best and start > best['first_liquidity_block']):
                    break
                out['candidates_checked'] += 1
                # Most launches mint atomically. Expanding windows avoid huge
                # histories while proving that the returned event is first.
                lo = start
                limit = min(upper, best['first_liquidity_block'] if best else upper)
                found = []
                # A positive Mint in the pool-creation transaction proves
                # activation in the earliest possible block. Modern pools
                # commonly activate atomically; avoid an unnecessary range
                # query while preserving the exact event/receipt evidence.
                if self.receipt_ep is not None and p.get('tx_hash'):
                    try:
                        receipt,ev=await result(self.receipt_ep,'eth_getTransactionReceipt',[p['tx_hash']])
                        out['evidence'].append(ev)
                        if receipt and int(receipt['blockNumber'],16)==start:
                            addr=V4 if p['dex']=='uniswap_v4' else p['pool_address'].lower()
                            found=[l for l in receipt.get('logs',[]) if l.get('address','').lower()==addr
                                   and l.get('topics',[None])[0]==TOPICS[p['dex']]
                                   and (p['dex']!='uniswap_v4' or l['topics'][1].lower()==p['pool_address'].lower())
                                   and positive(p['dex'],l)]
                            found.sort(key=lambda l:int(l['logIndex'],16))
                    except Exception:
                        # Failed receipt retrieval is not a failed token;
                        # the complete log query can still establish first.
                        found=[]
                for endpoint in sorted(set([min(start+99,limit),min(start+7199,limit),limit])):
                    if found:
                        break
                    if lo > endpoint:
                        continue
                    found = await self.logs(p, lo, endpoint, out['evidence'])
                    if found:
                        break
                    lo = endpoint + 1
                if not found:
                    continue
                log = found[0]
                block = int(log['blockNumber'], 16)
                ok, proof, ev = await self.eligible(creation, block)
                out['evidence'] += ev
                if not ok:
                    continue
                candidate = {k: (None if pd.isna(v) else v) for k,v in p.items()}
                candidate.update(first_liquidity_block=block, liquidity_tx=log['transactionHash'],
                                 liquidity_log_index=int(log['logIndex'],16), eligibility_proof=proof)
                if best is None or (block,candidate['liquidity_log_index'],candidate['pool_address']) < (
                    best['first_liquidity_block'],best['liquidity_log_index'],best['pool_address']):
                    best = candidate
            if best:
                out.update(best, status='selected', unprobeable_reason=None,
                           pool=best['pool_address'], created_block=int(best['block']),
                           block=best['first_liquidity_block']+300, offset_blocks=300)
                if out['block'] > self.head:
                    out.update(status='unprobeable', unprobeable_reason='launch_offset_after_observation_head')
            else:
                out['unprobeable_reason'] = ('pool_precedes_recorded_token_creation' if not pools and
                                             out.get('first_pool_block') is not None and out['first_pool_block']<creation else
                                             'late_pool_no_pool_created_within_180_days' if not pools
                                             else 'no_positive_liquidity_within_180_days')
        except Exception as exc:
            # An unchecked earlier candidate can invalidate an otherwise
            # observed later pool: do not silently choose that later pool.
            out['unprobeable_reason'] = f'liquidity_selection_error:{exc!r}'[:1000]
        return out


def prepare(head=None):
    frame = pd.read_parquet(ROOT / 'data/census/sample_frame.parquet').reset_index(drop=True)
    frame.insert(0,'sample_id',np.arange(len(frame)))
    frame = frame[frame.pool_ever].copy()
    pools = pd.read_parquet(ROOT / 'data/census/dex_pools.parquet')
    # Each sampled token has its own deadline. This also avoids expanding all
    # of WETH's hundreds of thousands of pools, most created years too late.
    f = frame[['sample_id','address','creation_block']].copy()
    f['upper'] = [search_upper(b,int(head) if head is not None else int(LAST[-1])) for b in f.creation_block]
    pieces = []
    for column in ('token0','token1'):
        q = pools.merge(f, left_on=column, right_on='address', how='inner')
        q = q[(q.block >= q.creation_block)&(q.block <= q.upper)]
        pieces.append(q.drop(columns=['address','creation_block','upper']))
    candidates = pd.concat(pieces).drop_duplicates(['sample_id','dex','pool_address']).sort_values(
        ['sample_id','block','log_index','pool_address'])
    columns = list(pools.columns)
    mapping = defaultdict(list)
    for rec in candidates[['sample_id']+columns].to_dict('records'):
        sid=int(rec.pop('sample_id'))
        mapping[sid].append(rec)
    return frame, mapping


async def main():
    global POST_MAP_TIMESTAMP
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--limit',type=int)
    ap.add_argument('--concurrency',type=int,default=8)
    ap.add_argument('--rps-archive',type=float,default=1.5)
    ap.add_argument('--rps-logs',type=float,default=2.)
    ap.add_argument('--retry-errors',action='store_true')
    ap.add_argument('--selection-only',action='store_true')
    ap.add_argument('--repair-evidence',action='store_true',help='rerun latest records whose saved pair evidence hash no longer matches')
    args = ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    logs = EvidenceEndpoint('https://gateway.tenderly.co/public/mainnet','logs',ROOT/'data/raw/sim/c17-rpc',
                            rps=args.rps_logs,max_batch=3,inflight=args.concurrency,timeout=90,max_retries=4)
    archive = EvidenceEndpoint('https://eth.drpc.org','archive',ROOT/'data/raw/sim/c17-rpc',
                               rps=args.rps_archive,max_batch=3,inflight=args.concurrency,timeout=90,max_retries=4)
    # The creation cohorts end in September; observation does not. Retain the
    # original historical run's saved head so October activations are eligible
    # under the same frozen observation window as the legacy sensitivity.
    legacy_meta=json.loads((ROOT/'data/tier2/sim-run/run_meta.json').read_text().splitlines()[0])
    head=int(legacy_meta['head_pinned'])
    header,head_evidence=await result(archive,'eth_getBlockByNumber',[hex(head),False])
    if not header:raise ValueError('saved observation head unavailable')
    POST_MAP_TIMESTAMP=int(header['timestamp'],16)
    frame,pools=prepare(head)
    if args.limit:
        ids=np.linspace(0,len(frame)-1,args.limit,dtype=int)
        frame=frame.iloc[ids]
    selector = Selector(logs,head,archive)
    done = load_jsonl(OUT/'liquidity_selection.jsonl')
    simdone = load_jsonl(OUT/'early_results.jsonl')
    # Earlier checkpoints used the month-map endpoint. Only unresolved late
    # windows need reopening; an established earlier activation stays first.
    for row in frame.to_dict('records'):
        sid=int(row['sample_id']);saved=done.get(sid)
        if saved and saved['status']!='selected' and search_upper(row['creation_block'],head)>int(saved.get('search_to_block',LAST[-1])):
            done.pop(sid);simdone.pop(sid,None)
    if args.retry_errors:
        done = {k:v for k,v in done.items() if 'error:' not in str(v.get('unprobeable_reason'))}
        simdone = {k:v for k,v in simdone.items() if not SR.needs_redo(v) and k in done and
                   (v.get('pool_info') or {}).get('quote_liquidity_status')!='unprobeable'}
    if args.repair_evidence:
        repairs=[]
        for sid,rec in list(simdone.items()):
            ev=rec.get('evidence')
            if not ev:continue
            try:
                body=gzip.open(ROOT/ev['raw'],'rb').read()
                valid=hashlib.sha256(body).hexdigest()==ev['sha256']
            except (OSError,ValueError):valid=False
            if not valid:
                repairs.append(sid);simdone.pop(sid,None)
        append(OUT/'evidence_repairs.jsonl',dict(at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),sample_ids=repairs))
        print('evidence hash repairs',repairs,flush=True)
    ctx = SR.Ctx()
    ctx.mode,ctx.offset,ctx.head_pinned='historical',300,head
    ctx.dust_floor_wei=10**16
    ctx.run='c17-early';ctx.raw_dir='data/raw/sim/c17-early'
    ctx.amts=[10**16,5*10**14];ctx.rug=False;ctx.etherscan=None
    ctx.archive=archive;ctx.fresh=archive;ctx.sim_endpoints=[archive]
    ctx.measure_liquidity=True
    ctx.unique_raw=True
    pp=pd.read_parquet(ROOT/'data/census/dex_pools.parquet')
    weth=SR.WETH.lower()
    pp=pp[pp.dex.eq('uniswap_v2') & (pp.token0.eq(weth)|pp.token1.eq(weth))].copy()
    pp['other']=np.where(pp.token0.eq(weth),pp.token1,pp.token0)
    ctx.quote_refs=pp.sort_values(['block','log_index']).drop_duplicates('other').set_index('other').to_dict('index')
    sem=asyncio.Semaphore(args.concurrency)
    counts=Counter();t0=time.time();n=0
    async def worker(row):
        nonlocal n
        sid=int(row['sample_id'])
        async with sem:
            chosen=done.get(sid)
            if chosen is None:
                chosen=await selector.select(row,pools.get(sid,[]))
                append(OUT/'liquidity_selection.jsonl',chosen)
            if not args.selection_only and sid not in simdone:
                if chosen['status']=='selected':
                    extra={k:v for k,v in chosen.items() if k not in ('token','pool','dex','block','created_block')}
                    pair=dict(token=SR.to_checksum_address(chosen['token']),pool=chosen['pool'],dex=chosen['dex'],
                              block=chosen['block'],created_block=chosen['created_block'],extra=extra)
                    rec=await SR.process(ctx,pair)
                    rec['sample_id']=sid
                else:
                    rec=dict(sample_id=sid,token=row['address'],pool=None,block=None,mode='historical',
                             offset_blocks=300,primary_label='unprobeable',
                             unprobeable=chosen['unprobeable_reason'],label_reason=chosen['unprobeable_reason'],
                             selection_evidence=chosen['evidence'],rules=SR.CL.RULES)
                append(OUT/'early_results.jsonl',rec)
                counts[rec['primary_label']]+=1
            n+=1
            if n%25==0 or n==len(frame):
                print(json.dumps(dict(done=n,total=len(frame),elapsed_s=round(time.time()-t0),
                                      labels=counts,archive=archive.stats,logs=logs.stats)),flush=True)
    meta=dict(args=vars(args),started_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
              observation_head=head,n_pooled=len(frame),selection_rule='earliest_positive_liquidity_within_180_days',
              initial_probe_wei=10**16,offset_blocks=300)
    meta.update(observation_timestamp=POST_MAP_TIMESTAMP,observation_head_evidence=head_evidence)
    meta['git_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    sources=[ROOT/'scripts/sim'/f'{p}.py' for p in ('run','engine','pipeline','v4','classify','rpc','control','abi')]
    sources += [Path(__file__),ROOT/'scripts/tier2/c17_rpc.py',ROOT/'data/census/sample_frame.parquet',
                ROOT/'data/census/dex_pools.parquet',ROOT/'data/census/blockmap.csv']
    meta['source_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    append(OUT/'early_run_meta.jsonl',meta)
    try:
        await asyncio.gather(*(worker(r) for r in frame.to_dict('records')))
    finally:
        await logs.close();await archive.close()


if __name__=='__main__':
    asyncio.run(main())
