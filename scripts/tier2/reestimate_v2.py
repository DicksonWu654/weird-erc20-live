#!/usr/bin/env python3
"""Tier-2 v2 tables. Primary domain is quote liquidity >= the probe size.

All-new estimates retain unknown categories and report identification bounds.
Late-frame CIs resample sampled creation blocks (including empty blocks) and
pool candidates within original month/source/stratum, with FPC rescaling.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import subprocess
from functools import lru_cache
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/tier2/v2'
sys.path.insert(0,str(ROOT/'scripts/sim'))
import classify as CL
import reaggregate_revision as R
from early_liquidity import load_jsonl

LABELS=['honeypot','high_tax','owner_controlled','normal','could_not_buy','v3_fot_incompatible',
        'no_meaningful_liquidity','no_pool','late_pool','unprobeable']
HARM={'honeypot','high_tax'}
BM=pd.read_csv(ROOT/'data/census/blockmap.csv').set_index('month')
COUNTS=pd.read_csv(ROOT/'data/census/monthly_counts.csv')
COUNTS=COUNTS[COUNTS.series.eq('excl_lp')].set_index('month')


@lru_cache(maxsize=2)
def liquidity_measurements(legacy=False):
    return load_jsonl(OUT/('liquidity_v21_legacy.jsonl' if legacy else 'liquidity_v21_early.jsonl'))


def measured_record(rec,measurement=None,virtual=False):
    """Overlay derived V4 measurement without rewriting captured simulator rows."""
    if not rec or (rec.get('pool_info') or {}).get('kind')!='v4':return rec
    pi=dict(rec['pool_info'])
    if virtual:
        pi['virtual_liquidity_sensitivity']=True
    else:
        valid=(measurement and measurement.get('status')=='ok' and
               measurement['pool'].lower()==rec['pool'].lower() and measurement['block']==rec['block'])
        final=measurement.get('final',{}) if valid else {}
        pi.update(quote_liquidity_method='v4_all_position_principal_at_sim_price',
                  quote_liquidity_eth_equiv_wei=final.get('quote_eth_equiv_wei'),
                  reserve_quote=final.get('reserve_quote'),
                  quote_liquidity_error=None if valid and final.get('quote_eth_equiv_wei') is not None else
                  (measurement or {}).get('reason',final.get('conversion_error','real_position_measurement_unavailable')))
    return {**rec,'pool_info':pi}


def classify_record(rec,floor=10**16,tax=.10,strict=False,lp_fee=False):
    if not rec or not rec.get('pool'):
        why=(rec or {}).get('unprobeable') or 'pending_early_simulation'
        label='late_pool' if str(why).startswith('late_pool_') else 'unprobeable'
        return label,why,[],{}
    # Only the tax threshold changes; floor precedence is reapplied to raw
    # trade measurements, so lowering the floor can restore old dust rows.
    saved=CL.RULES['HIGH_TAX']
    CL.RULES['HIGH_TAX']=np.nextafter(tax,np.inf) if strict else tax
    try:
        flags,label,why,taxes=CL.classify({**rec,'sells':rec.get('sells') or []},rec.get('control'),None,floor)
    finally:
        CL.RULES['HIGH_TAX']=saved
    if lp_fee and label in ('normal','owner_controlled','high_tax'):
        fee=taxes.get('lp_fee')
        if fee is not None:
            # Preserve the headline's per-leg >=10% comparison while adding
            # the core LP fee multiplicatively. Also retain the distinct
            # gross round-trip-cost diagnostic; it is not per-leg token tax.
            cost=1-(1-fee)**2*(1-(taxes.get('buy') or 0))*(1-(taxes.get('sell') or 0))
            taxes['lp_inclusive_round_trip_cost']=cost
            per_leg=max(1-(1-fee)*(1-(taxes.get('buy') or 0)),
                        1-(1-fee)*(1-(taxes.get('sell') or 0)),taxes.get('transfer') or 0)
            taxes['lp_inclusive_max_leg_cost']=per_leg
            if per_leg>=tax:
                label='high_tax';why='LP-inclusive per-leg cost sensitivity'
    return label,why,flags,taxes


def assemble(records,floor=10**16,tax=.10,strict=False,lp_fee=False,virtual=False,legacy=False):
    frame=pd.read_parquet(ROOT/'data/census/sample_frame.parquet').reset_index(drop=True)
    frame.insert(0,'sample_id',np.arange(len(frame)))
    bm=BM.reset_index()
    frame['analysis_month']=bm.month.to_numpy()[np.searchsorted(bm.last_block,frame.creation_block)]
    selections=load_jsonl(OUT/'liquidity_selection.jsonl')
    liquidity=liquidity_measurements(legacy)
    rows=[]
    for r in frame.itertuples():
        rec=records.get(int(r.sample_id))
        measurement=liquidity.get(int(r.sample_id),{})
        rec=measured_record(rec,measurement,virtual)
        sel=selections.get(int(r.sample_id),{})
        pi=(rec or {}).get('pool_info') or {}
        if not r.pool_ever:
            label,why,flags,taxes='no_pool','never_pooled_in_scanned_venues',[],{}
        else:
            label,why,flags,taxes=classify_record(rec,floor,tax,strict,lp_fee)
        reserve=CL.measured_quote_eth_equiv(pi)
        known=reserve is not None
        reserve=int(reserve) if known else None
        dex=(rec or {}).get('dex') or sel.get('dex')
        venue='V3' if dex=='uniswap_v3' else 'V4' if dex=='uniswap_v4' else 'V1/V2' if dex else 'unknown'
        tmax=max(taxes.get('buy') or 0,taxes.get('sell') or 0,taxes.get('transfer') or 0)
        first=measurement.get('first') or {}
        final=measurement.get('final') or {}
        fq=first.get('quote_eth_equiv_wei')
        rows.append(dict(category=label,base_category=label,category_reason=why,
                         unprobeable_reason=why if label in ('unprobeable','could_not_buy','late_pool') else None,
                         selected_pool=(rec or {}).get('pool'),selected_dex=dex,venue=venue,
                         sim_block=(rec or {}).get('block'),first_liquidity_block=sel.get('first_liquidity_block'),
                         selection_status=sel.get('status','not_pooled' if not r.pool_ever else 'pending'),
                         selection_reason=sel.get('unprobeable_reason'),
                         selection_evidence=json.dumps(sel.get('evidence',[])),
                         simulation_status='complete' if rec else 'not_pooled' if not r.pool_ever else 'pending',
                         quote_reserve_eth_equiv_wei=str(reserve) if known else None,
                         quote_liquidity_eth=float(reserve)/1e18 if known else np.nan,
                         quote_liquidity_method=pi.get('quote_liquidity_method'),
                         quote_liquidity_error=pi.get('quote_liquidity_error'),
                         virtual_quote_liquidity_eth_equiv_wei=measurement.get('virtual_quote_eth_equiv_wei'),
                         first_quote_eth_equiv_wei=fq,first_liquidity_known=fq is not None,
                         first_liquidity_ge_floor=(int(fq)>=floor) if fq is not None else False,
                         first_reserve_quote=first.get('reserve_quote'),first_reserve_token=first.get('reserve_token'),
                         final_position_reserve_quote=final.get('reserve_quote'),final_position_reserve_token=final.get('reserve_token'),
                         reserve_direction=measurement.get('reserve_direction','unknown'),
                         first_reserve_method=measurement.get('first_reserve_method'),
                         reserve_measurement_status=measurement.get('status','not_measured'),
                         reserve_measurement_reason=measurement.get('reason'),
                         first_liquidity_error=first.get('conversion_error') or measurement.get('reason'),
                         reserve_evidence=json.dumps(measurement.get('evidence',[])),
                         measurable=bool(r.pool_ever and known and reserve>=floor),
                         tradable_ge_0_1_eth=bool(r.pool_ever and known and reserve>=10**17),
                         liquidity_known=known,sim_flags=json.dumps(flags),sim_taxes=json.dumps(taxes),
                         tax_max=tmax,tax_threshold_boundary='tax_threshold_boundary' in flags,
                         owner_control_capability=('owner_controlled' in flags) if rec and rec.get('buy') else None,
                         v4_nonstandard_lp_fee='v4_nonstandard_lp_fee_round_trip_cost' in flags,
                         lp_fee=taxes.get('lp_fee'),lp_fee_round_trip_cost=taxes.get('lp_fee_round_trip_cost'),
                         sim_evidence=json.dumps((rec or {}).get('evidence')),
                         original_unprobeable=(rec or {}).get('unprobeable'),
                         replay_consistent=(rec or {}).get('replay_consistent'),
                         creation_method_group={'direct_deploy':'direct','factory':'factory-launcher'}.get(r.creation_method,'unknown'),
                         offset_blocks=300,dust_floor_wei=str(floor),high_tax_threshold=tax,
                         headline_trade_harm=label in HARM))
    d=pd.concat([frame,pd.DataFrame(rows)],axis=1)
    d['owner_control_capability']=pd.array(d.owner_control_capability,dtype='boolean')
    return d


class Bootstrap:
    def __init__(self,frame,reps=1000,seed=1720261022):
        self.reps=reps
        self.frame=frame
        self.rng=np.random.default_rng(seed)
        self.weights=np.tile(frame.weight.to_numpy(dtype=float),(reps,1))
        self.census={}
        for (month,source,stratum),g in frame.groupby(['month','source','stratum'],sort=True):
            n=int(g.n_stratum_sampled.iloc[0]);N=float(g.N_stratum.iloc[0])
            if source=='blocksample':
                n=int(COUNTS.loc[month,'n_blocks_sampled'])
                N=int(BM.loc[month,'last_block'])-max(int(BM.loc[month,'first_block']),21850000)+1
                observed=sorted(g.creation_block.unique())
                mapping={b:i for i,b in enumerate(observed)}
                units=np.array([mapping[b] for b in g.creation_block])
            else:
                observed=list(range(len(g)));units=np.arange(len(g))
            if n<=1 or n>=N:
                continue
            if len(observed)>n:
                raise ValueError(f'more observed PSUs than sampled: {month}/{source}/{stratum}')
            # Anonymous empty sampled blocks carry the same zero outcome as
            # explicitly enumerated empty blocks. Keeping n slots is exact
            # for the nonparametric cluster bootstrap distribution.
            draws=self.rng.multinomial(n,np.full(n,1/n),size=reps)
            multiplier=1+math.sqrt(max(0.,1-n/N))*(draws[:,units]-1)
            self.weights[:,g.index]*=multiplier
        for month,c in COUNTS.iterrows():
            z=self.rng.normal(size=reps)
            delta=np.where(z<0,(c.n_est-c.ci_low)/1.9599639845,(c.ci_high-c.n_est)/1.9599639845)
            self.census[month]=np.maximum(0.,float(c.n_est)+z*delta)

    def values(self,index,y=None):
        if not len(index):
            return np.zeros(self.reps)
        w=self.weights[:,np.asarray(index)]
        return w.sum(axis=1) if y is None else w @ np.asarray(y,dtype=float)


def period_values(df,frequency):
    if frequency=='monthly':return df.analysis_month
    if frequency=='annual':return df.analysis_month.str[:4]
    return df.analysis_month.str[:4]+'Q'+(((df.analysis_month.str[5:7].astype(int)-1)//3)+1).astype(str)


def tail_design_effect(frame):
    """Observed token-count clustering diagnostic, including zero blocks.

    sum(m_b^2)/sum(m_b) is the token-weighted cluster size, the count design
    effect under perfect within-block outcome correlation. It cannot account
    for a launcher burst that the first-stage block sample entirely missed.
    """
    b=frame[frame.source.eq('blocksample')]
    if b.empty:return 1.
    n=b.groupby(['month','source','stratum','creation_block']).size().to_numpy(dtype=float)
    return max(1.,float(n @ n/n.sum())) if n.sum() else 1.


def row_estimate(full,d,label,denominator,period,frequency,boot):
    all_new=denominator.startswith('headline_excl_lp')
    restricted=all_new and '2025-02' in set(d.analysis_month)
    if restricted and frequency!='monthly':
        d=d[d.analysis_month.ne('2025-02')]
        full=full[full.analysis_month.ne('2025-02')]
    structural=(denominator=='pooled_tokens' and label=='no_pool') or (
        denominator in ('measurable_pooled','tradable_ge_0_1_eth') and
        label in ('no_pool','no_meaningful_liquidity','late_pool'))
    D=float(d.weight.sum());dk=R.kish(d.weight)
    if label=='headline_trade_harm':y=d.headline_trade_harm.astype(float).to_numpy()
    elif label=='owner_control_capability':y=d.owner_control_capability.eq(True).fillna(False).to_numpy(dtype=float)
    else:y=d.category.eq(label).to_numpy(dtype=float)
    capability_unknown=label=='owner_control_capability' and d.owner_control_capability.isna().any()
    identified=D>0 and not structural and not capability_unknown and not (restricted and frequency=='monthly')
    mass=float(d.weight.to_numpy() @ y) if D else np.nan
    p=mass/D if identified else np.nan
    if np.isfinite(p):
        if not -1e-12<=p<=1+1e-12:
            raise ValueError(f'ratio outside [0,1]: {p}')
        p=float(np.clip(p,0.,1.))
    pos_eff=R.kish(d.loc[y.astype(bool),'weight'])
    eff=min(dk,pos_eff)
    coverage=('undefined_missing_2025-02_prefix' if restricted and frequency=='monthly' else
              'restricted_excludes_2025-02' if restricted else
              'year_to_date_Jan_Sep_2026' if frequency=='annual' and period=='2026' else 'full_frame_scope')
    months=sorted(d.analysis_month.unique())
    census_calibrated=denominator=='headline_excl_lp'
    target=float(COUNTS.loc[months,'n_est'].sum()) if census_calibrated else D
    low=high=clo=chi=np.nan;method='undefined'
    late=bool((full.analysis_month>='2025-02').any())
    if identified:
        den=boot.values(d.index)
        num=boot.values(d.index,y)
        replicates=np.divide(num,den,out=np.full_like(num,np.nan),where=den>0)
        if late:
            finite=replicates[np.isfinite(replicates)]
            if len(finite)>=boot.reps*.9:
                low,high=np.quantile(finite,[.025,.975])
                # Pure percentile intervals collapse at sample boundaries.
                # Retain the design-score/Kish boundary interval there.
                if p in (0.,1.) or high-low<1e-14:
                    low,high,_=R.base_interval(d,y,p,BM,COUNTS,frame=full)
                method='stratified_cluster_bootstrap_FPC_approx95'
            else:
                identified=False;p=np.nan
                method='undefined_bootstrap_domain_support'
        else:
            low,high,_=R.base_interval(d,y,p,BM,COUNTS,frame=full)
            method='C12_stratified_phase1_score_FPC_approx95'
        if np.isfinite(p):
            low,high=max(0.,min(low,p)),min(1.,max(high,p))
            if census_calibrated:
                cb=sum((boot.census[m] for m in months),np.zeros(boot.reps))
                cr=replicates*cb
                clo,chi=np.nanquantile(cr,[.025,.975])
                clo=min(float(clo),low*float(COUNTS.loc[months,'ci_low'].sum()),p*target)
                chi=max(float(chi),high*float(COUNTS.loc[months,'ci_high'].sum()),p*target)
            else:
                clo,chi=np.nanquantile(num,[.025,.975])
                clo=min(float(clo),low*D,p*target);chi=max(float(chi),high*D,p*target)
    row=dict(period=period,denominator=denominator,label=label,n_sampled=len(d),share=p,
             ci_low=low,ci_high=high,est_count=p*target,est_count_ci_low=clo,est_count_ci_high=chi,
             denominator_est_count=target,frame_denominator_weight=D,denominator_effective_n=dk,
             positive_effective_n=pos_eff,effective_n=eff,low_n=eff<10,
             quarterly_publishable=bool(identified and eff>=30 and not restricted) if frequency=='quarterly' else None,
             identified=bool(identified),period_coverage=coverage,ci_method=method,
             lower_confidence_late_frame=late,classification_error_included=False,
             observed_block_count_design_effect=tail_design_effect(full),
             census_count_uncertainty_propagated=census_calibrated,
             denominator_definition=('all_new_non_LP' if all_new else 'quote_liquidity_ge_0.01_ETH' if denominator=='measurable_pooled'
                                     else 'V1/V2 with measured reserve ≥0.1 ETH' if denominator=='tradable_ge_0_1_eth' else 'pooled'),
             measurement_scope='structurally_excluded_by_denominator' if structural else 'observed_trade_at_launch_plus300')
    return row,d


def tables(df,boot,frequency,only_headline=False,group=None):
    rows=[];bounds=[]
    periods=period_values(df,frequency)
    for period,full in df.groupby(periods,sort=True):
        groups=[('',full)] if group is None else [(str(k),g) for k,g in full.groupby(group)]
        for name,g in groups:
            # For domain cuts, keep the complete period in the variance frame.
            denoms={'headline_excl_lp':g,'measurable_pooled':g[g.measurable]}
            if group=='venue':
                denoms={'pooled_tokens':g[g.pool_ever],'measurable_pooled':g[g.measurable]}
            elif group=='creation_method_group':
                denoms={f'headline_excl_lp_{name}':g,'measurable_pooled':g[g.measurable]}
            if group is None and not only_headline:
                denoms.update(pooled_tokens=g[g.pool_ever],
                    tradable_ge_0_1_eth=g[g.tradable_ge_0_1_eth & g.venue.eq('V1/V2')])
            for denominator,d in denoms.items():
                labels=['headline_trade_harm'] if only_headline else LABELS+['headline_trade_harm','owner_control_capability']
                for label in labels:
                    row,cut=row_estimate(full,d,label,denominator,period,frequency,boot)
                    if group:row[group]=name
                    rows.append(row)
                    if label=='headline_trade_harm':
                        D=float(cut.weight.sum())
                        could=float(cut.loc[cut.category.eq('could_not_buy'),'weight'].sum())
                        unknown=float(cut.loc[cut.category.isin(['could_not_buy','unprobeable','v3_fot_incompatible','late_pool']),'weight'].sum())
                        p=row['share']
                        bounds.append(dict(period=period,frequency=frequency,denominator=denominator,
                                           could_not_buy_weight=could,denominator_weight=D,
                                           lower=p,upper=min(1.,p+could/D) if D and np.isfinite(p) else np.nan,
                                           all_unresolved_upper=min(1.,p+unknown/D) if D and np.isfinite(p) else np.nan,
                                           scope='partial identification; not a confidence interval'))
    col={'annual':'year','quarterly':'quarter','monthly':'month'}[frequency]
    return pd.DataFrame(rows).rename(columns={'period':col}),pd.DataFrame(bounds)


def standardized(venue,df,boot):
    mix=venue[venue.year.eq('2024')&venue.denominator.eq('measurable_pooled')].copy()
    weights=dict(zip(mix.venue,mix.frame_denominator_weight/mix.frame_denominator_weight.sum()))
    rows=[]
    for year,g in venue[venue.denominator.eq('measurable_pooled')].groupby('year'):
        mapping=g.set_index('venue')
        ok=all(v in mapping.index and np.isfinite(mapping.loc[v,'share']) for v,w in weights.items() if w>0)
        point=sum(w*mapping.loc[v,'share'] for v,w in weights.items() if w>0) if ok else np.nan
        lo=hi=np.nan;ne=0.
        if ok:
            reps=np.zeros(boot.reps);supports=[]
            for v,w in weights.items():
                if w<=0:continue
                cut=df[df.analysis_month.str[:4].eq(year)&df.measurable&df.venue.eq(v)]
                den=boot.values(cut.index);num=boot.values(cut.index,cut.headline_trade_harm)
                reps+=w*np.divide(num,den,out=np.full_like(num,np.nan),where=den>0)
                supports.append(float(mapping.loc[v,'effective_n']))
            good=reps[np.isfinite(reps)]
            if len(good)>=boot.reps*.9:
                lo,hi=np.quantile(good,[.025,.975]);lo=min(lo,point);hi=max(hi,point)
                if hi-lo<1e-14:
                    lo=sum(w*mapping.loc[v,'ci_low'] for v,w in weights.items() if w>0)
                    hi=sum(w*mapping.loc[v,'ci_high'] for v,w in weights.items() if w>0)
                ne=min(supports,default=0.)
            else:
                ok=False;point=np.nan
        rows.append(dict(year=year,denominator='measurable_pooled',reference_year=2024,
                         share=point,
                         reference_mix_json=json.dumps(weights,sort_keys=True),
                         identified=ok,reason='fixed_2024_measurable_venue_mix' if ok else 'missing_reference_venue_support',
                         ci_low=lo,ci_high=hi,ci_method='fixed_mix_stratified_bootstrap_FPC_approx95',
                         effective_n=ne,low_n=ne<10,
                         support_flag='low_support' if ne<10 else 'supported',
                         reference_venue_support_json=json.dumps({v:dict(effective_n=float(mapping.loc[v,'effective_n']),
                             denominator_effective_n=float(mapping.loc[v,'denominator_effective_n']),
                             n_sampled=int(mapping.loc[v,'n_sampled'])) if v in mapping.index else None
                             for v,w in weights.items() if w>0},sort_keys=True),
                         classification_error_included=False))
    return pd.DataFrame(rows)


def run(reps=1000,preview=False):
    records=load_jsonl(OUT/'early_results.jsonl')
    if not preview:
        for legacy in (False,True):
            rr=load_jsonl(OUT/'legacy_results.jsonl') if legacy else records
            measurements=liquidity_measurements(legacy)
            if not legacy:
                missing_any=[sid for sid,r in rr.items() if r.get('pool') and
                             (sid not in measurements or measurements[sid]['pool'].lower()!=r['pool'].lower() or
                              measurements[sid]['block']!=r['block'])]
                if missing_any:raise SystemExit(f'{len(missing_any)} first-liquidity/reserve measurements pending; refusing final tables')
            missing=[sid for sid,r in rr.items() if r.get('dex')=='uniswap_v4' and
                     (sid not in measurements or measurements[sid].get('status')!='ok' or
                      measurements[sid]['pool'].lower()!=r['pool'].lower() or measurements[sid]['block']!=r['block'])]
            if missing:raise SystemExit(f'{len(missing)} V4 position reconstructions incomplete (legacy={legacy}); refusing final tables')
    df=assemble(records)
    pending=int(df.simulation_status.eq('pending').sum())
    if pending and not preview:
        raise SystemExit(f'{pending} pooled rows still pending; refusing final tables')
    dest=OUT/'preview' if preview else OUT
    dest.mkdir(parents=True,exist_ok=True)
    df.to_parquet(dest/'token_results.parquet',index=False)
    print(f'assembled {len(df)} rows; pending={pending}; destination={dest}',flush=True)
    # Keep original indices so every domain uses the same bootstrap draws.
    scope=df[~((df.source=='pool_linked')&(df.analysis_month<'2025-02'))]
    boot=Bootstrap(df,reps)
    allbounds=[]
    for frequency in ('monthly','quarterly','annual'):
        t,b=tables(scope,boot,frequency)
        t.to_csv(dest/f'{frequency}_shares.csv',index=False)
        allbounds.append(b)
        print(f'{frequency} shares: {len(t)} rows',flush=True)
    venue,_=tables(scope,boot,'annual',True,'venue')
    venue.to_csv(dest/'venue_by_year.csv',index=False)
    methods,_=tables(scope,boot,'annual',True,'creation_method_group')
    methods.to_csv(dest/'method_by_year.csv',index=False)
    standardized(venue,scope,boot).to_csv(dest/'standardized.csv',index=False)
    from descriptive_v21 import write_tables
    write_tables(scope,boot,dest)
    pd.concat(allbounds).to_csv(dest/'bounds.csv',index=False)
    scenarios=[('primary',10**16,.1,False,False,records),
               ('floor_1e9_wei',10**9,.1,False,False,records),
               ('floor_0.1_eth',10**17,.1,False,False,records),
               ('tax_gt10_strict',10**16,.1,True,False,records),
               ('tax_ge5',10**16,.05,False,False,records),
               ('LP_fee_as_tax',10**16,.1,False,True,records)]
    scenarios.extend([('virtual_V4_liquidity',10**16,.1,False,False,records),
                      ('known_trade_outcomes_only',10**16,.1,False,False,records)])
    legacy_path=OUT/'legacy_results.jsonl'
    if legacy_path.exists():
        scenarios.append(('legacy_selected_pool_plus7200',10**16,.1,False,False,load_jsonl(legacy_path)))
    sensitivity=[]
    for name,floor,tax,strict,lp,rr in scenarios:
        print(f'sensitivity: {name}',flush=True)
        sd=assemble(rr,floor,tax,strict,lp,virtual=name=='virtual_V4_liquidity',legacy=name=='legacy_selected_pool_plus7200')
        sd=sd.loc[scope.index]
        if name=='floor_1e9_wei':
            from descriptive_v21 import write_residual
            write_residual(sd,boot,dest)
        if name=='known_trade_outcomes_only':
            sd['measurable']=sd.measurable & sd.category.isin(['honeypot','high_tax','normal','owner_controlled'])
        for frequency in ('annual','quarterly'):
            table,_=tables(sd,boot,frequency,True)
            pc='year' if frequency=='annual' else 'quarter'
            table=table.rename(columns={pc:'period'})
            table['scenario']=name;table['frequency']=frequency;table['floor_wei']=str(floor)
            table['tax_threshold']=tax;table['tax_strict']=strict
            table['offset_blocks']=7200 if name=='legacy_selected_pool_plus7200' else 300
            if name=='legacy_selected_pool_plus7200':
                table['measurement_scope']='legacy_selected_pool_creation_plus7200;selection_and_anchor_differ'
            if name=='virtual_V4_liquidity':table['measurement_scope']='sensitivity_only;V4_active_virtual_amount;other_venues_unchanged'
            if name=='known_trade_outcomes_only':table['measurement_scope']='comparability_sensitivity;excludes_unresolved_trade_outcomes_from_measurable_denominator'
            table['denominator_definition']=np.where(table.denominator.eq('measurable_pooled'),
                                                     f'quote_liquidity_ge_{floor}_wei',table.denominator_definition)
            pooled_stats={}
            for period,pg in sd.groupby(period_values(sd,frequency)):
                pg=pg[pg.pool_ever];total=float(pg.weight.sum())
                pooled_stats[period]=dict(
                    no_meaningful_liquidity_share_pooled=float(pg.loc[pg.category.eq('no_meaningful_liquidity'),'weight'].sum())/total if total else np.nan,
                    measurable_share_pooled=float(pg.loc[pg.measurable,'weight'].sum())/total if total else np.nan,
                    liquidity_known_weight_share=float(pg.loc[pg.liquidity_known,'weight'].sum())/total if total else np.nan)
            for column in ('no_meaningful_liquidity_share_pooled','measurable_share_pooled','liquidity_known_weight_share'):
                table[column]=table.period.map(lambda p:pooled_stats[p][column])
            sensitivity.append(table)
    sens=pd.concat(sensitivity,ignore_index=True)
    inflated=sens[sens.scenario.eq('primary')].copy()
    factor=np.sqrt(inflated.observed_block_count_design_effect)
    inflated['ci_low']=np.maximum(0.,inflated.share-factor*(inflated.share-inflated.ci_low))
    inflated['ci_high']=np.minimum(1.,inflated.share+factor*(inflated.ci_high-inflated.share))
    inflated['est_count_ci_low']=np.maximum(0.,inflated.est_count-factor*(inflated.est_count-inflated.est_count_ci_low))
    inflated['est_count_ci_high']=inflated.est_count+factor*(inflated.est_count_ci_high-inflated.est_count)
    inflated['scenario']='observed_block_tail_design_effect_inflation'
    inflated['ci_method']='cluster_bootstrap_plus_observed_token_count_tail_inflation_sensitivity'
    sens=pd.concat([sens,inflated],ignore_index=True)
    sens.to_csv(dest/'sensitivity.csv',index=False)
    sens[sens.scenario.isin(['primary','floor_1e9_wei','floor_0.1_eth'])].to_csv(dest/'dust_sensitivity.csv',index=False)
    manifest=dict(complete=pending==0 and not preview,n_rows=len(df),n_pooled=int(df.pool_ever.sum()),pending=pending,
                  n_first_liquidity_measured=int(df.first_liquidity_known.sum()),
                  n_reserve_measurement_unprobeable=int(df.reserve_measurement_status.eq('unprobeable').sum()),
                  n_reserve_measurement_pending=int((df.selection_status.eq('selected')&df.reserve_measurement_status.eq('not_measured')).sum()),
                  bootstrap_replicates=reps,bootstrap_seed=1720261022,rules=CL.RULES,
                  inputs={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [
                      ROOT/'data/census/sample_frame.parquet',ROOT/'data/census/monthly_counts.csv',
                      ROOT/'data/census/blockmap.csv',OUT/'early_results.jsonl',OUT/'liquidity_selection.jsonl',
                      OUT/'liquidity_v21_early.jsonl',OUT/'liquidity_v21_legacy.jsonl',
                      OUT/'v4_initialize_index.jsonl',OUT/'v4_initialize_manifest.json',
                      OUT/'v4_stateview_validation.csv',ROOT/'data/raw/census/dexlogs_uniswap_v4.jsonl.gz',
                      ROOT/'data/census/dex_pools.parquet',ROOT/'data/census/evidence/dex_raw_files.json']})
    sources=[ROOT/'scripts/tier2'/f'{name}.py' for name in ('reestimate_v2','reaggregate_revision','aggregate',
             'early_liquidity','c17_rpc','legacy_v2','misclassification_v2','origin_audit','report_v2','verify_v2',
             'liquidity_v21','descriptive_v21','initialize_index_v21','report_v21','spotchecks_v2')]
    sources += [ROOT/'scripts/sim'/f'{name}.py' for name in ('classify','engine','pipeline','run','v4')]
    manifest['source_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    manifest['git_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    (dest/'build_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (dest/'RULES_V2_READY').write_text(json.dumps(CL.RULES,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--preview',action='store_true');ap.add_argument('--reps',type=int,default=1000)
    a=ap.parse_args();run(a.reps,a.preview)
