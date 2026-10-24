"""C26 aggregate-only old-v2/new-v2.1 comparison and review extracts."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/tier2/v2'


def run():
    old=OUT/'_pre_c26/annual_shares.csv';new=OUT/'annual_shares.csv'
    keys=['year','denominator','label']
    columns=keys+['share','ci_low','ci_high','est_count','n_sampled']
    comparison=pd.read_csv(old)[columns].merge(pd.read_csv(new)[columns],on=keys,suffixes=('_pre_c26','_v21'),validate='one_to_one')
    comparison['change_percentage_points']=100*(comparison.share_v21-comparison.share_pre_c26)
    comparison.to_csv(OUT/'c26_old_vs_new.csv',index=False)
    d=pd.read_parquet(OUT/'token_results.parquet');before=pd.read_parquet(OUT/'_pre_c26/token_results.parquet')
    assert d.sample_id.tolist()==before.sample_id.tolist()
    assert np.array_equal(d.weight.to_numpy(),before.weight.to_numpy())
    scope=~(d.source.eq('pool_linked')&d.analysis_month.lt('2025-02'))
    v4=d[scope & d.venue.eq('V4')]
    cross=[]
    for year,g in v4.groupby(v4.analysis_month.str[:4]):
        previous=before.loc[g.index]
        for name,condition in [
            ('virtual_above_real_below',previous.measurable & g.liquidity_known & ~g.measurable),
            ('virtual_below_real_above',~previous.measurable & g.measurable),
            ('real_liquidity_unknown',~g.liquidity_known),
            ('all_selected_V4',pd.Series(True,index=g.index))]:
            cut=g[condition]
            cross.append(dict(year=year,transition=name,n_sampled=len(cut),weight=float(cut.weight.sum()),
                              all_selected_V4_weight=float(g.weight.sum()),
                              share_selected_V4_weight=float(cut.weight.sum()/g.weight.sum()) if g.weight.sum() else np.nan))
    pd.DataFrame(cross).to_csv(OUT/'v4_real_virtual_transitions.csv',index=False)
    blind_path=ROOT/'data/verify/blind300/comparisons.csv'
    independent=[]
    if blind_path.exists():
        blind=pd.read_csv(blind_path)
        pairs=blind[blind.venue.eq('V4')][['sample_id','real_quote_eth']].merge(
            d[['sample_id','analysis_month','quote_liquidity_eth','liquidity_known']],on='sample_id',validate='one_to_one')
        for year,g in pairs.groupby(pairs.analysis_month.str[:4]):
            known=g[g.liquidity_known & g.real_quote_eth.notna()]
            mismatch=known.quote_liquidity_eth.ge(.01).ne(known.real_quote_eth.ge(.01))
            independent.append(dict(year=year,n_blind_V4=len(g),n_both_measured=len(known),
                                    n_floor_disagreements=int(mismatch.sum()),
                                    max_absolute_ETH_difference=float((known.quote_liquidity_eth-known.real_quote_eth).abs().max()),
                                    scope='independent Decimal position reconstruction versus exact integer TickMath; ETH conversion/rounding may differ'))
        pd.DataFrame(independent).to_csv(OUT/'v4_independent_floor_check.csv',index=False)
    summary=dict(n_rows=len(d),n_pooled=int(d.pool_ever.sum()),n_selected=int(d.selection_status.eq('selected').sum()),
                 rows_and_weights_unchanged=True,n_category_changed=int(d.category.ne(before.category).sum()),
                 n_first_liquidity_known=int(d.first_liquidity_known.sum()),
                 first_reserve_methods=d.first_reserve_method.value_counts().to_dict(),
                 reserve_measurement_status=d.reserve_measurement_status.value_counts().to_dict(),
                 comparison_sha256=hashlib.sha256((OUT/'c26_old_vs_new.csv').read_bytes()).hexdigest(),
                 old_source=str(old.relative_to(ROOT)),old_sha256=hashlib.sha256(old.read_bytes()).hexdigest(),
                 new_source=str(new.relative_to(ROOT)),new_sha256=hashlib.sha256(new.read_bytes()).hexdigest())
    if independent:
        summary.update(independent_V4_floor_checks=sum(r['n_both_measured'] for r in independent),
                       independent_V4_floor_disagreements=sum(r['n_floor_disagreements'] for r in independent),
                       blind300_comparisons_sha256=hashlib.sha256(blind_path.read_bytes()).hexdigest())
    (OUT/'c26_report_manifest.json').write_text(json.dumps(summary,indent=2)+'\n')
    select=comparison[comparison.label.eq('headline_trade_harm')&comparison.denominator.eq('measurable_pooled')]
    print(select[['year','share_pre_c26','share_v21','ci_low_v21','ci_high_v21','change_percentage_points']].to_string(index=False))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':run()
