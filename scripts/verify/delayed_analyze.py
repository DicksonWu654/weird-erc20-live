#!/usr/bin/env python3
"""C23: weighted, same-pool launch/one-day transitions. Internal research only."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/verify/delayed'
sys.path.insert(0,str(ROOT/'scripts/tier2'))
from reestimate_v2 import Bootstrap
HARM={'honeypot','high_tax'}
NORMAL={'normal','owner_controlled'}
UNKNOWN={'could_not_buy','unprobeable','v3_fot_incompatible'}
SEED=2320261022

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def kish(w):
    w=np.asarray(w,dtype=float)
    return float(w.sum()**2/(w @ w)) if len(w) and w @ w>0 else 0.
def wilson(p,n):
    if n<=0 or not np.isfinite(p): return np.nan,np.nan
    z=1.959963984540054; a=1+z*z/n
    c=(p+z*z/(2*n))/a
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/a
    return max(0.,c-h),min(1.,c+h)
def load_jsonl(p):
    out={}
    for line in Path(p).read_text().splitlines():
        r=json.loads(line);out[int(r['sample_id'])]=r
    return out

def assemble():
    d=pd.read_parquet(ROOT/'data/tier2/v2/token_results.parquet')
    t=pd.read_parquet(ROOT/'data/tier2/v2/offset_transitions.parquet')
    # No reindexing: bootstrap columns are original sample_id/index.
    assert np.array_equal(d.sample_id,np.arange(len(d)))
    d=d.set_index('sample_id',drop=False)
    cols=['pool_matches','legacy_state_is_later','legacy_block','legacy_category',
          'legacy_liquidity_known','legacy_below_0_01_eth','legacy_evidence','legacy_measurement_evidence']
    d=d.join(t.set_index('sample_id')[cols])
    d['year']=d.analysis_month.str[:4]
    d['normal_trade']=d.measurable&d.category.isin(NORMAL)
    d['strict_normal']=d.measurable&d.category.eq('normal')
    d['late_offset']=d.legacy_block-d.first_liquidity_block
    d['same_later']=d.legacy_state_is_later.eq(True)
    d['exact7200']=d.same_later&d.late_offset.eq(7200)
    d['near_day']=d.same_later&d.late_offset.between(6900,7200)
    d['late_known']=d.legacy_liquidity_known.eq(True)
    d['late_above']=d.late_known&d.legacy_below_0_01_eth.eq(False)
    d['late_below']=d.late_known&d.legacy_below_0_01_eth.eq(True)
    d['late_harm']=d.late_above&d.legacy_category.isin(HARM)
    d['late_unknown']=d.late_above&~d.legacy_category.isin(HARM|NORMAL)
    d['delayed']=d.normal_trade&d.late_harm
    d['within_day']=d.headline_trade_harm|d.delayed
    return d

def estimate(d,y,boot,**meta):
    y=np.asarray(y,dtype=bool);w=d.weight.to_numpy(dtype=float)
    D=float(w.sum());n=len(d);pos=int(y.sum());p=float(w @ y/D) if D else np.nan
    dk=kish(w);pk=kish(w[y]);wl,wh=wilson(p,dk)
    if n:
        den=boot.values(d.index);num=boot.values(d.index,y)
        rep=np.divide(num,den,out=np.full_like(num,np.nan),where=den>0)
        valid=rep[np.isfinite(rep)]
        bl,bh=np.quantile(valid,[.025,.975]) if len(valid)>=.9*boot.reps else (np.nan,np.nan)
    else:bl=bh=np.nan;valid=[]
    # Boundary percentile intervals cannot establish absence with finite support.
    boundary=p in (0.,1.) or (np.isfinite(bl) and bh-bl<1e-14)
    cl,ch=(wl,wh) if boundary else (bl,bh)
    return dict(**meta,n_sampled=n,n_positive=pos,denominator_weight=D,
                positive_weight=float(w @ y) if n else 0.,share=p,
                denominator_kish_n_eff=dk,positive_kish_n_eff=pk,effective_n=min(dk,pk),
                low_positive_support=pk<10,wilson_ci_low=wl,wilson_ci_high=wh,
                bootstrap_ci_low=bl,bootstrap_ci_high=bh,ci_low=cl,ci_high=ch,
                ci_method='Kish_Wilson_boundary' if boundary else 'stratified_cluster_bootstrap_FPC',
                bootstrap_valid_replicates=len(valid),classification_error_included=False,
                matching_selection_error_included=False,
                period_coverage='Jan-Sep' if meta.get('year')=='2026' else 'observed_creation_cohort')

def run(reps=1000):
    OUT.mkdir(parents=True,exist_ok=True)
    full=assemble();boot=Bootstrap(full,reps=reps,seed=SEED)
    d=full[~(full.source.eq('pool_linked')&full.analysis_month.lt('2025-02'))]
    trans=[];within=[];coverage=[]
    for year,g in d.groupby('year'):
      for venue in ['All','V1/V2','V3','V4']:
        v=g if venue=='All' else g[g.venue.eq(venue)]
        m=v[v.measurable]
        within.append(estimate(m,m.headline_trade_harm,boot,year=year,venue=venue,
                              followup='full_launch_frame',metric='harm_plus300',denominator='launch_measurable'))
        for scope,col in [('exact_plus7200','exact7200'),('near_day_6900_7200','near_day'),('legacy_same_pool_later','same_later')]:
          p=v[v[col]];k=p[p.late_known];normal=k[k.normal_trade];survive=normal[normal.late_above]
          coverage.append(dict(year=year,venue=venue,followup=scope,
              n_launch_measurable=len(m),launch_measurable_weight=float(m.weight.sum()),
              n_launch_normal=int(v.normal_trade.sum()),launch_normal_weight=float(v.loc[v.normal_trade,'weight'].sum()),
              n_timing_matched=len(p),n_matched_measurable=int(p.measurable.sum()),
              n_normal_known=len(normal),normal_kish_n_eff=kish(normal.weight),
              matched_normal_weight=float(normal.weight.sum()),
              normal_followup_weight_coverage=float(normal.weight.sum()/v.loc[v.normal_trade,'weight'].sum()) if v.normal_trade.any() else np.nan,
              n_surviving_normal=len(survive),surviving_normal_kish_n_eff=kish(survive.weight),
              min_offset=float(p.late_offset.min()) if len(p) else np.nan,
              max_offset=float(p.late_offset.max()) if len(p) else np.nan))
          common=dict(year=year,venue=venue,followup=scope,normal_definition='normal_or_owner_controlled')
          metrics=[('delayed_trap_given_survives',survive,survive.late_harm,'normal_plus300_and_late_ge_floor'),
                   ('below_floor_given_normal',normal,normal.late_below,'normal_plus300_late_liquidity_known'),
                   ('delayed_trap_given_normal',normal,normal.late_harm,'normal_plus300_late_liquidity_known'),
                   ('late_trade_unknown_given_survives',survive,survive.late_unknown,'normal_plus300_and_late_ge_floor'),
                   ('late_liquidity_unknown_given_normal',p[p.normal_trade],~p[p.normal_trade].late_known,'normal_plus300_timing_matched')]
          for metric,a,y,den in metrics:
            trans.append(estimate(a,y,boot,**common,metric=metric,denominator=den))
          strict=survive[survive.strict_normal]
          trans.append(estimate(strict,strict.late_harm,boot,year=year,venue=venue,followup=scope,
                        normal_definition='strict_normal_label',metric='delayed_trap_given_survives',denominator='strict_normal_plus300_and_late_ge_floor'))
          a=k[k.measurable]
          for metric,y in [('harm_plus300',a.headline_trade_harm),('harm_plus300_or_delayed',a.within_day),
                           ('increment_from_delayed',a.delayed),
                           ('harm_plus300_or_delayed_or_below_floor',a.within_day|(a.normal_trade&a.late_below))]:
            within.append(estimate(a,y,boot,year=year,venue=venue,followup=scope,metric=metric,
                          denominator='launch_measurable_same_pool_late_liquidity_known'))
          # Full-domain observed lower bound; unknown trade or unmatched normal can be delayed.
          obs=m[col]&m.late_known
          lower=m.headline_trade_harm|(obs&m.delayed)
          upper=lower|(m.normal_trade&(~obs|(m.late_unknown&obs)))
          for metric,y in [('within_day_observed_lower_bound',lower),('within_day_missing_followup_upper_bound',upper)]:
            within.append(estimate(m,y,boot,year=year,venue=venue,followup=scope,metric=metric,
                           denominator='all_launch_measurable_identification_bound'))
    pd.DataFrame(trans).to_csv(OUT/'transitions_by_year.csv',index=False)
    pd.DataFrame(within).to_csv(OUT/'within_day_harm.csv',index=False)
    pd.DataFrame(coverage).to_csv(OUT/'coverage.csv',index=False)
    # Paired contrasts use common bootstrap draws, including the subtraction of launch harm.
    contrasts=[]
    for venue in ['All','V1/V2','V3','V4']:
      estimates={}
      for year in ['2023','2024','2025','2026']:
        a=d[d.year.eq(year)&d.exact7200&d.late_known&d.measurable]
        if venue!='All':a=a[a.venue.eq(venue)]
        den=boot.values(a.index)
        for metric,col in [('harm_plus300','headline_trade_harm'),('harm_plus300_or_delayed','within_day')]:
          values=boot.values(a.index,a[col])
          estimates[(year,metric)]=(float(a.weight @ a[col]/a.weight.sum()) if len(a) else np.nan,
                    np.divide(values,den,out=np.full_like(values,np.nan),where=den>0))
      for early,late in [('2024','2025'),('2024','2026'),('2025','2026')]:
        for metric in ['harm_plus300','harm_plus300_or_delayed']:
          a,ar=estimates[(early,metric)];b,br=estimates[(late,metric)]
          delta=br-ar;good=delta[np.isfinite(delta)]
          lo,hi=np.quantile(good,[.025,.975]) if len(good)>=.9*reps else (np.nan,np.nan)
          contrasts.append(dict(venue=venue,early_year=early,late_year=late,metric=metric,
                            difference=b-a,bootstrap_ci_low=lo,bootstrap_ci_high=hi))
    pd.DataFrame(contrasts).to_csv(OUT/'year_contrasts.csv',index=False)
    # Fixed primary 2024 launch-measurable venue composition, with the same
    # full-frame bootstrap draws used for every constituent ratio.
    ref=d[d.year.eq('2024')&d.measurable].groupby('venue').weight.sum()
    ref=ref/ref.sum();standard=[]
    for scope,col in [('exact_plus7200','exact7200'),('near_day_6900_7200','near_day')]:
      for year in sorted(d.year.unique()):
       for metric,outcome in [('harm_plus300','headline_trade_harm'),('harm_plus300_or_delayed','within_day')]:
        point=0.;rep=np.zeros(reps);support=[];missing=False
        for venue,rw in ref.items():
          a=d[d.year.eq(year)&d.venue.eq(venue)&d.measurable&d[col]&d.late_known]
          if a.empty:missing=True;break
          point+=rw*float(a.weight @ a[outcome]/a.weight.sum())
          den=boot.values(a.index);num=boot.values(a.index,a[outcome])
          rep+=rw*np.divide(num,den,out=np.full_like(num,np.nan),where=den>0)
          support.append(kish(a.weight))
        good=rep[np.isfinite(rep)]
        lo,hi=np.quantile(good,[.025,.975]) if not missing and len(good)>=.9*reps else (np.nan,np.nan)
        standard.append(dict(year=year,followup=scope,metric=metric,share=point if not missing else np.nan,
          bootstrap_ci_low=lo,bootstrap_ci_high=hi,minimum_venue_denominator_kish=min(support) if support else 0.,
          reference='full launch-measurable 2024 venue weights',reference_weights=json.dumps(ref.to_dict()),
          missing_reference_venue=missing))
    pd.DataFrame(standard).to_csv(OUT/'venue_standardized.csv',index=False)
    # Private per-row provenance; no source rows leave this owned internal directory.
    cols=['sample_id','year','venue','weight','category','measurable','normal_trade','headline_trade_harm',
          'selected_pool','sim_block','first_liquidity_block','legacy_block','late_offset','legacy_category',
          'same_later','exact7200','late_known','late_below','late_above','delayed','late_unknown',
          'sim_evidence','legacy_evidence','legacy_measurement_evidence']
    d[d.pool_ever][cols].to_parquet(OUT/'matched_rows.parquet',index=False)
    # No additional simulation required: both recent exact-horizon normal cohorts exceed 300.
    sufficient={y:int((d.year.eq(y)&d.exact7200&d.late_known&d.normal_trade).sum()) for y in ['2024','2025','2026']}
    inputs=['data/tier2/v2/offset_transitions.parquet','data/tier2/v2/token_results.parquet',
            'data/tier2/v2/early_results.jsonl','data/tier2/v2/legacy_results.jsonl',
            'data/census/monthly_counts.csv','data/census/blockmap.csv','data/census/sample_frame.parquet']
    meta=dict(seed=SEED,bootstrap_reps=reps,weights='unchanged original census expansion weights',
              normal_definition='measurable normal or owner_controlled; strict-normal sensitivity supplied',
              exact_plus7200_normal_counts=sufficient,additional_sims_triggered=not all(n>=300 for n in sufficient.values()),
              additional_sims_decision='Not triggered: exact +7200 matched normal counts >=300 each year. Sparse positive/venue cells retain wide CIs.',
              interval_scope='Sampling only, conditional on matched subset; no classification/matching-selection correction. Kish-Wilson descriptive and cluster bootstrap approximate.',
              provenance={p:sha(ROOT/p) for p in inputs},
              source_hashes={str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),ROOT/'scripts/tier2/reestimate_v2.py']})
    (OUT/'analysis_manifest.json').write_text(json.dumps(meta,indent=2)+'\n')
    print(json.dumps(meta,indent=2))
    print(pd.DataFrame(trans).query("year >= '2023' and venue in ['All','V1/V2'] and followup == 'exact_plus7200' and normal_definition == 'normal_or_owner_controlled' and metric in ['delayed_trap_given_survives','below_floor_given_normal']")[['year','venue','metric','n_sampled','n_positive','share','ci_low','ci_high']].to_string(index=False))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--reps',type=int,default=1000);args=ap.parse_args();run(args.reps)
