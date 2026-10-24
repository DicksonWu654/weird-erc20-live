"""Check that low V3 pool balance need not imply a failed funded round trip."""
import json
import pandas as pd
from dust_rpc import V3,ROOT,DUST,WETH,enc,rpc
from v3_blind import verify


def main():
    frame=pd.read_parquet(DUST/'inputs/data__tier2__token_results.parquet').set_index('sample_id');out=[]
    for sid in [41148,31306,9575]:
        r=frame.loc[sid]
        row=dict(sample_id=sid,token=r.address,pool=r.selected_pool,block=int(r.sim_block),pool_created_block=int(r.first_quoted_pool_block),dex='uniswap_v3')
        raw,p=rpc('eth_call',[dict(to=WETH,data=enc('balanceOf(address)',['address'],[r.selected_pool])),hex(row['block'])],V3/'raw_balance')
        d=verify(row);d.update(direct_quote_balance=str(int(raw,16)),direct_balance_evidence=p,production_label=r.category)
        dest=V3/'balance_spots';dest.mkdir(exist_ok=True);(dest/(str(sid)+'.json')).write_text(json.dumps(d,indent=2))
        out.append(dict(sample_id=sid,direct_quote_balance=d['direct_quote_balance'],independent_label=d['verdict'],evidence=str((dest/(str(sid)+'.json')).relative_to(ROOT))))
        print(out[-1],flush=True)
    pd.DataFrame(out).to_csv(V3/'balance_spots.csv',index=False)


if __name__=='__main__':main()
