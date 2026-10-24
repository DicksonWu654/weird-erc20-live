#!/usr/bin/env python3
"""Era/venue blind checks and explicit transferability bounds for v2 headlines.

The blind checks observe +7200, use adaptive probe sizes, and oversample old
predicted labels. Raw recall is sample-conditional. It is not an identified
population sensitivity, so it must not be inserted into Rogan--Gladen as if
it were one. Report bounds and the required transport assumption instead.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from reestimate_v2 import ROOT,OUT,HARM,classify_record,measured_record,liquidity_measurements
from early_liquidity import load_jsonl
from reaggregate_revision import wilson

POOLED_ASSUMPTION=('pooled raw precision/NPV transfer across years, venues and predicted categories including owner_controlled; '
                   'C27 same-block exits transfer to production next/delayed-block sells; legacy +7200 adaptive probes '
                   'transfer to +300 fixed initial 0.01 ETH; caller/route and tax-boundary policy differences ignored')


def era(month):
    y=int(str(month)[:4])
    return '2017-19' if y<=2019 else '2020-22' if y<=2022 else '2023-24' if y<=2024 else '2025-26'


def blind_rows(directory=OUT):
    old=pd.read_parquet(ROOT/'data/tier2/token_results.parquet')
    mapping={(r.address.lower(),str(r.selected_pool).lower(),int(r.sim_block)):r for r in old.itertuples()
             if pd.notna(r.sim_block) and isinstance(r.selected_pool,str)}
    legacy=load_jsonl(OUT/'legacy_results.jsonl')
    rows=[]
    for name,file in [('C8',ROOT/'data/verify/v23/verdicts.csv'),('C7',ROOT/'data/verify/v4/verdicts.csv'),
                      ('C16',ROOT/'data/verify/v3/blind_verdicts.csv')]:
        if not file.exists():continue
        for b in pd.read_csv(file).to_dict('records'):
            k=(b['token'].lower(),b['pool'].lower(),int(b['block']))
            oldrow=mapping.get(k)
            if oldrow is None:continue
            rec=legacy.get(int(oldrow.sample_id))
            if not rec:continue
            rec=measured_record(rec,liquidity_measurements(True).get(int(oldrow.sample_id)))
            label,reason,flags,taxes=classify_record(rec)
            pi=rec.get('pool_info') or {}
            q=pi.get('quote_liquidity_eth_equiv_wei')
            measured=q is not None and int(q)>=10**16
            truth=b.get('independent_label',b.get('verdict'))
            bt=b.get('buy_tax');st=b.get('sell_tax')
            tax=max(float(bt) if pd.notna(bt) else 0.,float(st) if pd.notna(st) else 0.)
            # C7/C8 taxes are percent; C16 CSV uses fractions.
            tax=tax if name=='C16' else tax/100
            truth_harm=(truth in ('honeypot','not_sellable','high_tax') or tax>=.1-1e-8)
            decidable=truth in ('honeypot','not_sellable','high_tax','sellable','normal')
            rows.append(dict(set=name,sample_id=int(oldrow.sample_id),token=b['token'],pool=b['pool'],block=b['block'],
                             analysis_month=oldrow.analysis_month,era=era(oldrow.analysis_month),
                             venue='V4' if len(b['pool'])==66 else 'V3' if oldrow.selected_dex=='uniswap_v3' else 'V1/V2',
                             prediction=label,predicted_harm=label in HARM,truth=truth,truth_harm=truth_harm,
                             decidable=decidable,measurable=measured,evidence_path=b.get('evidence_path'),
                             original_selection_stratum=b.get('stratum','legacy_label_stratified'),
                             limitation='different observation block/probe; adaptive truth omits transfer-only tax'))
    # C27's summary is the publication gate: do not inspect sealed labels or
    # comparisons while independent verdict collection is still blind.
    summary=ROOT/'data/verify/blind300/summary.csv'
    if summary.exists():
        new_summary=pd.read_csv(summary)
        if not {'year','venue','domain','tp','fp','fn','tn'}<=set(new_summary.columns):
            raise ValueError('unknown C27 summary schema; refusing to guess')
        report=summary.with_name('report_manifest.json')
        if report.exists():
            expected=json.loads(report.read_text())['summary_sha256']
            if hashlib.sha256(summary.read_bytes()).hexdigest()!=expected:raise ValueError('C27 summary hash changed')
        new_summary.to_csv(Path(directory)/'blind300_summary_used.csv',index=False)
        comparisons=summary.with_name('comparisons.csv')
        if not comparisons.exists():raise ValueError('C27 summary present without comparisons.csv')
        current=pd.read_parquet(Path(directory)/'token_results.parquet').set_index('sample_id')
        for b in pd.read_csv(comparisons).to_dict('records'):
            sid=int(b['sample_id']);r=current.loc[sid]
            prediction=r.category
            truth=b['verdict']
            rows.append(dict(set='C27',sample_id=sid,token=b['token'],pool=b['pool'],block=b['block'],
                             analysis_month=r.analysis_month,era=era(r.analysis_month),venue=r.venue,
                             prediction=prediction,predicted_harm=prediction in HARM,
                             truth=truth,truth_harm=truth in HARM,
                             decidable=truth in ('honeypot','high_tax','sellable'),
                             measurable=bool(r.measurable),evidence_path=b.get('evidence_path'),
                             original_selection_stratum=f'{b["year"]}/{b["venue"]}/{b.get("predicted", "frozen_prediction")}',
                             frozen_prediction=b.get('predicted'),
                             limitation='fixed +300 same-block blind replay versus production next/delayed sells; pre-C26 binary-label stratification; current real-floor domain; owner transport and pooled error homogeneity assumed'))
    return pd.DataFrame(rows)


def run(directory=OUT):
    directory=Path(directory)
    blind=blind_rows(directory)
    blind.to_csv(directory/'blind_comparisons.csv',index=False)
    metrics=[];error_bounds={}
    for (e,v),g in blind.groupby(['era','venue']):
        good=g[g.decidable & g.measurable & g.prediction.isin(['honeypot','high_tax','normal','owner_controlled'])].copy()
        eligible_n=len(good)
        good['token_key']=good.token.str.lower();good['pool_key']=good.pool.str.lower()
        # Different blind harnesses testing the same state are one sampled
        # token-state unit, not additional independent validation support.
        good=good.drop_duplicates(['token_key','pool_key','block'],keep='last')
        tp=int((good.predicted_harm & good.truth_harm).sum())
        fp=int((good.predicted_harm & ~good.truth_harm).sum())
        fn=int((~good.predicted_harm & good.truth_harm).sum())
        tn=int((~good.predicted_harm & ~good.truth_harm).sum())
        for metric,a,n in [('precision',tp,tp+fp),('recall_sample_conditional',tp,tp+fn),
                           ('negative_predictive_value',tn,tn+fn),('specificity_sample_conditional',tn,tn+fp)]:
            point=a/n if n else np.nan
            lo,hi=wilson(point,n) if n else (np.nan,np.nan)
            metrics.append(dict(era=e,venue=v,task='headline_trade_harm',metric=metric,n_success=a,n_trials=n,estimate=point,ci_low=lo,ci_high=hi,
                                tp=tp,fp=fp,fn=fn,tn=tn,n_blind=len(g),n_jointly_measurable_decidable=len(good),
                                n_unknown_or_outside_floor=len(g)-eligible_n,
                                n_duplicate_state_units_excluded=eligible_n-len(good),
                                population_recall_identified=False,
                                reason='label-stratified sample; owner/era gaps; no validated transport to +300'))
        for label,lg in good.groupby('prediction'):
            n=len(lg);a=int(lg.truth_harm.sum())
            p=a/n;lo,hi=wilson(p,n)
            # Small cells remain descriptive; n>=5 permits a transparent
            # exploratory transfer of the full Wilson envelope, not a point.
            error_bounds[(e,v,label)]=(lo,hi,n) if n>=5 else (0.,1.,n)
    pd.DataFrame(metrics).to_csv(directory/'blind_metrics.csv',index=False)
    good=blind[blind.decidable & blind.measurable & blind.prediction.isin(['honeypot','high_tax','normal','owner_controlled'])].copy()
    good['token_key']=good.token.str.lower();good['pool_key']=good.pool.str.lower()
    good=good.drop_duplicates(['token_key','pool_key','block'],keep='last')
    pooled_metrics=[]
    for name,pred,truth in [('precision',True,True),('negative_predictive_value',False,False)]:
        cut=good[good.predicted_harm.eq(pred)];n=len(cut);success=int(cut.truth_harm.eq(truth).sum())
        p=success/n if n else np.nan;lo,hi=wilson(p,n) if n else (np.nan,np.nan)
        pooled_metrics.append(dict(metric=name,n_success=success,n_trials=n,estimate=p,ci_low=lo,ci_high=hi,
                                  blind_sets=';'.join(sorted(good.set.unique())),
                                  assumption=POOLED_ASSUMPTION,
                                  population_recall_identified=False,scope='pooled-assumption scenario; not population accuracy or a classification-error CI'))
    pooled=pd.DataFrame(pooled_metrics);pooled.to_csv(directory/'pooled_blind_metrics.csv',index=False)
    pm=pooled.set_index('metric')
    d=pd.read_parquet(directory/'token_results.parquet')
    annual=pd.read_csv(directory/'annual_shares.csv')
    d=d[~((d.source=='pool_linked')&(d.analysis_month<'2025-02'))].copy()
    d['era']=d.analysis_month.map(era)
    adjusted=[];coverage=[];pooled_ranges=[]
    any_counts=blind.groupby(['era','venue','prediction']).size().to_dict()
    decidable_counts=good.groupby(['era','venue','prediction']).size().to_dict()
    year_counts=blind.assign(year=blind.analysis_month.str[:4]).groupby(['year','venue','prediction']).size().to_dict()
    for year,year_group in d.groupby(d.analysis_month.str[:4]):
        g=year_group[year_group.measurable]
        mass=float(g.weight.sum());any_mass=joint_mass=sameyear_mass=0.;cells=[]
        for (e,v,label),cell in g.groupby(['era','venue','category']):
            w=float(cell.weight.sum());n_any=any_counts.get((e,v,label),0);n_good=decidable_counts.get((e,v,label),0)
            n_year=year_counts.get((year,v,label),0)
            if n_any:any_mass+=w
            if n_good:joint_mass+=w
            if n_year:sameyear_mass+=w
            cells.append(dict(era=e,venue=v,prediction=label,weight=w,n_any_blind=n_any,n_jointly_decidable=n_good,n_same_year=n_year))
        coverage.append(dict(year=year,n_measurable_sampled=len(g),domain_weight=mass,
                             weight_share_cells_with_any_blind_check=any_mass/mass if mass else np.nan,
                             weight_share_cells_without_any_blind_check=1-any_mass/mass if mass else np.nan,
                             weight_share_cells_with_jointly_decidable_check=joint_mass/mass if mass else np.nan,
                             weight_share_cells_with_same_year_blind_check=sameyear_mass/mass if mass else np.nan,
                             cell_definition='creation era x venue x current predicted category; any check includes unresolved/outside-floor truth',
                             same_year_cell_definition='creation year x venue x current predicted category',
                             cells_json=json.dumps(cells,sort_keys=True),blind_sets=';'.join(sorted(blind.set.unique()))))
    for r in annual[annual.label.eq('headline_trade_harm')&annual.denominator.isin(['headline_excl_lp','measurable_pooled'])].itertuples():
        g=d[d.analysis_month.str[:4].eq(str(r.year))]
        if r.denominator=='headline_excl_lp':g=g[g.analysis_month.ne('2025-02')]
        else:g=g[g.measurable]
        D=float(g.weight.sum());low=high=unsupported=0.;missing=[]
        for (e,v,label),cell in g.groupby(['era','venue','category']):
            w=float(cell.weight.sum())
            if label in ('no_pool','no_meaningful_liquidity','late_pool'):
                a=b=0.
            else:
                a,b,n=error_bounds.get((e,v,label),(0.,1.,0))
                if n<5:
                    unsupported+=w;missing.append(f'{e}/{v}/{label}:n={n}')
            low+=w*a;high+=w*b
        supported=not missing and np.isfinite(r.share) and D>0
        adjusted.append(dict(year=r.year,denominator=r.denominator,observed_share=r.share,
                             sampling_ci_low=r.ci_low,sampling_ci_high=r.ci_high,
                             adjusted_low=low/D if D and np.isfinite(r.share) else np.nan,
                             adjusted_high=high/D if D and np.isfinite(r.share) else np.nan,
                             adjustment='conditional_precision_NPV_envelope_bounds',
                             strict_identified_low=0. if D and np.isfinite(r.share) else np.nan,
                             strict_identified_high=(float(g.loc[~g.category.isin(['no_pool','no_meaningful_liquidity','late_pool']),'weight'].sum())/D
                                                     if D and np.isfinite(r.share) else np.nan),
                             population_misclassification_identified=False,
                             conditional_all_cells_supported=supported,
                             unsupported_weight_share=unsupported/D if D else np.nan,
                             unsupported_cells=';'.join(missing),
                             required_assumption='transport +7200 adaptive-probe label-specific error envelopes to +300 0.01-ETH launch probe',
                             reason='Rogan-Gladen unavailable: population sensitivity/specificity not identified; ranges are not 95% CIs'))
        pos=float(g.loc[g.headline_trade_harm,'weight'].sum())/D if D else np.nan
        neg=float(g.loc[g.category.isin(['normal','owner_controlled']),'weight'].sum())/D if D else np.nan
        unknown=float(g.loc[g.category.isin(['could_not_buy','unprobeable','v3_fot_incompatible','late_pool']),'weight'].sum())/D if D else np.nan
        valid=D>0 and np.isfinite(r.share) and pm.n_trials.gt(0).all()
        low=pos*float(pm.loc['precision','ci_low']) if valid else np.nan
        high=min(1.,pos+neg*(1-float(pm.loc['negative_predictive_value','ci_low']))) if valid else np.nan
        pooled_ranges.append(dict(year=r.year,denominator=r.denominator,observed_share=r.share,
                                  pooled_assumption_low=low,pooled_assumption_high=high,
                                  all_unresolved_upper=min(1.,high+unknown) if valid else np.nan,
                                  negative_prediction_weight_share=neg,unresolved_weight_share=unknown,
                                  precision_lower=pm.loc['precision','ci_low'],npv_lower=pm.loc['negative_predictive_value','ci_low'],
                                  precision_success=pm.loc['precision','n_success'],precision_trials=pm.loc['precision','n_trials'],
                                  npv_success=pm.loc['negative_predictive_value','n_success'],npv_trials=pm.loc['negative_predictive_value','n_trials'],
                                  blind_sets=';'.join(sorted(good.set.unique())),
                                  classification_error_identified=False,sampling_uncertainty_included=False,
                                  assumption=POOLED_ASSUMPTION,
                                  scope='classification scenario, not a confidence interval; unobserved outcomes have separate upper bound'))
    pd.DataFrame(adjusted).to_csv(directory/'misclass_adjusted.csv',index=False)
    pd.DataFrame(coverage).to_csv(directory/'blind_coverage_by_year.csv',index=False)
    pd.DataFrame(pooled_ranges).to_csv(directory/'pooled_assumption_ranges.csv',index=False)
    sources=[ROOT/'data/verify/v23/verdicts.csv',ROOT/'data/verify/v4/verdicts.csv',ROOT/'data/verify/v3/blind_verdicts.csv']
    sources += [p for p in [ROOT/'data/verify/blind300/summary.csv',ROOT/'data/verify/blind300/comparisons.csv',
                           ROOT/'data/verify/blind300/report_manifest.json'] if p.exists()]
    (directory/'misclassification_manifest.json').write_text(json.dumps(dict(
        blind300_available=(ROOT/'data/verify/blind300/summary.csv').exists(),
        blind_sets=sorted(blind.set.unique()),sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources if p.exists()},
        assumption=POOLED_ASSUMPTION),indent=2)+'\n')
    return blind,pd.DataFrame(metrics),pd.DataFrame(adjusted)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--directory',type=Path,default=OUT);a=ap.parse_args()
    b,m,r=run(a.directory);print('blind',len(b),'metric cells',len(m),'annual ranges',len(r))
