"""Read-only reuse of +7200 evidence, with v2 liquidity/classification fields."""
import gzip
import asyncio
import argparse
import json
import sys
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/sim'))
import classify as CL
from abi import enc, WETH, ZERO


def load_results(path):
    out={}
    for line in Path(path).read_text().splitlines():
        r=json.loads(line)
        out[(r['token'].lower(),r['pool'].lower(),int(r['block']))]=r
    return out


def view_results(rec):
    ev=rec.get('evidence') or {}
    path=ROOT/str(ev.get('raw',''))
    if not path.is_file():
        return {}
    with gzip.open(path,'rt') as f:
        envs=json.load(f)
    out={}
    for e in envs:
        req=e.get('request') or {}
        if req.get('method')!='eth_simulateV1':
            continue
        response=(e.get('response') or {}).get('result') or []
        if not isinstance(response,list):
            continue
        # Only the pre-buy metadata in phase 1 is relevant to liquidity.
        if not str(e.get('label','')).startswith('phase1'):
            continue
        blocks=[b for b in response if b.get('calls')]
        for plan,block in zip(req['params'][0]['blockStateCalls'],blocks):
            for call,res in zip(plan['calls'],block['calls']):
                if res.get('status')=='0x1':
                    out.setdefault((call['to'].lower(),call.get('data','0x').lower()),res.get('returnData','0x'))
    return out


def build(write=True):
    import aggregate as A
    old=pd.read_parquet(ROOT/'data/tier2/token_results.parquet')
    results={**load_results(ROOT/'data/tier2/sim-run/results.jsonl'),
             **load_results(ROOT/'data/tier2/v4-results/results.jsonl')}
    c16={}
    v1res=A.v1_reserve_map();prices=A.quote_price_map()
    for p in (ROOT/'data/verify/v3/balance_details').glob('*.json'):
        x=json.loads(p.read_text())
        c16[int(x['sample_id'])]=(x,p)
    rows=[]
    extra={}
    cache=ROOT/'data/tier2/v2/legacy_liquidity.jsonl'
    if cache.exists():
        for line in cache.read_text().splitlines():
            item=json.loads(line);extra[int(item['sample_id'])]=item
    for r in old.itertuples():
        if not isinstance(r.selected_pool,str) or pd.isna(r.sim_block):
            continue
        k=(r.address.lower(),r.selected_pool.lower(),int(r.sim_block))
        rec=results.get(k)
        if not rec:
            continue
        rec=dict(rec);pi=dict(rec.get('pool_info') or {})
        reserve=r.quote_reserve_eth_equiv_wei
        evidence=[]
        # The old parquet's nullable numeric column can round large wei
        # integers. Prefer exact integers from its cited RPC evidence.
        quote=str(pi.get('quote') or '').lower();raw_reserve=pi.get('reserve_quote')
        if r.selected_dex=='uniswap_v1' and k in v1res and v1res[k][0] is not None:
            reserve=int(v1res[k][0]);evidence.append(v1res[k][2])
        elif raw_reserve is not None and quote in (WETH.lower(),ZERO):
            reserve=int(raw_reserve)
        elif raw_reserve is not None and (quote,int(r.sim_block)) in prices:
            weth,q,source=prices[(quote,int(r.sim_block))]
            reserve=int(raw_reserve)*weth//q;evidence.append(source)
        if pd.isna(reserve) and r.selected_dex=='uniswap_v1':
            saved=[s.get('quote') for s in ((rec.get('rug') or {}).get('screen') or {}).get('series',[])
                   if s.get('block')==int(r.sim_block) and s.get('quote') is not None]
            if saved:
                reserve=saved[0]
        if pd.notna(reserve):
            pi['quote_liquidity_eth_equiv_wei']=str(int(reserve))
            pi['quote_liquidity_method']='legacy_v1_v2_saved_eth_equivalent_reserve'
        elif r.selected_dex=='uniswap_v3' and int(r.sample_id) in c16:
            x,p=c16[int(r.sample_id)]
            # Exact token/pool/block match is mandatory before reusing C16.
            if x.get('status')=='ok' and x['selected_pool'].lower()==r.selected_pool.lower() and int(x['sim_block'])==int(r.sim_block):
                val=x.get('quote_balance_eth_equiv_wei')
                if val is not None:
                    pi['quote_liquidity_eth_equiv_wei']=str(val)
                    pi['quote_liquidity_method']='v3_pool_quote_balance_C16'
                    evidence=[str(p.relative_to(ROOT))]+x.get('evidence',[])
        elif r.selected_dex=='uniswap_v4':
            views=view_results(rec)
            pid=bytes.fromhex(r.selected_pool[2:])
            slot=views.get(('0x7ffe42c4a5deea5b0fec41c94c136cf115597227',enc('getSlot0(bytes32)',pid).lower()))
            liq=views.get(('0x7ffe42c4a5deea5b0fec41c94c136cf115597227',enc('getLiquidity(bytes32)',pid).lower()))
            if slot and liq and len(slot)>=66 and len(liq)>=66:
                sqrt,L=int(slot[2:66],16),int(liq[2:66],16)
                quote=pi.get('quote','').lower()
                quote0=pi.get('quote_is_token0')
                if quote in (WETH.lower(),ZERO):
                    val=(L*2**96//sqrt if quote0 else L*sqrt//2**96) if sqrt else 0
                    pi.update(quote_liquidity_eth_equiv_wei=str(val),sqrt_price_x96=str(sqrt),liquidity=str(L),
                              quote_liquidity_method='v4_active_virtual_quote_amount')
                if len(slot)>=258:
                    pi['current_lp_fee']=int(slot[194:258],16)
        cached=extra.get(int(r.sample_id))
        if cached and cached['pool'].lower()==r.selected_pool.lower() and cached['block']==int(r.sim_block):
            pi.update(cached['measurement'])
            evidence.append(cached['evidence'])
        rec.update(sample_id=int(r.sample_id),pool_info=pi,liquidity_reuse_evidence=evidence,
                   sensitivity_scope='legacy_first_quoted_pool_creation_plus7200')
        # Undo only bookkeeping reasons introduced by v1's old floor; raw
        # buys/sells are kept. A lack of trade data remains unprobeable.
        if rec.get('unprobeable')=='dust_pool':
            rec['unprobeable']=None
        res={**rec,'sells':rec.get('sells') or []}
        flags,label,reason,taxes=CL.classify(res,rec.get('control'),None)
        rec.update(flags=flags,primary_label=label,label_reason=reason,taxes=taxes,rules=CL.RULES)
        rows.append(rec)
    if write:
        dest=ROOT/'data/tier2/v2/legacy_results.jsonl'
        with dest.open('w') as f:
            for r in rows:
                f.write(json.dumps(r,separators=(',',':'))+'\n')
    return {r['sample_id']:r for r in rows}


async def fill_missing(rows,retry_errors=False):
    from c17_rpc import EvidenceEndpoint
    import run as SR
    ctx=SR.Ctx()
    ctx.archive=EvidenceEndpoint('https://eth.drpc.org','legacy',ROOT/'data/raw/sim/c17-legacy-rpc',
                                 rps=.5,max_batch=3,inflight=4,timeout=90,max_retries=4)
    pools=pd.read_parquet(ROOT/'data/census/dex_pools.parquet')
    weth=WETH.lower()
    refs=pools[pools.dex.eq('uniswap_v2')&(pools.token0.eq(weth)|pools.token1.eq(weth))].copy()
    refs['other']=refs.token1.where(refs.token0.eq(weth),refs.token0)
    ctx.quote_refs=refs.sort_values(['block','log_index']).drop_duplicates('other').set_index('other').to_dict('index')
    todo=[r for r in rows.values() if r['pool_info'].get('quote_liquidity_eth_equiv_wei') is None and
          (retry_errors or r['pool_info'].get('quote_liquidity_status')!='unprobeable')]
    sem=asyncio.Semaphore(4);n=0
    async def one(rec):
        nonlocal n
        async with sem:
            rl=SR.PL.RawLog();pi=rec['pool_info']
            pair=dict(dex=rec.get('dex'),extra=dict(token0=pi.get('token0'),token1=pi.get('token1')))
            measured=await SR.PL.E.measure_quote_liquidity(ctx,rl,rec['token'],rec['pool'],rec['block'],pair,pi)
            path=ROOT/'data/raw/sim/c17-legacy'/f'{rec["sample_id"]}.json.gz'
            sha=rl.dump(str(path)) if rl.items else None
            saved=dict(sample_id=rec['sample_id'],pool=rec['pool'],block=rec['block'],measurement=measured,
                       evidence=dict(raw=str(path.relative_to(ROOT)),sha256=sha) if sha else None)
            with (ROOT/'data/tier2/v2/legacy_liquidity.jsonl').open('a') as f:
                f.write(json.dumps(saved,separators=(',',':'))+'\n')
            n+=1
            if n%50==0 or n==len(todo):print('legacy liquidity',n,'/',len(todo),ctx.archive.stats,flush=True)
    try:await asyncio.gather(*(one(r) for r in todo))
    finally:await ctx.archive.close()


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--fetch-missing',action='store_true')
    ap.add_argument('--retry-errors',action='store_true');args=ap.parse_args()
    x=build()
    if args.fetch_missing:
        asyncio.run(fill_missing(x,args.retry_errors));x=build()
    print('legacy results',len(x),'measured',sum('quote_liquidity_eth_equiv_wei' in r['pool_info'] for r in x.values()))
