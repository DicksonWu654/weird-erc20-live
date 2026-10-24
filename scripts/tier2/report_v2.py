#!/usr/bin/env python3
"""Saved, reproducible old/new headline comparison and descriptive timing join."""
import hashlib
import json
import numpy as np
import pandas as pd
from early_liquidity import ROOT,OUT,load_jsonl


def run():
    manifest=json.loads((OUT/'build_manifest.json').read_text())
    if not manifest['complete']:raise SystemExit('report requires complete v2 build')
    oldpath=ROOT/'data/tier2/_pre_c15/annual_shares.csv'
    newpath=OUT/'annual_shares.csv'
    old=pd.read_csv(oldpath);new=pd.read_csv(newpath)
    keep=['headline_excl_lp','measurable_pooled']
    old=old[old.label.eq('headline_trade_harm')&old.denominator.isin(keep)]
    new=new[new.label.eq('headline_trade_harm')&new.denominator.isin(keep)]
    columns=['year','denominator','share','ci_low','ci_high','effective_n','period_coverage']
    comparison=old[columns].merge(new[columns],on=['year','denominator'],suffixes=('_old','_new'),validate='one_to_one')
    comparison['change_percentage_points']=100*(comparison.share_new-comparison.share_old)
    comparison['old_definition']='pool-creation+7200; technical floor; successfully classified domain'
    comparison['new_definition']='first positive liquidity+300; 180-day pool window; >=0.01 ETH liquidity domain'
    comparison.to_csv(OUT/'headline_old_vs_new.csv',index=False)
    d=pd.read_parquet(OUT/'token_results.parquet')
    d=d[~((d.source=='pool_linked')&(d.analysis_month<'2025-02'))]
    legacy=load_jsonl(OUT/'legacy_results.jsonl')
    rows=[]
    for r in d[d.pool_ever].itertuples():
        oldrec=legacy.get(int(r.sample_id))
        pi=(oldrec or {}).get('pool_info') or {}
        q=pi.get('quote_liquidity_eth_equiv_wei')
        same=bool(oldrec and isinstance(r.selected_pool,str) and oldrec['pool'].lower()==r.selected_pool.lower())
        later=bool(same and pd.notna(r.sim_block) and oldrec['block']>r.sim_block)
        rows.append(dict(sample_id=r.sample_id,analysis_month=r.analysis_month,weight=r.weight,
                         pool_matches=same,legacy_state_is_later=later,
                         new_block=r.sim_block,legacy_block=(oldrec or {}).get('block'),
                         first_liquidity_block=r.first_liquidity_block,new_category=r.category,
                         new_harm=bool(r.headline_trade_harm),legacy_category=(oldrec or {}).get('primary_label'),
                         legacy_liquidity_known=q is not None,
                         legacy_below_0_01_eth=(int(q)<10**16) if q is not None else None,
                         new_evidence=r.sim_evidence,legacy_evidence=json.dumps((oldrec or {}).get('evidence')),
                         legacy_measurement_evidence=json.dumps((oldrec or {}).get('liquidity_reuse_evidence'))))
    transitions=pd.DataFrame(rows)
    transitions.to_parquet(OUT/'offset_transitions.parquet',index=False)
    aggregates=[]
    for year,g in transitions.groupby(transitions.analysis_month.str[:4]):
        comparable=g[g.legacy_state_is_later & g.legacy_liquidity_known]
        harmed=comparable[comparable.new_harm]
        depleted=harmed[harmed.legacy_below_0_01_eth.eq(True)]
        W=float(harmed.weight.sum())
        aggregates.append(dict(year=year,n_pooled=len(g),n_same_pool=int(g.pool_matches.sum()),
                               n_comparable_known_later_state=len(comparable),
                               comparable_weight=float(comparable.weight.sum()),
                               n_early_harm=len(harmed),early_harm_weight=W,
                               n_early_harm_later_below_floor=len(depleted),
                               early_harm_later_below_floor_weight=float(depleted.weight.sum()),
                               share_early_harm_later_below_floor=float(depleted.weight.sum())/W if W else np.nan,
                               scope='descriptive matched-pool subset; no rug attribution; not a population CI'))
    pd.DataFrame(aggregates).to_csv(OUT/'offset_transition_by_year.csv',index=False)
    lines=['| Year | Old all-new | New all-new | Old measurable | New ≥0.01 ETH |',
           '| --- | ---: | ---: | ---: | ---: |']
    def fmt(v):return 'NA' if pd.isna(v) else f'{100*v:.2f}%'
    for year,g in comparison.groupby('year'):
        a=g[g.denominator.eq('headline_excl_lp')].iloc[0]
        m=g[g.denominator.eq('measurable_pooled')].iloc[0]
        lines.append(f'| {year} | {fmt(a.share_old)} | {fmt(a.share_new)} | {fmt(m.share_old)} | {fmt(m.share_new)} |')
    (OUT/'headline_table.txt').write_text('\n'.join(lines)+'\n')
    report=dict(old_source=str(oldpath.relative_to(ROOT)),new_source=str(newpath.relative_to(ROOT)),
                old_sha256=hashlib.sha256(oldpath.read_bytes()).hexdigest(),
                new_sha256=hashlib.sha256(newpath.read_bytes()).hexdigest(),
                comparison_rows=len(comparison),timing_join_rows=len(transitions),
                timing_scope='same pool, legacy block strictly later, both observations retained; no pure offset claim')
    (OUT/'report_manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print('\n'.join(lines));print(pd.DataFrame(aggregates).to_string(index=False))


if __name__=='__main__':run()
