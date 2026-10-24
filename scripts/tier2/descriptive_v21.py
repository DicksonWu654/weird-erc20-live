"""Weighted launch-funding/removal descriptions and unresolved-route dilution."""
import json
import numpy as np
import pandas as pd


def estimate(full,domain,outcome,metric,denominator,year,venue,boot):
    from reestimate_v2 import row_estimate
    cut=domain.copy()
    cut['headline_trade_harm']=outcome.reindex(cut.index).fillna(False).astype(bool)
    row,_=row_estimate(full,cut,'headline_trade_harm',denominator,year,'annual',boot)
    row.update(year=year,venue=venue,metric=metric,label=metric,
               n_positive=int(cut.headline_trade_harm.sum()),
               positive_weight=float(cut.loc[cut.headline_trade_harm,'weight'].sum()),
               denominator_definition=denominator,
               measurement_scope='descriptive launch-state liquidity; reserve direction is not attribution or intent')
    return row


def write_tables(df,boot,dest):
    rows=[];dilution=[];residual=[]
    for year,full in df.groupby(df.analysis_month.str[:4],sort=True):
        for venue,g in [('all',full)]+list(full.groupby('venue',sort=True)):
            pooled=g[g.pool_ever];funded=pooled[pooled.first_liquidity_ge_floor]
            below=pooled.category.eq('no_meaningful_liquidity')
            emptied=funded[funded.category.eq('no_meaningful_liquidity')]
            joint=pooled.headline_trade_harm|below
            metrics=[
                ('first_liquidity_measured_share_of_pooled',pooled,pooled.first_liquidity_known,'pooled_tokens'),
                ('first_funded_share_of_pooled_observed_lower',pooled,pooled.first_liquidity_ge_floor,'pooled_tokens'),
                ('first_funded_share_of_first_measured',pooled[pooled.first_liquidity_known],pooled.first_liquidity_ge_floor,'first_liquidity_measured_pooled'),
                ('below_floor_at300_share_of_pooled_observed_lower',pooled,below,'pooled_tokens'),
                ('below_floor_by300_given_first_funded',funded,below,'observed_first_funded_pooled'),
                ('harmful_or_emptied_within_hour_given_first_funded',funded,joint,'observed_first_funded_pooled'),
                ('harmful_or_below_floor_at300_share_of_pooled_observed_lower',pooled,joint,'pooled_tokens')]
            for direction in ('withdrawal','dumping','other','unknown'):
                metrics.append((f'{direction}_given_first_funded_then_below',emptied,emptied.reserve_direction.eq(direction),'observed_first_funded_then_below_floor'))
            for metric,domain,outcome,den in metrics:
                row=estimate(full,domain,outcome,metric,den,year,venue,boot)
                weight=float(domain.weight.sum())
                if metric.startswith('first_funded_share_of_pooled'):
                    missing=~domain.first_liquidity_known
                elif metric.startswith('harmful_or_'):
                    missing=(~domain.liquidity_known)|domain.category.isin(['could_not_buy','unprobeable','v3_fot_incompatible','late_pool'])
                elif 'below_floor' in metric or metric.startswith('below_floor_by'):
                    missing=~domain.liquidity_known
                else:missing=pd.Series(False,index=domain.index)
                missing_weight=float(domain.loc[missing,'weight'].sum())
                initial_unknown_weight=float(pooled.loc[~pooled.first_liquidity_known,'weight'].sum())
                possible_funded_weight=weight+initial_unknown_weight
                row.update(n_unobserved=int(missing.sum()),unobserved_weight_share=missing_weight/weight if weight else np.nan,
                           identification_lower=row['share'],
                           identification_upper=min(1.,row['share']+missing_weight/weight) if weight and pd.notna(row['share']) else np.nan,
                           direction_rule='both raw reserves fell: withdrawal; quote fell and token rose: dumping; otherwise other; missing: unknown',
                           direction_limit='reserve-direction evidence only; positions exclude fees; no creator attribution',
                           initial_amount_scope='first positive event, not block end; Mint contributions where no saved Sync')
                row['unobserved_first_liquidity_pooled_weight']=initial_unknown_weight
                row['unobserved_first_liquidity_pooled_weight_share']=initial_unknown_weight/float(pooled.weight.sum()) if pooled.weight.sum() else np.nan
                if den=='observed_first_funded_pooled':
                    row['point_membership_scope']='observed initially funded selected pools; unknown first-liquidity membership excluded'
                    row['unknown_initial_membership_lower']=row['positive_weight']/possible_funded_weight if possible_funded_weight else np.nan
                    row['unknown_initial_membership_upper']=min(1.,(row['positive_weight']+missing_weight+initial_unknown_weight)/possible_funded_weight) if possible_funded_weight else np.nan
                else:
                    row['point_membership_scope']='denominator stated in denominator_definition'
                    row['unknown_initial_membership_lower']=np.nan
                    row['unknown_initial_membership_upper']=np.nan
                rows.append(row)
            measurable=g[g.measurable]
            reason=measurable.category_reason.fillna('')
            scopes={
                'unresolved_trade_outcome':measurable.category.isin(['could_not_buy','unprobeable','v3_fot_incompatible']),
                'could_not_buy':measurable.category.eq('could_not_buy'),
                'unprobeable':measurable.category.eq('unprobeable'),
                'unresolved_v4_non_native_quote_route':measurable.venue.eq('V4') & reason.str.contains('unsupported.*quote|non.*(?:eth|native).*quote|quote.*unsupported',case=False,regex=True),
                'no_positive_sell_quote':reason.eq('no_positive_sell_quote')}
            # Preserve exact reason composition, avoiding a guessed definition
            # if the simulator changes a route-error spelling.
            for label,outcome in scopes.items():
                rr=estimate(full,measurable,outcome,label,'measurable_pooled',year,venue,boot)
                rr['reason_weight_json']=json.dumps({str(k):float(v) for k,v in measurable.loc[outcome].groupby('category_reason').weight.sum().items()},sort_keys=True)
                dilution.append(rr)
            known=measurable[measurable.category.isin(['honeypot','high_tax','normal','owner_controlled'])]
            dilution.append(estimate(full,known,known.headline_trade_harm,'known_outcome_harm_share','measurable_known_trade_outcomes',year,venue,boot))
            small=pooled[pooled.liquidity_known & (pooled.quote_liquidity_eth>=1e-9) & (pooled.quote_liquidity_eth<.01)]
            # Labels at this floor are explicitly a noisy sensitivity; no
            # below-floor row silently enters the primary harm numerator.
            residual.append(dict(year=year,venue=venue,n_sampled=len(small),weight=float(small.weight.sum()),
                                 scope='1e9 <= quote wei < 1e16; raw trade labels in tiny-floor sensitivity; informative exclusion, not primary harm'))
    pd.DataFrame(rows).drop(columns=['period'],errors='ignore').to_csv(dest/'launch_liquidity_by_year_venue.csv',index=False)
    pd.DataFrame(dilution).drop(columns=['period'],errors='ignore').to_csv(dest/'unresolved_dilution_by_year_venue.csv',index=False)
    pd.DataFrame(residual).to_csv(dest/'residual_liquidity_support.csv',index=False)


def write_residual(tinyfloor,boot,dest):
    rows=[]
    for year,full in tinyfloor.groupby(tinyfloor.analysis_month.str[:4],sort=True):
        for venue,g in [('all',full)]+list(full.groupby('venue',sort=True)):
            cut=g[g.pool_ever & g.liquidity_known & g.quote_liquidity_eth.ge(1e-9) & g.quote_liquidity_eth.lt(.01)]
            for label,outcome in [('headline_trade_harm',cut.headline_trade_harm),
                                  ('unresolved',cut.category.isin(['could_not_buy','unprobeable','v3_fot_incompatible']))]:
                row=estimate(full,cut,outcome,label,'residual_1e9_to_1e16_wei',year,venue,boot)
                row['measurement_scope']='noisy residual-pool trade labels at technical floor; informative exclusion sensitivity, not primary harm'
                rows.append(row)
    pd.DataFrame(rows).drop(columns=['period'],errors='ignore').to_csv(dest/'residual_liquidity_harm.csv',index=False)
