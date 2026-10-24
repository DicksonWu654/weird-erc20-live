"""Summarize the completed V3 balance census and concrete floor changes."""
import json
import pandas as pd
from dust_rpc import V3,DUST


def main():
    d=pd.read_csv(V3/'v3_dust.csv',dtype={'quote_balance_eth_equiv_wei':str});d['year']=d.analysis_month.str[:4]
    d['balance']=pd.to_numeric(d.quote_balance_eth_equiv_wei,errors='coerce')
    d['measurable']=d.category.isin(['honeypot','high_tax','normal','owner_controlled','rugged'])
    rows=[]
    for year,g in [('all',d),*list(d.groupby('year'))]:
        for domain,x in [('all_rows',g),('measurable_pooled',g[g.measurable])]:
            below=x.balance.lt(10**9);known=x.balance.notna()
            rows.append(dict(year=year,domain=domain,n=len(x),known_n=int(known.sum()),below_1e9_n=int(below.sum()),
                             below_fraction=float(below.sum()/known.sum()) if known.any() else None,
                             below_weight=x.loc[below,'weight'].sum(),total_weight=x.weight.sum(),
                             below_weight_share=x.loc[below,'weight'].sum()/x.weight.sum() if len(x) else None,
                             zero_balance_n=int(x.balance.eq(0).sum()),zero_active_liquidity_n=int(pd.to_numeric(x.active_liquidity,errors='coerce').eq(0).sum())))
    pd.DataFrame(rows).to_csv(V3/'v3_dust_summary.csv',index=False)
    d[d.below_1e9_wei_floor.eq(True)&d.measurable].to_csv(V3/'v3_default_floor_affected_rows.csv',index=False)
    frame=pd.read_parquet(DUST/'inputs/data__tier2__token_results.parquet')
    frame=frame[~((frame.frame_basis=='first_pool_month')&(frame.analysis_month<'2025-02'))]
    excluded=set(d.loc[d.below_1e9_wei_floor.eq(True),'sample_id'])
    impacts=[]
    for year,g in frame[frame.pool_ever&frame.base_category.isin(['normal','owner_controlled','honeypot','high_tax','rugged'])].groupby(frame.analysis_month.str[:4]):
        revised=g[~g.sample_id.isin(excluded)]
        impacts.append(dict(year=year,baseline_n=len(g),retained_n=len(revised),
                            baseline_share=g.loc[g.category.isin(['honeypot','high_tax']),'weight'].sum()/g.weight.sum(),
                            v3_balance_floor_only_share=revised.loc[revised.category.isin(['honeypot','high_tax']),'weight'].sum()/revised.weight.sum(),
                            scope='hypothetical 1e9 V3 balance floor only; other venues unchanged; not a validated replacement estimand'))
    pd.DataFrame(impacts).to_csv(V3/'v3_default_floor_headline_sensitivity.csv',index=False)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__=='__main__':main()
