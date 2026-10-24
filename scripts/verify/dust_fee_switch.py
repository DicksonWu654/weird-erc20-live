"""Pin V2 feeTo activation and inspect LP ownership behind residual reserves."""
import concurrent.futures as cf
import hashlib
import json
import math

import pandas as pd
from eth_abi import decode
from eth_utils import keccak
from dust_rpc import ROOT,DUST,WETH,ZERO,enc,rpc,sim,call,uint

FACTORY='0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f'
TRANSFER='0x'+keccak(text='Transfer(address,address,uint256)').hex()
BURN='0x'+keccak(text='Burn(address,uint256,uint256,address)').hex()


def fee_at(b):
    r,p=rpc('eth_call',[dict(to=FACTORY,data=enc('feeTo()')),hex(b)])
    return '0x'+r[-40:],p


def main():
    lo,hi=24100000,24200000;evidence=[]
    while hi-lo>1:
        mid=(lo+hi)//2;fee,p=fee_at(mid);evidence.append(p)
        if fee==ZERO:lo=mid
        else:hi=mid
    fee,p=fee_at(hi);evidence.append(p)
    prev,p=fee_at(hi-1);evidence.append(p)
    block,p=rpc('eth_getBlockByNumber',[hex(hi),False]);evidence.append(p)
    d=dict(activation_block=hi,activation_timestamp=int(block['timestamp'],16),fee_to=fee,previous_fee_to=prev,evidence=evidence)
    (DUST/'fee_activation.json').write_text(json.dumps(d,indent=2));print(d,flush=True)
    sample=pd.read_csv(DUST/'reserve_sample.csv',dtype={'saved_raw_quote_wei':str}).to_dict('records')
    details=DUST/'fee_details';details.mkdir(exist_ok=True)
    def one(row):
        dest=details/(str(row['sample_id'])+'.json')
        if dest.exists():return
        pool=row['selected_pool'];b=int(row['sim_block'])
        calls=[call(pool,enc('totalSupply()')),call(pool,enc('balanceOf(address)',['address'],[fee])),
               call(pool,enc('balanceOf(address)',['address'],[ZERO])),call(pool,enc('kLast()')),
               call(pool,'0x0902f1ac'),call(pool,enc('MINIMUM_LIQUIDITY()'))]
        rc,p=sim(calls,b,DUST/'raw_fee')
        supply,held,locked,k,minimum=uint(rc[0]),uint(rc[1]),uint(rc[2]),uint(rc[3]),uint(rc[5])
        out=dict(sample_id=row['sample_id'],month=row['analysis_month'],category=row['category'],pool=pool,block=b,
                 reserve_quote_raw=row['saved_raw_quote_wei'],lp_total_supply=str(supply),fee_recipient_lp=str(held),
                 permanently_locked_lp=str(locked),minimum_liquidity=minimum,k_last=str(k),
                 fee_plus_locked_equals_supply=supply==held+locked if None not in [supply,held,locked] else None,
                 fee_recipient_lp_fraction=held/supply if supply else None,evidence=[p])
        dest.write_text(json.dumps(out,indent=2))
    with cf.ThreadPoolExecutor(4) as ex:list(ex.map(one,sample))
    rows=[json.loads(p.read_text()) for p in details.glob('*.json')]
    pd.DataFrame(rows).to_csv(DUST/'fee_residuals.csv',index=False)
    # Independently fetch burn receipts for 5 saved attributed removals each side.
    frame=pd.read_parquet(DUST/'inputs/data__tier2__token_results.parquet')
    out=[]
    for year in ['2025','2026']:
        x=frame[frame.selected_dex.eq('uniswap_v2')&frame.analysis_month.str.startswith(year)&frame.rug_detail_evidence.notna()]
        n=0
        for row in x.to_dict('records'):
            source=ROOT/row['rug_detail_evidence'];detail=json.loads(source.read_text());burns=(detail.get('detail') or {}).get('burns',[])
            burns=[b for b in burns if b['block']<=row['sim_block']]
            if not burns:continue
            burn=burns[0];receipt,p=rpc('eth_getTransactionReceipt',[burn['tx']],DUST/'raw_fee')
            pool=row['selected_pool'];transfers=[]
            for l in receipt['logs']:
                if l['address'].lower()==pool.lower() and l['topics'][0]==TRANSFER:
                    transfers.append(dict(sender='0x'+l['topics'][1][-40:],recipient='0x'+l['topics'][2][-40:],amount=str(int(l['data'],16))))
            rc,p2=sim([call(pool,enc('totalSupply()')),call(pool,enc('balanceOf(address)',['address'],[fee])),
                       call(pool,enc('balanceOf(address)',['address'],[ZERO])),call(pool,'0x0902f1ac')],burn['block'],DUST/'raw_fee')
            n+=1;out.append(dict(sample_id=row['sample_id'],year=year,pool=pool,burn_block=burn['block'],tx=burn['tx'],
                                fee_minted=sum(int(t['amount']) for t in transfers if t['sender']==ZERO and t['recipient']==fee),
                                lp_supply_after=str(uint(rc[0])),fee_lp_after=str(uint(rc[1])),locked_lp=str(uint(rc[2])),
                                reserve0_after=str(uint(rc[3])),reserve1_after=str(uint(rc[3],1)),
                                lp_transfer_events=json.dumps(transfers),source_evidence=row['rug_detail_evidence'],
                                source_evidence_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),evidence=json.dumps([p,p2])))
            print('burn',year,n,row['sample_id'],out[-1]['fee_minted'],flush=True)
            if n==5:break
    pd.DataFrame(out).to_csv(DUST/'fee_burn_receipts.csv',index=False)


if __name__=='__main__':main()
