"""Describe threshold eligibility effects without changing Tier-2 data."""
import json
import numpy as np
import pandas as pd
from dust_rpc import ROOT,DUST,V3


def main():
    d=pd.read_parquet(DUST/'inputs/data__tier2__token_results.parquet')
    d=d[~((d.frame_basis=='first_pool_month')&(d.analysis_month<'2025-02'))].copy();d['year']=d.analysis_month.str[:4]
    d['audit_quote_wei']=d.quote_reserve_eth_equiv_wei
    vp=V3/'v3_dust.csv'
    if vp.exists():
        v=pd.read_csv(vp,dtype={'quote_balance_eth_equiv_wei':str}).set_index('sample_id')
        vals=pd.to_numeric(v.quote_balance_eth_equiv_wei,errors='coerce')
        idx=d.selected_dex.eq('uniswap_v3');d.loc[idx,'audit_quote_wei']=d.loc[idx,'sample_id'].map(vals)
    measurable=d.pool_ever&d.base_category.isin(['honeypot','high_tax','normal','owner_controlled','rugged'])
    rows=[]
    for (year,venue),g in d[d.pool_ever].groupby(['year','selected_dex']):
        for floor in [10**9,10**12,10**14,10**16,10**17]:
            known=g.audit_quote_wei.notna();below=known&g.audit_quote_wei.lt(floor)
            m=g[measurable.loc[g.index]&~below]
            rows.append(dict(year=year,venue=venue,floor_wei=str(floor),n_pooled=len(g),reserve_known_n=int(known.sum()),
                             reserve_below_n=int(below.sum()),reserve_below_weight=g.loc[below,'weight'].sum(),
                             newly_excluded_measurable_n=int((below&measurable.loc[g.index]).sum()),
                             retained_measurable_n=len(m),retained_measurable_weight=m.weight.sum(),
                             conditional_harm_share=m.loc[m.category.isin(['honeypot','high_tax']),'weight'].sum()/m.weight.sum() if len(m) else None,
                             scope='known quote only; unavailable reserve retained as unknown; declared sensitivity, not corrected publication'))
    pd.DataFrame(rows).to_csv(DUST/'floor_sensitivity_by_venue_year.csv',index=False)
    newfloor=10**16
    affected=d[d.audit_quote_wei.notna()&d.audit_quote_wei.lt(newfloor)&~d.category.eq('dust_pool')].copy()
    affected['suggested_category']='dust_pool';affected['policy']='proposed uniform 0.01 ETH balance/reserve floor; policy change, not proven corrupt records'
    affected['was_measurable']=measurable.loc[affected.index]
    affected[['sample_id','address','analysis_month','selected_dex','selected_pool','sim_block','category','audit_quote_wei','weight','was_measurable','suggested_category','policy','sim_evidence']].to_csv(DUST/'proposed_floor_affected_rows.csv',index=False)
    unknown=d[d.pool_ever&d.audit_quote_wei.isna()].copy()
    unknown['was_measurable']=measurable.loc[unknown.index]
    unknown['policy']='liquidity unassessed; cannot assert that floor is passed'
    unknown[['sample_id','address','analysis_month','selected_dex','selected_pool','sim_block','category','weight','was_measurable','policy','sim_evidence']].to_csv(DUST/'liquidity_unknown_rows.csv',index=False)
    # A directly comparable V2-only series at the proposed floor.
    a=pd.DataFrame(rows);a[a.venue.eq('uniswap_v2')].to_csv(DUST/'v2_only_floor_series.csv',index=False)
    # Zoom at the known protocol activation, using pool creation to avoid crossing-day ambiguity.
    activation=json.loads((DUST/'fee_activation.json').read_text())['activation_block']
    x=d[d.selected_dex.eq('uniswap_v2')&d.first_quoted_pool_block.between(activation-30000,activation+30000)].copy()
    x['side']=np.where(x.first_quoted_pool_block<activation,'before','after')
    summary=x.groupby('side').agg(n=('sample_id','size'),dust_n=('category',lambda s:s.eq('dust_pool').sum()),
                                  dust_fraction=('category',lambda s:s.eq('dust_pool').mean()),median_quote=('audit_quote_wei','median'))
    summary.to_csv(DUST/'activation_window.csv')
    try:
        import matplotlib;matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,ax=plt.subplots(figsize=(9,4.5));ax.scatter(x.first_quoted_pool_block,x.audit_quote_wei.clip(lower=1),s=18,alpha=.65)
        ax.axvline(activation,color='#a33',linestyle='--',label='feeTo activation, 2025-12-27')
        ax.axhline(10**9,color='#555',linestyle=':',label='default dust floor')
        ax.axhline(10**16,color='#148',linestyle=':',label='0.01 ETH sensitivity')
        ax.set(yscale='log',xlabel='Pool creation block',ylabel='Quote reserve at pool creation +7,200 blocks (wei)',title='Uniswap V2 residual reserves across protocol-fee activation')
        ax.legend(fontsize=8);fig.text(.5,.005,'Each point is one selected V2 pool; zero reserves are plotted at 1 wei.',ha='center',fontsize=8)
        fig.tight_layout(rect=[0,.025,1,1]);fig.savefig(DUST/'fee_boundary.png',dpi=160);plt.close(fig)
    except ImportError:pass
    print('proposed floor affects',len(affected),'rows, measurable',int(affected.was_measurable.sum()))
    print(affected.groupby(['year','selected_dex']).size().to_string());print(summary.to_string())


if __name__=='__main__':main()
