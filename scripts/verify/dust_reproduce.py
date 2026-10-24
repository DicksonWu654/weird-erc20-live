"""Independent numerical ledger for every quantitative R1[1], [2], [3] claim."""
import json
import numpy as np
import pandas as pd
from dust_rpc import ROOT,DUST


def main():
    d=pd.read_parquet(DUST/'inputs/data__tier2__token_results.parquet')
    d=d[~((d.frame_basis=='first_pool_month')&(d.analysis_month<'2025-02'))].copy()
    d['year']=d.analysis_month.str[:4];rows=[]
    def put(finding,claim,value,scope):rows.append(dict(finding=finding,claim=claim,value=value,scope=scope))
    pooled=d[d.pool_ever];meas=d[d.pool_ever&d.base_category.isin(['honeypot','high_tax','normal','owner_controlled','rugged'])]
    v=d[d.selected_dex.eq('uniswap_v2')]
    for year in ['2025','2026']:
        x=v[v.year.eq(year)];put(1,year+'_v2_n',len(x),'selected V2 creation-year rows')
        put(1,year+'_v2_dust_fraction',x.category.eq('dust_pool').mean(),'unweighted dust/V2')
        put(1,year+'_median_sim_minus_token_creation',(x.sim_block-x.creation_block).median(),'blocks; pool offset is exactly 7200')
    x=v[v.year.eq('2025')];top=x[x.category.eq('dust_pool')].code_hash.value_counts().head(20).index;y=x[~x.code_hash.isin(top)]
    put(1,'2025_dust_after_excluding_top20_dust_hashes',y.category.eq('dust_pool').mean(),'top20 ranked within DUST rows; not all V2 hashes')
    put(1,'2025_distinct_hashes',x.code_hash.nunique(),'all V2 rows before exclusion');put(1,'2025_distinct_senders',x.tx_sender.nunique(),'all V2 rows before exclusion')
    mm=[]
    for column in ['analysis_month','month']:
        for month,g in d[d.analysis_month.ge('2025-01')].groupby(column):
            a=g[g.selected_dex.eq('uniswap_v2')];p=g[g.pool_ever]
            mm.append(dict(period_basis=column,month=month,v2_n=len(a),pooled_n=len(p),dust_n=a.category.eq('dust_pool').sum(),
                           dust_over_v2_n=a.category.eq('dust_pool').mean(),dust_over_all_pooled_n=a.category.eq('dust_pool').sum()/len(p),
                           all_pooled_screen_positive=p.rug_screen_verdict.eq('liquidity_declined_screen_only').mean(),
                           v2_screen_positive=a.rug_screen_verdict.eq('liquidity_declined_screen_only').mean(),
                           median_v2_quote_wei=a.quote_reserve_eth_equiv_wei.median()))
    pd.DataFrame(mm).to_csv(DUST/'review_monthly_denominators.csv',index=False)
    for year in ['2024','2025','2026']:
        x=meas[meas.year.eq(year)]
        for known in [True,False]:
            g=x[x.tradable_reserve_known.eq(known)];put(2,year+('_reserve_known' if known else '_reserve_unknown')+'_harm_share',g.loc[g.category.isin(['honeypot','high_tax']),'weight'].sum()/g.weight.sum(),'design-weighted measurable pooled')
    x=d[d.selected_dex.eq('uniswap_v3')];put(2,'v3_n',len(x),'all years');put(2,'v3_dust_n',x.category.eq('dust_pool').sum(),'all years')
    x=meas[meas.selected_dex.eq('uniswap_v3')&meas.year.eq('2026')]
    put(2,'2026_v3_measurable_n',len(x),'measurable V3');put(2,'2026_v3_owner_controlled',x.category.eq('owner_controlled').sum(),'exclusive production category')
    put(2,'2026_v3_live_eoa_plus_mint',x.sim_flags.str.contains('owner:live_eoa').mul(x.sim_flags.str.contains('fn:mint')).sum(),'flags co-occur; may differ from exclusive category')
    # Reproduce prior validation composition directly from their saved row tables.
    for path,name in [('data/verify/v23/verdicts.csv','C8'),('data/verify/v4/verdicts.csv','C7')]:
        p=ROOT/path
        if p.exists():
            t=pd.read_csv(p);put(2,name+'_rows',len(t),path)
            if name=='C8':
                vv=t[t.dex.eq('uniswap_v3')];put(2,'C8_v3_rows',len(vv),path);put(2,'C8_v3_honeypot_positive',vv.verdict.eq('not_sellable').sum(),path)
            else:put(2,'C7_honeypot_positive',t.verdict.eq('not_sellable').sum(),path)
            (DUST/(name+'_validation_columns.json')).write_text(json.dumps(dict(path=path,columns=t.columns.tolist())))
    for year,g in pooled.groupby('year'):
        dust=g.category.eq('dust_pool');screen=g.rug_screen_verdict.eq('liquidity_declined_screen_only')
        put(3,year+'_dust_share',g.loc[dust,'weight'].sum()/g.weight.sum(),'all pooled design weight')
        put(3,year+'_dust_screen_share',g.loc[dust&screen,'weight'].sum()/g.weight.sum(),'dust and decline SCREEN; not attribution')
    s=d[d.rug_second_pass_sampled];dust=s[s.base_category.eq('dust_pool')]
    put(3,'phase2_checks',len(s),'all checked rows');put(3,'phase2_ineligible_dust_honeypot',s.base_category.isin(['dust_pool','honeypot']).sum(),'all checked rows')
    put(3,'sampled_dust_total',len(dust),'includes one unknown');put(3,'sampled_dust_determinate',dust.liquidity_pulled_observed.notna().sum(),'319 excludes one unknown')
    put(3,'sampled_dust_liquidity_pulled',dust.liquidity_pulled_observed.eq(True).sum(),'observed attribution; not screen')
    eligible=d.rug_screen_verdict.eq('liquidity_declined_screen_only')&~d.category.isin(['dust_pool','honeypot','no_pool'])
    pi={m:min(40,len(g))/len(g) for m,g in d[eligible].groupby('month')}
    d['independent_phase2_weight']=d.weight/d.month.map(pi)
    x=d[d.year.eq('2026')&d.index.isin(meas.index)];s=x[eligible.loc[x.index]&x.rug_second_pass_sampled];D=x.weight.sum()
    for col in ['liquidity_pulled_observed','insider_rug_observed']:
        positive=s[s[col].eq(True)].independent_phase2_weight.sum();unknown=s[s[col].isna()].independent_phase2_weight.sum()
        unsupported=x[eligible.loc[x.index]&~x.month.isin(s.month)].weight.sum()
        upper=positive+unknown+unsupported+x.loc[x.selected_dex.eq('uniswap_v4'),'weight'].sum()
        put(3,'2026_'+col+'_checked',len(s),'eligible measurable checked');put(3,'2026_'+col+'_positive',s[col].eq(True).sum(),'eligible measurable checked')
        put(3,'2026_'+col+'_range_low',positive/D,'direct HT conditional assignment range; not CI or population bounds')
        put(3,'2026_'+col+'_range_high',upper/D,'unknown HT plus unassessed V4 and unsupported frame mass')
    # Every pooled-domain census weight is independently N_h/n_h.
    delta=pooled.weight-pooled.N_stratum/pooled.n_stratum_sampled
    put('D','pooled_weight_N_over_n_max_abs_diff',delta.abs().max(),'same first-phase estimator as C12; original strata preserved')
    tab=pd.read_csv(DUST/'venue_by_year.csv');old=tab[(tab.year==2024)&tab.selected_venue.ne('all')].set_index('selected_venue');new=tab[(tab.year==2026)&tab.selected_venue.ne('all')].set_index('selected_venue')
    standardized=sum(r.venue_share_measurable_weight*new.loc[venue,'honeypot_or_high_tax_share'] for venue,r in old.iterrows())
    put(2,'2026_harm_at_2024_venue_mix',standardized,'descriptive fixed 2024 selected-venue weights; not causal')
    pd.DataFrame(rows).to_csv(DUST/'review_numbers.csv',index=False)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__=='__main__':main()
