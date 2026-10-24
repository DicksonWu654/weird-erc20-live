"""Verify fee mints against the canonical V2 integer _mintFee formula."""
import json
import math
import pandas as pd
from dust_rpc import DUST,enc,sim,call,uint


def main():
    rows=pd.read_csv(DUST/'fee_burn_receipts.csv',dtype={'fee_minted':str,'lp_supply_after':str,'fee_lp_after':str,'locked_lp':str}).to_dict('records');out=[]
    for row in rows:
        pool=row['pool'];b=int(row['burn_block'])
        rc,p=sim([call(pool,enc('totalSupply()')),call(pool,enc('kLast()')),call(pool,'0x0902f1ac')],b-1,DUST/'raw_fee')
        supply,k,r0,r1=uint(rc[0]),uint(rc[1]),uint(rc[2]),uint(rc[2],1)
        root=math.isqrt(r0*r1);last=math.isqrt(k)
        predicted=supply*(root-last)//(root*5+last) if k and root>last else 0
        out.append(dict(sample_id=row['sample_id'],year=row['year'],burn_block=b,supply_before=str(supply),
                        k_last_before=str(k),reserve0_before=str(r0),reserve1_before=str(r1),
                        predicted_fee_lp=str(predicted),observed_fee_lp=row['fee_minted'],formula_matches=str(predicted)==row['fee_minted'],
                        post_supply_equals_fee_plus_locked=int(row['lp_supply_after'])==int(row['fee_lp_after'])+int(row['locked_lp']),
                        evidence=p,receipt_tx=row['tx'],
                        caveat='parent-block prediction can differ if another swap occurs earlier in the burn block'))
    pd.DataFrame(out).to_csv(DUST/'fee_mint_formula.csv',index=False)
    print(pd.DataFrame(out)[['sample_id','year','predicted_fee_lp','observed_fee_lp','formula_matches','post_supply_equals_fee_plus_locked']].to_string(index=False))


if __name__=='__main__':main()
