"""C16 historical V2 reserve rereads and complete V3 quote-balance audit."""
import argparse
import concurrent.futures as cf
import json
import traceback

import pandas as pd
from eth_abi import decode
from dust_rpc import ROOT, DUST, V3, WETH, ZERO, enc, rpc, call, sim, uint


def header(b):
    r,p=rpc('eth_getBlockByNumber',[hex(b),False])
    return int(r['timestamp'],16),p


def day_block(b):
    """First canonical block at or after creation header timestamp +86400."""
    ts,p=header(b); target=ts+86400; lo=b; hi=b+7500; evidence=[p]
    th,p=header(hi); evidence.append(p)
    while th<target:
        hi+=7500; th,p=header(hi); evidence.append(p)
    tl=ts
    while hi-lo>1:
        mid=max(lo+1,min(hi-1,lo+(target-tl)*(hi-lo)//max(1,th-tl)))
        tm,p=header(mid);evidence.append(p)
        if tm<target:lo,tl=mid,tm
        else:hi,th=mid,tm
    return hi,evidence


def v2_one(row):
    out={k:row[k] for k in ['sample_id','address','selected_pool','analysis_month','category','sim_block','creation_block','first_quoted_pool_block','saved_raw_quote_wei']}
    out['collector_version']=2
    evidence=[]; pool=row['selected_pool']; block=int(row['sim_block'])
    def equiv(amount,quote,b):
        if quote==WETH:return amount
        conversion=row.get('reserve_conversion_evidence')
        if not conversion:return None
        ref=json.loads((ROOT/conversion).read_text())
        raw,p=rpc('eth_call',[dict(to=ref['reference_pool'],data='0x0902f1ac'),hex(b)])
        evidence.append(p)
        rr=decode(['uint112','uint112','uint32'],bytes.fromhex(raw[2:]))
        wr,qr=(rr[0],rr[1]) if ref['reference_token0'].lower()==WETH else (rr[1],rr[0])
        return amount*wr//qr if qr else None
    for name,b in [('sim',block),('pool_day',int(row['first_quoted_pool_block'])+7200),('token_day',int(row['creation_block'])+7200)]:
        # Direct eth_call at the exact parent block; no state overrides or router.
        raw,p=rpc('eth_call',[dict(to=pool,data='0x0902f1ac'),hex(b)])
        evidence.append(p)
        if raw=='0x':
            out[name+'_block']=b;out[name+'_status']='no_pool_at_block';continue
        r0,r1,last=decode(['uint112','uint112','uint32'],bytes.fromhex(raw[2:]))
        meta,p=sim([call(pool,'0x0dfe1681'),call(pool,'0xd21220a7'),
                    call(WETH,enc('balanceOf(address)',['address'],[pool]))],b,DUST/'raw')
        evidence.append(p)
        t0='0x'+f'{uint(meta[0]):040x}'; t1='0x'+f'{uint(meta[1]):040x}'
        quote=t1 if t0==row['address'].lower() else t0
        rq=r1 if t0==row['address'].lower() else r0
        eth=equiv(rq,quote,b)
        out.update({name+'_block':b,name+'_quote_token':quote,name+'_reserve_raw':str(rq),
                    name+'_weth_balance':str(uint(meta[2])),name+'_token0':t0,name+'_token1':t1,
                    name+'_reserve_timestamp':last,name+'_eth_equiv_wei':str(eth) if eth is not None else None,
                    name+'_dust':eth<10**9 if eth is not None else None})
        if name=='sim':
            out['saved_reserve_exact_match']=str(rq)==str(row['saved_raw_quote_wei'])
            out['recorded_label_correct']=((row['category']=='dust_pool')==(eth<10**9)) if eth is not None else None
    for name,created in [('pool_exact_day',int(row['first_quoted_pool_block'])),('token_exact_day',int(row['creation_block']))]:
        b,ev=day_block(created);evidence+=ev
        raw,p=rpc('eth_call',[dict(to=pool,data='0x0902f1ac'),hex(b)]);evidence.append(p)
        if raw=='0x':
            out[name+'_block']=b;out[name+'_status']='no_pool_at_block';continue
        rr=decode(['uint112','uint112','uint32'],bytes.fromhex(raw[2:]))
        rq=rr[1] if out['sim_token0']==row['address'].lower() else rr[0]
        eth=equiv(rq,out['sim_quote_token'],b)
        out.update({name+'_block':b,name+'_reserve_raw':str(rq),name+'_eth_equiv_wei':str(eth) if eth is not None else None,
                    name+'_dust':eth<10**9 if eth is not None else None})
    out['evidence']=evidence
    return out


def refs():
    p=pd.read_parquet(ROOT/'data/census/dex_pools.parquet')
    p=p[p.dex.eq('uniswap_v2')&((p.token0==WETH)|(p.token1==WETH))].sort_values(['block','log_index'])
    p['other']=p.token0.where(p.token0.ne(WETH),p.token1)
    return p.drop_duplicates('other').set_index('other').to_dict('index')


def v3_one(row,reference):
    quote=row['first_quoted_pool_quote_token'];pool=row['selected_pool'];b=int(row['sim_block'])
    out={k:row[k] for k in ['sample_id','address','analysis_month','selected_pool','sim_block','category','weight']}
    out['quote_token']=quote
    c=[call(quote,enc('balanceOf(address)',['address'],[pool])),call(pool,'0x0dfe1681'),
       call(pool,'0xd21220a7'),call(pool,enc('slot0()')),call(pool,enc('liquidity()'))]
    ref=reference.get(quote)
    if quote!=WETH and ref and ref['block']<=b:c.append(call(ref['pool_address'],'0x0902f1ac'))
    rc,path=sim(c,b,V3/'raw_balance')
    bal=uint(rc[0]);liq=uint(rc[4]);sqrt=uint(rc[3]);eth=None
    if quote==WETH:eth=bal
    elif bal is not None and len(rc)>5:
        r0,r1=uint(rc[5]),uint(rc[5],1)
        wr,qr=(r0,r1) if ref['token0']==WETH else (r1,r0)
        if wr and qr:eth=bal*wr//qr
    t0='0x'+f'{uint(rc[1]):040x}' if uint(rc[1]) is not None else None
    t1='0x'+f'{uint(rc[2]):040x}' if uint(rc[2]) is not None else None
    virt=None
    if sqrt and liq is not None:
        virt=liq*(2**96)//sqrt if quote==t0 else liq*sqrt//(2**96)
    out.update(quote_balance_raw=str(bal) if bal is not None else None,
               quote_balance_eth_equiv_wei=str(eth) if eth is not None else None,
               active_liquidity=str(liq),sqrt_price_x96=str(sqrt),virtual_active_quote_raw=str(virt),
               below_1e9_wei_floor=eth<10**9 if eth is not None else None,
               status='ok' if eth is not None else 'unprobeable_quote_conversion_or_balance',
               token0=t0,token1=t1,evidence=[path],
               reference_pool=ref['pool_address'] if quote!=WETH and ref else None,
               measurement='quote-token pool balance; out-of-range/fees included; no invented V4 singleton reserve')
    return out


def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['v2','v3']);ap.add_argument('--limit',type=int);a=ap.parse_args()
    base=DUST if a.mode=='v2' else V3;details=base/('reserve_details' if a.mode=='v2' else 'balance_details');details.mkdir(exist_ok=True)
    rows=pd.read_csv(DUST/'reserve_sample.csv' if a.mode=='v2' else V3/'v3_rows.csv',keep_default_na=False,dtype={'saved_raw_quote_wei':str}).to_dict('records')
    reference=refs() if a.mode=='v3' else None
    if a.limit:rows=rows[:a.limit]
    def one(row):
        dest=details/(str(row['sample_id'])+'.json')
        if dest.exists():
            old=json.loads(dest.read_text())
            if 'error' not in old and (a.mode=='v3' or old.get('collector_version')==2):return
        try:out=v2_one(row) if a.mode=='v2' else v3_one(row,reference)
        except Exception as exc:
            traceback.print_exc();out=dict(sample_id=row['sample_id'],error=repr(exc),status='unprobeable')
        dest.write_text(json.dumps(out,indent=2))
    with cf.ThreadPoolExecutor(5) as ex:
        for i,_ in enumerate(ex.map(one,rows)):
            if i%20==0:print(a.mode,i+1,'/',len(rows),flush=True)
    out=[json.loads(p.read_text()) for p in sorted(details.glob('*.json'))]
    pd.DataFrame(out).to_csv(base/('reserve_checks.csv' if a.mode=='v2' else 'v3_dust.csv'),index=False)
    print('DONE',a.mode,len(out),flush=True)


if __name__=='__main__':main()
