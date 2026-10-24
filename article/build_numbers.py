#!/usr/bin/env python3
"""Offline paper build: saved evidence tables -> ledger, paper, and figures.

No RPC, provider calls, or executable repository imports. Restricted census rows
stay local; publication contains aggregates and independently verified examples.
The final build requires the completed launch-state dataset; preview data is refused.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import re
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
ARTICLE=ROOT/'article'
T2='data/tier2/v2/'
CHAINS=['ethereum','base','arbitrum','bsc','polygon','optimism']
HARM='headline_trade_harm'
COMPARISONS={'sim_vs_honeypot':'Simulator / honeypot.is','sim_vs_goplus':'Simulator / GoPlus','honeypot_vs_goplus':'honeypot.is / GoPlus'}
ENTRIES={}
CACHE={}

def read(path):
    if path not in CACHE: CACHE[path]=pd.read_csv(ROOT/path,keep_default_na=False)
    return CACHE[path].copy()

def one(d, **where):
    for k,v in where.items(): d=d[d[k].astype(str)==str(v)]
    if len(d)!=1: raise ValueError(f'Expected one row: {where}; got {len(d)}')
    return d.iloc[0]

def number(x):
    return float(x) if str(x) not in ('','nan','None') else math.nan

def pct(x, digits=1):
    x=number(x)
    return 'NA' if not math.isfinite(x) else f'{100*x:.{digits}f}%'

def decimal(x, digits=3):
    x=number(x)
    if math.isfinite(x) and abs(x)<.5*10**(-digits): x=0.
    return 'NA' if not math.isfinite(x) else f'{x:.{digits}f}'

def estimate(r):
    if str(r.get('identified','True')).lower()=='false' or str(r['share'])=='': return 'NA'
    if number(r.get('effective_n',math.inf))<30: return 'Not reported (effective support <30)'
    return f"{pct(r['share'])} [{pct(r['ci_low'])}, {pct(r['ci_high'])}]"

def count_estimate(r):
    if str(r.get('identified','True')).lower()=='false' or str(r.get('est_count',''))=='': return 'NA'
    if number(r.get('effective_n',math.inf))<30: return 'Not reported (effective support <30)'
    return f"{number(r['est_count']):,.0f} [{number(r['est_count_ci_low']):,.0f}, {number(r['est_count_ci_high']):,.0f}]"

def supported_pct(r, column='share', support_column='effective_n'):
    return 'Not reported (effective support <30)' if number(r.get(support_column,math.inf))<30 else pct(r[column])

def support(r):
    return f"{number(r['positive_effective_n']):.0f} / {number(r['denominator_effective_n']):.0f}"

def add(key,value,source,query,raw_value=None):
    if key in ENTRIES: raise ValueError('Duplicate key '+key)
    sources=source if isinstance(source,list) else [source]
    hashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources}
    rec={'value':str(value),'source_file':source,'query':query,'source_sha256':hashes}
    if raw_value is not None: rec['numeric_value']=raw_value
    ENTRIES[key]=rec
    return str(value)

def render_value(key):
    item=ENTRIES[key]; value=item['value']
    if isinstance(value,str) and re.fullmatch(r'\d+',value): return f'{int(value):,}'
    return str(value)

def site_count_numbers():
    """Saved domain-specific literal counts; upstream hashes checked at assembly."""
    source='data/site-counts.json'
    values=json.loads((ROOT/source).read_text())
    assert isinstance(values,dict) and values
    ENTRIES['site_counts']={'value':values,'source_file':source,
        'query':'saved JSON pointer count inventory; upstream input digests in data/release-projections.json; site rebuild independently binds each displayed domain',
        'source_sha256':{source:hashlib.sha256((ROOT/source).read_bytes()).hexdigest()}}

def table(key,headers,rows,source,query):
    def clean(v):
        value=str(v)
        return value.replace('|','&#124;').replace('\n',' ')
    s='\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(clean,r))+' |' for r in rows])
    add(key,s,source,query)

def rate_cell(r):
    rate=pct(r['rate']) if number(r['n_measurable'])>=30 else 'Not reported (n<30)'
    return f"{int(r['n_yes']):,}/{int(r['n_measurable']):,} ({rate})"

def chain_name(chain): return 'BNB Smart Chain' if chain=='bsc' else chain.title()

def static_numbers():
    source='data/matrix/prevalence_by_chain_rank.csv'; main=read(source)
    exsource='data/matrix/prevalence_ex_securities.csv'; ex=read(exsource)
    extra_source='data/matrix/prevalence_extras.csv'; extras=read(extra_source)
    sm='data/matrix/subsets.json'; subsets=json.loads((ROOT/sm).read_text())['subsets']
    for label,domain in [('n_universe','inventory'),('n_main','ranked'),('n_extras','extras')]:
        add(label,subsets[domain]['n_tokens'],sm,f'subsets.{domain}.n_tokens',subsets[domain]['n_tokens'])
    add('n_chains',len(subsets['inventory']['by_chain']),sm,'len(subsets.inventory.by_chain)',len(subsets['inventory']['by_chain']))
    add('n_security_exclusions',int(one(main,chain='all',rank_band='all_market_cap',feature='is_proxy').n_tokens)-int(one(ex,chain='all',rank_band='all_market_cap',feature='is_proxy').n_tokens),[source,exsource],'main token count minus securities-excluded token count')
    features={'proxy':'is_proxy','upgradeable':'upgradeable','upgrade_eoa':'upgrade_single_eoa','blocklist':'blocklist_capability','blocklist_union':'blocklist_any','fot':'fee_on_transfer_observed','share':'share_accounting_capability','drift':'balance_drift_7d','missing_transfer':'ret_missing_transfer','permissioned':'permissioned_transfer','fresh_reject':'transfer_reverts_fresh_contract','controlled':'controlled','ronin':'sanction_block_ronin'}
    summary=read('data/matrix/summary.csv')
    for label,f in features.items():
        for prefix,df,path,filters in [('main',main,source,{'chain':'all','rank_band':'all_market_cap'}),('inventory',summary,'data/matrix/summary.csv',{'dim':'all','chain':'all','rank_band':'all'})]:
            r=one(df,feature=f,**filters)
            for col in ['n_yes','n_measurable','rate']:
                v=number(r[col]); v=int(v) if col!='rate' else v
                add(f'{prefix}_{label}_{col}',pct(v) if col=='rate' else v,path,f'{filters}, feature={f}, column={col}',v)
    scope=[]
    for c in CHAINS:
        cells=[]
        for b in ['1-100','101-500','501-2000']:
            r=one(main,chain=c,rank_band=b,feature='is_proxy'); cells.append(r.n_tokens)
        r=one(extras,chain=c,feature='is_proxy'); scope.append([chain_name(c),*cells,r.n_tokens])
    table('universe_table',['Chain','Ranks 1–100','101–500','501–2000','Extras'],scope,[source,extra_source],'is_proxy rows; n_tokens by chain and band; extras separate')
    rows=[]
    d=main[(main.chain=='all')&(main.rank_band=='all_market_cap')&(main.measure_type=='d_xo_section')]
    for _,r in d.iterrows():
        e=one(ex,chain='all',rank_band='all_market_cap',feature=r.feature)
        title={'Tokens with blocklists':'Blocklist capability or tested-address rejection','Upgradable tokens':'Upgrade authority exists','Balance modifications outside transfers (observed drift)':'Observed balance drift','Share-accounting capability':'Share-accounting capability'}.get(r.feature,r.feature)
        rows.append([title,rate_cell(r),f"{pct(r.ci95_lo)}–{pct(r.ci95_hi)}",rate_cell(e)])
    table('prevalence_table',['Behavior / capability','Ranked: yes / measured (rate)','Descriptive Wilson 95% interval','Excluding securities: yes / measured (rate)'],rows,[source,exsource],'chain=all,rank_band=all_market_cap,measure_type=d_xo_section; separate observed/capability rows; ex-securities same features')
    core=['ret_missing_transfer','fee_on_transfer_observed','share_accounting_capability','balance_drift_7d','blocklist_any','upgrade_single_eoa']
    names=['Missing transfer return','Observed transfer fee','Share capability','Observed drift','Blocklist union','EOA upgrade path']
    rows=[]
    for c in CHAINS:
        for b in ['1-100','101-500','501-2000']:
            rr=[one(main,chain=c,rank_band=b,feature=f) for f in core]
            if not int(rr[0].n_tokens): continue
            rows.append([chain_name(c),b,*[rate_cell(r) for r in rr]])
    table('chain_rank_table',['Chain','Rank band',*names],rows,source,'per chain × rank band; feature-specific measured denominators; no extras')
    rows=[]
    for c in CHAINS:
        for tag,df in [('Ranked ≤500',main),('Ex securities',ex)]:
            rows.append([chain_name(c),tag,*[rate_cell(one(df,chain=c,rank_band='1-500',feature=f)) for f in ['is_proxy','upgrade_single_eoa','blocklist_any','share_accounting_capability']]])
    table('crosschain_table',['Chain','Domain','Proxy','EOA upgrade path','Blocklist union','Share capability'],rows,[source,exsource],'rank_band=1-500; matched rank depth with ex-securities sensitivity')
    rows=[]
    for f,name in zip(core,names):
        r=one(extras,chain='all',feature=f); rows.append([name,rate_cell(r)])
    table('extras_table',['Behavior / capability','Extras only: yes / measured (rate)'],rows,extra_source,'chain=all; convenience extras excluded from prevalence')
    tests=read('data/matrix/cross_chain_tests.csv'); rows=[]
    for f,name in [('ret_missing_transfer','Missing transfer return'),('false_on_failure','False on failure'),('approve_race_guard','Approval race guard'),('uint96_limit','Bounded-integer limit')]:
        r=one(tests,analysis_set='market_cap',chain_a='ethereum',chain_b='rest',feature=f)
        rows.append([name,f'{int(r.yes_a):,}/{int(r.n_measurable_a):,}',f'{int(r.yes_b):,}/{int(r.n_measurable_b):,}',f'{number(r.p_raw):.3g}',f'{number(r.p_holm):.3g}'])
        if f=='ret_missing_transfer':
            p=number(r.p_holm)
            add('missing_return_holm',f'{p:.3g}','data/matrix/cross_chain_tests.csv','analysis_set=market_cap,ethereum vs rest,feature=ret_missing_transfer,column=p_holm',p)
            result=(f'The Ethereum-versus-rest missing-transfer-return contrast is not significant after Holm correction (p={p:.3g}).' if p>=.05 else f'The missing-transfer-return contrast is significant at the deployment level (Holm-adjusted p={p:.3g}); dependence between deployments and shared issuers prevents a chain-population inference.')
            add('missing_return_inference',result,'data/matrix/cross_chain_tests.csv','analysis_set=market_cap,ethereum vs rest,ret_missing_transfer; compare saved p_holm to 0.05; preserve dependence caveat')
    table('crosschain_tests',['Feature','Ethereum','Rest','Raw Fisher p','Holm p'],rows,'data/matrix/cross_chain_tests.csv','market_cap ranks 1–500; Ethereum vs rest; saved feature×comparison Holm family')
    issuer=read('data/matrix/security_issuer_composition.csv')
    issuer=issuer[(issuer.scope=='market_cap')&(issuer.chain=='all')]
    count_source='data/matrix/b20_membership_counts.csv'; b20_counts=read(count_source)
    def b20_count(key): return int(one(b20_counts,metric=key).n)
    issuer.loc[issuer.issuer_family.eq('Coinbase B20'),'issuer_family']='Coinbase-listed B20 (API address join)'
    assert int(issuer.loc[issuer.issuer_family.str.startswith('Coinbase-listed'),'n_tokens'].sum())==b20_count('b20_paper_ranked_n')
    table('security_composition',['Family / listing basis','Deployments','Upgrade authority','Blocklist union','Share capability'],issuer[['issuer_family','n_tokens','upgradeable_yes','blocklist_any_yes','share_capability_yes']].values.tolist(),['data/matrix/security_issuer_composition.csv',count_source],'ranked,chain=all; Coinbase-listed membership privately checked against saved API address join; grouped counts, no issuance inference')
    for key in ['b20_coinbase_survey_n','b20_other_survey_n','b20_measured_universe_n','b20_paper_ranked_n','b20_paper_inventory_n']:
        val=b20_count(key);add(key,val,count_source,'saved independently checked API-address join count: '+key,val)
    tornado=json.loads((ROOT/'data/matrix/c19_verification.json').read_text())['tornado_total_overlap_outside']
    for k,v in zip(['tornado_total','tornado_overlap','tornado_only'],tornado): add(k,v,'data/matrix/c19_verification.json','tornado_total_overlap_outside',v)
    rows=[]
    for c in CHAINS: rows.append([chain_name(c),one(summary,dim='chain',chain=c,rank_band='all',feature='permissioned_transfer').n_yes])
    table('permissioned_chain_table',['Chain','Permissioned-transfer observations'],rows,'data/matrix/summary.csv','dim=chain,feature=permissioned_transfer,column=n_yes; inventory counts')
    # Current-state, aligned, post-fix validation. Older versions are sensitivity only.
    hs='data/validate/round3/headline.csv'; h=read(hs)
    prep_source='data/validate/round2/prepare_meta.json'; prep=json.loads((ROOT/prep_source).read_text())
    for key,field in [('validation_cohort_n','n_subset'),('validation_original_pairs_n','n_pairs')]:add(key,prep[field],prep_source,field,prep[field])
    timing_source='data/validate/round3/timing_counts.csv'; timing=read(timing_source)
    add('validation_ethereum_n',int(timing.loc[timing.chain.astype(int)==1,'n'].sum()),timing_source,'sum(n) where chain=1; main current-state Ethereum frame')
    h=h[(h.dataset=='round3_v2')&(h.stratum=='all')&(h.lag_group=='all')&(h.weighting=='unweighted')]
    rows=[]
    for _,r in h.iterrows():
        matrix=f'[[{r.n00},{r.n01}],[{r.n10},{r.n11}]]'
        rows.append([COMPARISONS[r.comparison],r.label.replace('_',' '),r.n,matrix,f'{pct(r.agreement)} [{pct(r.wilson_ci_low)}, {pct(r.wilson_ci_high)}]',f'{decimal(r.kappa)} [{decimal(r.kappa_ci_low)}, {decimal(r.kappa_ci_high)}]',decimal(r.positive_specific_agreement),decimal(r.negative_specific_agreement),decimal(r.pabak)])
        for col in ['n','n00','n01','n10','n11','agreement','kappa','positive_specific_agreement']:
            v=number(r[col]);add(f'validation_{r.comparison}_{r.label}_{col}',pct(v) if col=='agreement' else decimal(v) if col in ['kappa','positive_specific_agreement'] else int(v),hs,f'dataset=round3_v2,stratum=all,lag_group=all,weighting=unweighted,comparison={r.comparison},label={r.label},column={col}',v)
    table('validation_table',['Comparison','Task','n','[[00,01],[10,11]]','Agreement [Wilson 95%]','κ [bootstrap 95%]','Positive specific','Negative specific','PABAK'],rows,hs,'round3_v2; all/unweighted/all; raw labels, no adjudicated accuracy')
    vs='data/validate/round3/summary.csv'; allv=read(vs);rows=[]
    for stratum in ['liquid','thin','dust','top']:
        for pair in ['sim_vs_honeypot','sim_vs_goplus']:
            r=one(allv,dataset='round3_v2',stratum=stratum,comparison=pair,label='honeypot',lag_group='all',weighting='unweighted')
            rows.append([stratum,COMPARISONS[pair],r.n,f'{r.n00}/{r.n01}/{r.n10}/{r.n11}',(pct(r.agreement) if number(r.n)>=30 else 'Not reported (n<30)'),(f'{decimal(r.kappa)} [{decimal(r.kappa_ci_low)}, {decimal(r.kappa_ci_high)}]' if number(r.n)>=30 else 'Not reported (n<30)'),(decimal(r.positive_specific_agreement) if number(r.n)>=30 else 'Not reported (n<30)')])
    table('validation_strata',['Stratum','Comparison','n','00/01/10/11','Agreement','κ [95%]','Positive specific'],rows,vs,'round3_v2, honeypot task, unweighted/all lag; original selection strata')
    rows=[]
    for dataset in ['round2_original','round2_v2','round3_v2']:
        for lag in ['pair_aligned_le30m','pair_stale_gt2h']:
            for pair in ['sim_vs_honeypot','sim_vs_goplus']:
                r=one(allv,dataset=dataset,stratum='all',comparison=pair,label='honeypot',lag_group=lag,weighting='unweighted')
                rows.append([{'round2_original':'Earlier classifier','round2_v2':'Earlier state, revised rules','round3_v2':'Fresh state, revised rules'}[dataset],lag.replace('pair_aligned_le30m','≤30 min').replace('pair_stale_gt2h','>2 h'),COMPARISONS[pair],r.n,(pct(r.agreement) if number(r.n)>=30 else 'Not reported (n<30)'),(decimal(r.kappa) if number(r.n)>=30 else 'Not reported (n<30)')])
    table('validation_lag',['Observation / rules','Pair lag','Comparison','n','Agreement','κ'],rows,vs,'all strata,honeypot,unweighted; aligned versus stale; absent cells remain NA')
    calibrated=allv[(allv.dataset=='round3_v2')&(allv.stratum=='all')&(allv.lag_group=='all')&(allv.label=='honeypot')&(allv.weighting=='new_eth_candidates_calibrated')]
    table('validation_calibrated',['Comparison','Paired n','Kish support','Weighted agreement [bootstrap 95%]','κ [bootstrap 95%]','Positive specific','Frame-weight coverage'],[[COMPARISONS[r.comparison],r.n,decimal(r.kish_n_eff,1),(f'{pct(r.agreement)} [{pct(r.agreement_bootstrap_ci_low)}, {pct(r.agreement_bootstrap_ci_high)}]' if number(r.kish_n_eff)>=30 else 'Not reported (effective support <30)'),(f'{decimal(r.kappa)} [{decimal(r.kappa_ci_low)}, {decimal(r.kappa_ci_high)}]' if number(r.kish_n_eff)>=30 else 'Not reported (effective support <30)'),(decimal(r.positive_specific_agreement) if number(r.kish_n_eff)>=30 else 'Not reported (effective support <30)'),pct(r.target_weight_coverage)] for _,r in calibrated.iterrows()],vs,'round3_v2,all strata/all lag,honeypot,new_eth_candidates_calibrated; not design-unbiased agreement')
    v3s='data/verify/v3/recall.csv'; v3=read(v3s);rows=[]
    for venue in ['uniswap_v3','uniswap_v4']:
        for metric in ['precision','negative_predictive_value']:
            q=v3[(v3.venue==venue)&(v3.task=='honeypot')&(v3.metric==metric)]
            if q.empty: continue
            r=q.iloc[0];small=number(r.n_trials)<30
            rows.append([venue.replace('uniswap_','').upper(),metric.replace('_',' '),f'{r.n_success}/{r.n_trials}','Not reported (trials <30)' if small else pct(r.estimate),'Not reported (trials <30)' if small else f'[{pct(r.ci_low)}, {pct(r.ci_high)}]'])
    table('blind_v3_table',['Venue','Honeypot-label task: sample metric','Success / trials','Estimate','Wilson 95%'],rows,v3s,'honeypot-label task only; enriched old-label sample; unweighted case-control recall omitted; metrics with <30 trials withheld')
    bs='data/validate/round2/b20_router_counts.csv'; b20=read(bs)
    total=int(b20.n.sum())
    good=int(b20.loc[b20.buy_status.eq('success') & b20.sell_status.eq('success'),'n'].sum())
    for k,v in [('b20_sample_n',total),('b20_refuted',good),('b20_unresolved',total-good)]:add(k,v,bs,'sum(n) by saved independent router buy/sell outcomes',v)
    claims='data/verify/claims/verdicts.csv'; cv=read(claims)
    policies=json.loads(one(cv,claim_id='N011').detail.split('policies: ',1)[1])
    for k,v in [('policy_mirror_n',policies['5']['known']),('policy_three_denied',policies['5']['denied']),('policy_fourth_denied',policies['122']['denied'])]:add(k,v,claims,'N011 saved policies mirror membership counts',v)
    # Adjudication is a matched scope comparison, never token-level accuracy.
    adj='data/validate/round2/adjudicator_counts.csv'; counts=read(adj)
    total=int(counts.n.sum())
    agree=int(counts.loc[counts.first_verdict.eq(counts.blind_verdict),'n'].sum())
    add('adjudicator_result',f'{agree}/{total} ({pct(agree/total)})',adj,'sum(n) where first_verdict=blind_verdict; matched scope counts')
    fs='data/verify/dust/fee_activation.json'; fee=json.loads((ROOT/fs).read_text())
    add('fee_activation_block',f"{fee['activation_block']:,}",fs,'activation_block',fee['activation_block'])
    form='data/verify/dust/fee_mint_formula.csv'; f=read(form)
    add('fee_formula_checks',len(f),form,'row count; independent integer-formula checks',len(f))
    core_source='data/verify/claims/checks/core_ethereum.json'
    core=json.loads((ROOT/core_source).read_text()); named=[]
    for symbol in ['USDC','cbBTC','BUIDL','PYUSD']:
        item=next(r for r in core if r.get('symbol')==symbol)
        check=next(r for r in item['upgrade_tests'] if r.get('verified'))
        sender=check['sender']; assert item['accounts'][sender]['code']=='0x'
        named.append([symbol,check['signature'],sender,check['calls'][0]['block'],'Authority succeeds; stranger reverts'])
        if symbol=='PYUSD':
            owner='0x'+item['calls']['owner()']['result'][-40:]
            assert owner!=sender and item['accounts'][owner]['code']=='0x'
            add('pyusd_owner',owner,core_source,'PYUSD calls.owner().result, decoded address')
            add('pyusd_upgrade_admin',sender,core_source,'PYUSD verified upgrade_tests.sender; differs from owner()')
    table('named_upgrade_table',['Ethereum deployment','Reproduced method','Terminal EOA','Pinned block','Differential result'],named,core_source,'All four specified issuer examples with independent verified caller/stranger differential and empty-code check')
    add('b20_survey_n',b20_count('b20_survey_n'),count_source,'saved convenience survey frame count',b20_count('b20_survey_n'))
    for key,value in [('b20_trade_block',int(b20.iloc[0]['block']))]:add(key,value,bs,'saved block; router case-study observations')
    policy_block=int(re.search(r'Base block (\d+)',one(cv,claim_id='N011').detail)[1])
    add('b20_policy_block',policy_block,claims,'N011 detail, independently pinned Base policy block')
    cs='data/census/monthly_counts.csv'; census=read(cs)
    nonlp=census[census.series=='excl_lp'].copy(); peak=nonlp.loc[pd.to_numeric(nonlp.n_est).idxmax()]
    add('census_peak',f"{peak.month}: {number(peak.n_est):,.0f} [{number(peak.ci_low):,.0f}, {number(peak.ci_high):,.0f}]",cs,'series=excl_lp; row with maximum n_est; month, n_est, ci_low, ci_high rounded to tokens')
    add('census_months',census.month.nunique(),cs,'count distinct month',int(census.month.nunique()))
    add('sample_frame_n',int(read('data/census/frame_stats.csv').iloc[0].n_rows),'data/census/frame_stats.csv','saved frame row count')
    live='data/census/validation_sampling_months.csv'; live_rows=read(live)
    covered=live_rows.covered_srs.astype(str).str.lower().eq('true').sum()
    add('census_live_coverage',f'{covered}/{len(live_rows)} ({pct(covered/len(live_rows))})',live,'sum(covered_srs)/row count; two series per month')
    add('census_live_relative_error',f'{100*pd.to_numeric(live_rows.rel_err).mean():.1f}%',live,'mean(rel_err), relative error, not percentage points')
    mcsource='data/census/evidence/estimator_sim.csv'; mc=read(mcsource)
    mc_all=mc[(mc.n.astype(int)==1000)&(mc.est=='dec')&(mc.series=='all_')]
    mc_nonlp=mc[(mc.n.astype(int)==1000)&(mc.est=='srs')&(mc.series=='exlp')]
    add('census_mc_coverage',pct(pd.to_numeric(mc_all.coverage).mean()),mcsource,'n=1000,est=dec,series=all_; mean per-month Monte Carlo coverage')
    add('census_nonlp_mc_coverage',pct(pd.to_numeric(mc_nonlp.coverage).mean()),mcsource,'n=1000,est=srs,series=exlp; mean per-month Monte Carlo coverage')
    rugsource='data/verify/rug/summary.json'; rug=json.loads((ROOT/rugsource).read_text())
    add('lp_blind_n',rug['rows'],rugsource,'rows')
    for label,val in rug['counts'].items():add('lp_blind_'+label,val,rugsource,'counts.'+label)
    legacy_dust_source='data/verify/dust/venue_by_year.csv'; legacy=read(legacy_dust_source)
    legacy=legacy[legacy.selected_venue=='all']
    table('superseded_dust_screen',['Year','Old tiny-floor measurable sample','Old sale/tax-positive sample'],[[r.year,r.n_measurable,r.positive_n] for _,r in legacy.iterrows()],legacy_dust_source,'Superseded +7200 selected-pool design; sample counts only because this table lacks effective-support diagnostics; not primary estimand or attributed rugs')
    for name,path in [('golden','data/probe-golden/report.csv')]:
        g=read(path);passed=g.match.astype(str).str.lower().eq('true').sum()
        add('golden_result',f'{passed}/{len(g)} ({pct(passed/len(g))})',path,'count(match=true)/row count')


def tier2_numbers():
    # Completed-snapshot verification replaces the internal readiness gate.
    if not (ROOT/T2/'annual_shares.csv').exists(): raise RuntimeError('Completed annual estimates absent')
    verification=json.loads((ROOT/T2/'verification.json').read_text())
    if not verification.get('complete'): raise RuntimeError('Launch-state evidence verification is incomplete')
    if verification.get('rules_version')!='3.3': raise RuntimeError('Final paper requires rules 3.3')
    bootstrap=read(T2+'bootstrap_check.csv')
    table('bootstrap_checks',['Synthetic scenario','Domain','First-stage replicates','Bootstrap draws','Empirical coverage'],[[r.scenario.replace('_',' '),r.domain,r.replicates,r.bootstrap_replicates,pct(r.empirical_coverage)] for _,r in bootstrap.iterrows()],T2+'bootstrap_check.csv','all seeded synthetic coverage tests; synthetic_only=true; not measured Ethereum coverage')
    a=read(T2+'annual_shares.csv'); yearly=sorted(a.year.astype(str).unique())
    rows=[]
    for y in yearly:
        r=one(a,year=y,denominator='measurable_pooled',label=HARM)
        ar=one(a,year=y,denominator='headline_excl_lp',label=HARM)
        floors=[one(read(T2+'sensitivity.csv'),frequency='annual',period=y,denominator='measurable_pooled',scenario=s) for s in ['floor_1e9_wei','floor_0.1_eth']]
        bound=one(read(T2+'bounds.csv'),frequency='annual',period=y,denominator='measurable_pooled')
        mc=one(read(T2+'misclass_adjusted.csv'),year=y,denominator='measurable_pooled')
        low_support=number(r.effective_n)<30
        title=y+(' Jan–Sep' if y=='2026' else '')
        cells=[title,estimate(r),f'{pct(bound.lower)}–{pct(bound.upper)} / {pct(bound.all_unresolved_upper)}' if not low_support else 'Not reported (effective support <30)',estimate(ar)]
        rows.append(cells)
        for den,rr in [('measurable',r),('all_new',ar)]:
            for col in ['share','ci_low','ci_high','denominator_effective_n','positive_effective_n']:
                v=number(rr[col]);add(f'v2_{y}_{den}_{col}',(supported_pct(rr,col) if col in ['share','ci_low','ci_high'] else f'{v:.0f}'),T2+'annual_shares.csv',f'year={y},denominator={rr.denominator},label={HARM},column={col}',v if math.isfinite(v) else None)
        add('v2_'+y+'_misclass_range',f'{pct(mc.adjusted_low)}–{pct(mc.adjusted_high)}',T2+'misclass_adjusted.csv',f'year={y},measurable_pooled,conditional adjusted_low/high')
        add('v2_'+y+'_strict_range',f'{pct(mc.strict_identified_low)}–{pct(mc.strict_identified_high)}',T2+'misclass_adjusted.csv',f'year={y},measurable_pooled,strict_identified_low/high')
        for label in ['no_meaningful_liquidity','could_not_buy','late_pool','unprobeable']:
            rr=one(a,year=y,denominator='pooled_tokens',label=label)
            add(f'v2_{y}_{label}_pooled',pct(rr['share']),T2+'annual_shares.csv',f'year={y},pooled_tokens,label={label},column=share',number(rr['share']) if str(rr['share']) else None)
    table('v2_annual_table',['Creation year','Primary ≥0.01 ETH [approx. 95%]','Buy-failure range / all-unknown upper','All-new [approx. 95%]'],rows,[T2+p for p in ['annual_shares.csv','bounds.csv']],'annual headline at +300; real V4 principal floor; rates with effective support <30 withheld; 2025 all-new excludes February')
    counts=[]; diagnostics=[]
    for y in yearly:
        r=one(a,year=y,denominator='measurable_pooled',label=HARM)
        below=one(a,year=y,denominator='pooled_tokens',label='no_meaningful_liquidity')
        mc=one(read(T2+'misclass_adjusted.csv'),year=y,denominator='measurable_pooled')
        floors=[one(read(T2+'sensitivity.csv'),frequency='annual',period=y,denominator='measurable_pooled',scenario=ss) for ss in ['floor_1e9_wei','floor_0.1_eth']]
        diagnostics.append([y,support(r),supported_pct(floors[0]),supported_pct(floors[1]),f'{pct(mc.adjusted_low)}–{pct(mc.adjusted_high)}' if number(r.effective_n)>=30 else 'Not reported (effective support <30)'])
        if y in ['2024','2025','2026']:
            counts.append([y,count_estimate(r),count_estimate(below)])
            for tag,rr in [('harm',r),('below_floor',below)]:
                for col in ['est_count','est_count_ci_low','est_count_ci_high']:
                    val=number(rr[col]);add(f'v2_{y}_{tag}_{col}',f'{val:,.0f}',T2+'annual_shares.csv',f'year={y},denominator={rr.denominator},label={rr.label},column={col}; expansion-weight count and saved sampling interval; no separate census-count propagation in this domain',val)
    table('v2_count_table',['Year','Estimated sale/tax-harm tokens [approx. 95%]','Estimated below-floor pooled tokens [approx. 95%]'],counts,T2+'annual_shares.csv','2024–2026 expansion-weight token counts and saved approximate sampling count intervals; overlapping outcomes are not added')
    table('v2_diagnostic_table',['Year','Effective positive / denominator n','Tiny floor 10⁻⁹ ETH','All-venue ≥0.1 ETH floor','Conditional classification envelope'],diagnostics,[T2+p for p in ['annual_shares.csv','sensitivity.csv','misclass_adjusted.csv']],'appendix only; rates below effective support 30 withheld; classification envelopes are not confidence intervals')
    s=read(T2+'sensitivity.csv'); rows=[]
    for y in ['2021','2024','2025','2026']:
        for scenario in ['primary','floor_1e9_wei','floor_0.1_eth','virtual_V4_liquidity','tax_gt10_strict','tax_ge5','LP_fee_as_tax','legacy_selected_pool_plus7200','observed_block_tail_design_effect_inflation']:
            r=one(s,frequency='annual',period=y,denominator='measurable_pooled',scenario=scenario)
            rows.append([y,{'primary':'Launch +300, real-V4 ≥0.01 ETH, tax ≥10%','floor_1e9_wei':'All-venue tiny floor 10⁻⁹ ETH','floor_0.1_eth':'All-venue ≥0.1 ETH floor','virtual_V4_liquidity':'Virtual-V4 ≥0.01 ETH floor (sensitivity)','tax_gt10_strict':'Tax >10%','tax_ge5':'Tax ≥5%','LP_fee_as_tax':'Include core LP fee','legacy_selected_pool_plus7200':'Legacy pool +7200; selection also differs','observed_block_tail_design_effect_inflation':'Observed block-tail CI inflation'}[scenario],estimate(r)])
    table('v2_sensitivity_table',['Year','Scenario','Share [approx. 95%]'],rows,T2+'sensitivity.csv','annual measurable domain, primary/tax/fee/legacy/tail sensitivities; legacy is not pure timing')
    floor_points={}
    for y in ['2025','2026']:
        for scenario in ['primary','virtual_V4_liquidity']:
            floor_points[(y,scenario)]=one(s,frequency='annual',period=y,denominator='measurable_pooled',scenario=scenario)
    description='; '.join(f"{y}: real {supported_pct(floor_points[(y,'primary')])}, virtual {supported_pct(floor_points[(y,'virtual_V4_liquidity')])}" for y in ['2025','2026'])
    real_change=number(floor_points[('2026','primary')]['share'])-number(floor_points[('2025','primary')]['share'])
    virtual_change=number(floor_points[('2026','virtual_V4_liquidity')]['share'])-number(floor_points[('2025','virtual_V4_liquidity')]['share'])
    conclusion=' The 2025–2026 point ordering reverses between these liquidity definitions.' if real_change*virtual_change<0 else ' This comparison isolates the sensitivity of eligibility to the V4 liquidity definition.'
    add('v21_real_virtual_comparison',description+'.'+conclusion,T2+'sensitivity.csv','annual measurable_pooled,2025/2026,primary versus virtual_V4_liquidity; compare signed year-to-year point changes')
    v=read(T2+'venue_by_year.csv');m=read(T2+'method_by_year.csv');std=read(T2+'standardized.csv');rows=[]
    tokens=read(T2+'cohort_weights.csv')
    active=tokens[~((tokens.frame_basis=='first_pool_month')&(tokens.analysis_month<'2025-02'))]
    for y in yearly:
        total=number(one(a,year=y,denominator='measurable_pooled',label=HARM).frame_denominator_weight)
        cells=[]
        for venue in ['V1/V2','V3','V4']:
            rr=v[(v.year.astype(str)==y)&(v.denominator=='measurable_pooled')&(v.venue==venue)]
            if rr.empty: cells.append('NA (no support)');continue
            r=rr.iloc[0]; cells.append(f"{supported_pct(r)} ({pct(number(r.frame_denominator_weight)/total) if total else 'NA'} of domain)")
        method=[]
        for g in ['direct','factory-launcher']:
            rr=m[(m.year.astype(str)==y)&(m.denominator=='measurable_pooled')&(m.creation_method_group==g)]
            method.append(supported_pct(rr.iloc[0]) if not rr.empty else 'NA')
        st=one(std,year=y)
        only=active[active.analysis_month.str.startswith(y)&active.selected_dex.eq('uniswap_v2')&active.measurable]
        denom=only.weight.sum(); v2only=only.loc[only.headline_trade_harm,'weight'].sum()/denom if denom else math.nan
        positives=only[only.headline_trade_harm]
        def kish(group):return group.weight.sum()**2/group.weight_sq.sum() if len(group) else 0.
        only_support=min(kish(only),kish(positives))
        add(f'v2_{y}_uniswap_v2_only',pct(v2only) if only_support>=30 else 'Not reported (effective support <30)',T2+'cohort_weights.csv',f'active creation year={y},selected_dex=uniswap_v2,measurable=true; weighted headline / domain; unrounded support >=30 required',float(v2only) if math.isfinite(v2only) else None)
        add(f'v2_{y}_uniswap_v2_only_support',f'{only_support:.2f}',T2+'cohort_weights.csv',f'active creation year={y},selected_dex=uniswap_v2,measurable=true; minimum positive/denominator Kish support',float(only_support))
        rows.append([y,*cells,*method,estimate(st)+f"; minimum venue support {number(st.effective_n):.1f}"])
    table('v2_composition_table',['Year','V1/V2 + Sushi rate (mix)','V3 rate (mix)','V4 rate (mix)','Direct deploys','Factory / launcher','Fixed 2024 mix [approx. 95%] / support'],rows,[T2+'venue_by_year.csv',T2+'method_by_year.csv',T2+'standardized.csv',T2+'annual_shares.csv',T2+'cohort_weights.csv'],'annual measurable domain; venue weights relative to measurable frame mass; method-specific ratios, all-NA unknown column omitted; standardized saved reference mix and saved CI withheld below support 30; literal Uniswap-V2-only kept as separate ledger scalar')
    mix=json.loads(std.iloc[0].reference_mix_json)
    add('v2_reference_mix',', '.join(f'{k}: {pct(v)}' for k,v in mix.items()),T2+'standardized.csv','reference_mix_json; fixed 2024 measurable venue weights')
    rows=[]
    for y in yearly:
        cells=[y]
        for label in ['no_meaningful_liquidity','could_not_buy','late_pool','unprobeable']:
            cells.append(supported_pct(one(a,year=y,denominator='pooled_tokens',label=label)))
        rows.append(cells)
    table('v2_excluded_table',['Year','Below meaningful-liquidity floor','Could not buy','Late pool','Other unprobeable'],rows,T2+'annual_shares.csv','descriptive expansion-weighted shares of all pooled tokens, not scam prevalence')
    q=read(T2+'quarterly_shares.csv');q=q[(q.denominator=='measurable_pooled')&(q.label==HARM)&(q.quarterly_publishable.astype(str).str.lower()=='true')]
    table('v2_quarter_table',['Quarter','Measurable share [approx. 95%]','Effective positive / denominator n'],[[r.quarter,estimate(r),support(r)] for _,r in q.iterrows()],T2+'quarterly_shares.csv','measurable headline; quarterly_publishable=true (support ≥30 and required frame coverage)')
    bm=read(T2+'blind_metrics.csv')
    table('v2_blind_era_table',['Era','Venue','Honeypot-or-high-tax task: sample metric','Success / trials','Estimate [Wilson 95%]','Outside floor / unknown'],[[r.era,r.venue,r.metric.replace('recall_sample_conditional','sample recall (unweighted)').replace('specificity_sample_conditional','sample specificity (unweighted)').replace('_',' '),f'{r.n_success}/{r.n_trials}',(f'{pct(r.estimate)} [{pct(r.ci_low)}, {pct(r.ci_high)}]' if number(r.n_trials)>=30 else 'Not reported (trials <30)'),r.n_unknown_or_outside_floor] for _,r in bm.iterrows()],T2+'blind_metrics.csv','all era×venue sample-conditional harm metrics; duplicate legacy state units excluded')
    timing=read(T2+'offset_transition_by_year.csv')
    transition_source=T2+'transition_weights.csv'
    transitions=read(transition_source)
    def timing_support(year):
        cut=transitions[transitions.analysis_month.str.startswith(str(year))&transitions.pool_matches&transitions.legacy_state_is_later&transitions.legacy_liquidity_known&transitions.new_harm]
        def kish(group):return group.weight.sum()**2/group.weight_sq.sum() if len(group) else 0.
        return min(kish(cut),kish(cut[cut.legacy_below_0_01_eth.astype(str).str.lower().eq('true')]))
    table('v2_timing_transitions',['Year','Comparable same-pool observations','Early-harm observations','Later below floor / early harm','Effective transition support','Early-harm weight later below floor'],[[r.year,r.n_comparable_known_later_state,r.n_early_harm,f'{r.n_early_harm_later_below_floor}/{r.n_early_harm}',f'{timing_support(r.year):.1f}',(pct(r.share_early_harm_later_below_floor) if timing_support(r.year)>=30 else 'Not reported (effective support <30)')] for _,r in timing.iterrows()],[T2+'offset_transition_by_year.csv',transition_source],'same pool; strictly later legacy state with known liquidity; descriptive transition, not rug attribution or pure offset experiment; minimum positive/denominator Kish support')
    tr=one(timing,year='2026')
    add('v2_2026_early_harm_later_below_floor',(pct(tr.share_early_harm_later_below_floor) if timing_support(2026)>=30 else 'Not reported (effective support <30)'),[T2+'offset_transition_by_year.csv',transition_source],'year=2026; share_early_harm_later_below_floor; matched subset, support >=30 only')
    origin=read(T2+'origin_counts.csv')
    add('origin_n',int(origin.n.sum()),T2+'origin_counts.csv','sum(n) of seeded wrapper-revert origin sample',int(origin.n.sum()))
    counts=origin.groupby('origin').n.sum()
    table('origin_table',['Diagnostic failure origin','Sampled wrapper-revert cases'],[[k.replace('_',' '),int(v)] for k,v in counts.items()],T2+'origin_counts.csv','group by origin; sum(n); deterministic seeded TRANSFER_FROM_FAILED audit; no per-token publication')
    # Overall below-floor share is descriptive, not an annual-average ratio.
    pooled=active[active.pool_ever.astype(bool)]
    cat='base_category' if 'base_category' in pooled else 'category'
    low=pooled.loc[pooled[cat]=='no_meaningful_liquidity','weight'].sum()/pooled.weight.sum()
    add('v2_overall_below_floor',pct(low),T2+'cohort_weights.csv','sum(active pooled weight with no_meaningful_liquidity)/sum(active pooled weight); remove overlapping pre-February row',float(low))
    add('v2_pooled_n',int(pooled.n.sum()),T2+'cohort_weights.csv','sum(n) for active pooled groups',int(pooled.n.sum()))


def revision_numbers():
    """Rules-3.3 real-liquidity, coverage and transport-scenario tables."""
    ls=T2+'launch_liquidity_by_year_venue.csv'; liq=read(ls)
    rows=[]; combined=[]
    for year in sorted(liq.year.astype(str).unique()):
        for venue in ['all','V1/V2','V3','V4']:
            g=liq[(liq.year.astype(str)==year)&liq.venue.eq(venue)]
            if g.empty: continue
            def metric(name): return one(g,metric=name)
            measured=metric('first_liquidity_measured_share_of_pooled')
            funded=metric('first_funded_share_of_pooled_observed_lower')
            lost=metric('below_floor_by300_given_first_funded')
            withdrawn=metric('withdrawal_given_first_funded_then_below')
            rows.append([year,'All' if venue=='all' else venue,supported_pct(measured),supported_pct(funded),estimate(lost),supported_pct(withdrawn)])
            joint=metric('harmful_or_below_floor_at300_share_of_pooled_observed_lower')
            emptied=metric('harmful_or_emptied_within_hour_given_first_funded')
            combined.append([year,'All' if venue=='all' else venue,estimate(joint),pct(joint.identification_upper) if number(joint.effective_n)>=30 else 'Not reported',estimate(emptied),count_estimate(lost)])
            if year in ['2024','2025','2026']:
                tag='all' if venue=='all' else venue.lower().replace('/','')
                for name,r in [('harm_or_below',joint),('harm_or_emptied',emptied),('lost_given_funded',lost)]:
                    val=number(r['share'])
                    add(f'v21_{year}_{tag}_{name}',supported_pct(r),ls,f'year={year},venue={venue},metric={r.metric}; column=share; descriptive, not attributed harm',val if math.isfinite(val) else None)
    table('v21_first_liquidity_table',['Year','Venue','First state measured / pooled','Initially ≥0.01 ETH / pooled (lower)','Below floor at +300 / initially funded [approx. 95%]','Both reserves fell / funded then depleted'],rows,ls,'year×venue; saved first-state coverage/funding and sub-hour loss; unknown initial states explicit; supported rates only')
    table('v21_combined_table',['Year','Venue','Harm or below floor / pooled [approx. 95%]','Unknown-state upper','Harmful or emptied / initially funded [approx. 95%]','Estimated initially funded then depleted [approx. 95%]'],combined,ls,'year×venue; pooled combined description and initially-funded combined description are distinct; expansion-weight depletion counts use saved approximate sampling intervals; not rug attribution')
    ts=T2+'below_origin_stats.csv'; stats=read(ts); rows=[]
    for _,r in stats.iterrows():
        mass=float(r.weight);eff=mass**2/float(r.weight_sq) if mass else 0
        def share(column):
            positive=float(r[column]);squares=float(r[column+'_sq'])
            positive_eff=positive**2/squares if squares else 0
            if min(eff,positive_eff)<30:return 'Not reported (effective support <30)'
            return pct(positive/mass) if mass else 'NA'
        median=number(r.median_first_eth)
        rows.append([int(r.year),f'{int(r.n):,}',share('known_weight') if eff>=30 else 'Not reported',share('initial_ge001_weight') if eff>=30 else 'Not reported',share('initial_ge1_weight') if eff>=30 else 'Not reported',f'{median:.3g} ETH' if eff>=30 and math.isfinite(median) else 'NA',share('withdrawal_weight') if eff>=30 else 'Not reported'])
    table('v21_v2_below_origin_table',['Year','Sampled below-floor V2/Sushi pools','First quote known','Initially ≥0.01 ETH','Initially ≥1 ETH','Weighted median first quote (known)','Both reserves fell'],rows,ts,'active selected uniswap_v2/sushiswap rows with base_category=no_meaningful_liquidity; percentages over all below-floor weight; median over known first quotes; no publication of token rows')
    cs=T2+'blind_coverage_by_year.csv'; coverage=read(cs)
    ps=T2+'pooled_assumption_ranges.csv'; pooled=read(ps)
    rows=[]
    for _,c in coverage.iterrows():
        r=one(pooled,year=c.year,denominator='measurable_pooled')
        headline=one(read(T2+'annual_shares.csv'),year=c.year,denominator='measurable_pooled',label=HARM)
        scenario=f'{pct(r.pooled_assumption_low)}–{pct(r.pooled_assumption_high)}' if number(headline.effective_n)>=30 else 'Not reported (effective support <30)'
        upper=pct(r.all_unresolved_upper) if number(headline.effective_n)>=30 else 'Not reported (effective support <30)'
        rows.append([c.year,pct(c.weight_share_cells_without_any_blind_check),pct(c.weight_share_cells_with_jointly_decidable_check),pct(c.weight_share_cells_with_same_year_blind_check),scenario,upper])
        for suffix,col in [('unverified_cell_share','weight_share_cells_without_any_blind_check'),('decidable_cell_share','weight_share_cells_with_jointly_decidable_check'),('sameyear_cell_share','weight_share_cells_with_same_year_blind_check')]:
            val=number(c[col]);add(f'v21_{c.year}_{suffix}',pct(val),cs,f'year={c.year}; column={col}; domain-weight cell coverage, not individual replay fraction',val if math.isfinite(val) else None)
        add(f'v21_{c.year}_pooled_assumption_range',f'{pct(r.pooled_assumption_low)}–{pct(r.pooled_assumption_high)}',ps,f'year={c.year},measurable_pooled; pooled_assumption_low/high; assumes cross-cell transport, not CI')
    table('v21_coverage_table',['Year','No blind check in cell','Jointly decidable cell coverage','Same-year cell coverage','Pooled-assumption range','Plus all unresolved: upper'],rows,[cs,ps],'measurable-domain weight coverage and annual pooled-transport classification scenarios; separate unknown endpoint; not a confidence interval')
    ds=T2+'unresolved_dilution_by_year_venue.csv'; dilution=read(ds)
    descriptions=[]
    for year in ['2024','2025','2026']:
        for key,metric in [('unresolved_domain_share','unresolved_trade_outcome'),('known_outcome_harm_share','known_outcome_harm_share')]:
            r=one(dilution,year=year,venue='all',metric=metric);val=number(r['share'])
            add(f'v21_{year}_{key}',supported_pct(r),ds,f'year={year},venue=all,metric={metric}; column=share; known-outcome ratio is sensitivity',val)
            if metric=='unresolved_trade_outcome' and year in ['2024','2026']:
                rate=pct(val) if number(r.effective_n)>=30 else f'{int(r.n_positive)}/{int(r.n_sampled)} sampled states (weighted rate withheld: effective support {number(r.effective_n):.1f})'
                descriptions.append(f'{year}: {rate}')
    add('v21_unresolved_comparison','; '.join(descriptions),ds,'venue=all,year=2024/2026,metric=unresolved_trade_outcome; rate if effective support >=30, otherwise observed sample counts and support')
    for key,metric in [('non_native_quote_unknown_n','unresolved_v4_non_native_quote_route'),('no_sell_quote_unknown_n','no_positive_sell_quote')]:
        r=one(dilution,year=2026,venue='all',metric=metric)
        add('v21_2026_'+key,int(r.n_positive),ds,f'year=2026,venue=all,metric={metric}; n_positive; unknown route-reason sample count',int(r.n_positive))
    rs=T2+'residual_liquidity_harm.csv'; residual=read(rs); rows=[]
    for year in ['2024','2025','2026']:
        r=one(residual,year=year,venue='all',metric=HARM)
        rows.append([year,int(r.n_sampled),estimate(r)])
    table('v21_residual_table',['Year','Sampled residual-liquidity pools','Noisy sale/tax-harm ratio [approx. 95%]'],rows,rs,'all venues,1e9<=real quote wei<1e16; metric=headline_trade_harm; informative exclusion, not primary harm')
    r=one(read(T2+'annual_shares.csv'),year=2026,denominator='tradable_ge_0_1_eth',label=HARM)
    add('v21_2026_v1v2_tradable_share',supported_pct(r),T2+'annual_shares.csv','year=2026,denominator=tradable_ge_0_1_eth,label=headline_trade_harm; V1/V2 only, not all-venue 0.1-ETH floor',number(r['share']))


def launch_blind_numbers():
    base='data/verify/blind300/'; source=base+'summary.csv'
    summary=read(source)
    audit_source=base+'audit.json'; audit=json.loads((ROOT/audit_source).read_text())
    if str(audit.get('status','')).lower() not in ['pass','passed']: raise RuntimeError('Independent +300 replay audit is not PASS')
    manifest_source=base+'report_manifest.json'; manifest=json.loads((ROOT/manifest_source).read_text())
    assert manifest['label_unsealed_after_verdict_freeze'] is True
    assert manifest['summary_sha256']==hashlib.sha256((ROOT/source).read_bytes()).hexdigest()
    sample_source=base+'sample_counts.csv'; sample=read(sample_source)
    add('blind300_sample_n',int(sample.n.sum()),sample_source,'row count; independent fixed-input +300 replay; no per-token publication',int(sample.n.sum()))
    real=summary[summary.domain.eq('real_0.01_eth')]
    allr=one(real,year='all',venue='all')
    add('blind300_abstract',f'An independent +300 replay covers {int(sample.n.sum())} selected states, with {allr.n_decidable}/{allr.n} real-floor states yielding a known binary outcome; inference remains conditional on the sampled prediction frame, sampling intervals exclude classification error, and blind-cell coverage and a pooled error scenario describe the remaining gap.',[source,sample_source],'all years/venues,real_0.01_eth; n_decidable/n, conditional binary frame')
    rows=[]; weighted=[]
    def raw_metric(r,name,trials):
        return 'NA (no trials)' if trials==0 else 'Not reported (trials <30)' if trials<30 else f'{pct(r[name])} [{pct(r[name+"_ci_low"])}, {pct(r[name+"_ci_high"])}]'
    def weighted_metric(r,name,support):
        if number(support)<30: return 'Not reported (effective support <30)'
        return f'{pct(r[name])} [{pct(r.get(name+"_ci_low",""))}, {pct(r.get(name+"_ci_high",""))}]'
    for _,r in real.iterrows():
        if str(r.year)=='all' and str(r.venue)!='all': continue
        pos=int(r.tp)+int(r.fp);neg=int(r.tn)+int(r.fn)
        rows.append([r.year,r.venue,f'{r.n_decidable}/{r.n}',f'{r.tp}/{r.fp}/{r.fn}/{r.tn}',raw_metric(r,'precision_harm',pos),raw_metric(r,'npv_not_harm',neg),f'{r.n_no_liquidity}/{r.n_could_not_buy}/{r.n_unprobeable}'])
        sensitivity_support=min(number(r.precision_harm_kish_n),number(r.npv_not_harm_kish_n))
        weighted.append([r.year,r.venue,weighted_metric(r,'weighted_precision_harm',r.precision_harm_kish_n),weighted_metric(r,'weighted_npv_not_harm',r.npv_not_harm_kish_n),weighted_metric(r,'weighted_sensitivity',sensitivity_support),f'{number(r.precision_harm_kish_n):.1f}/{number(r.npv_not_harm_kish_n):.1f}'])
    table('blind300_table',['Year','Venue','Known / real-floor sampled','TP/FP/FN/TN','Sample precision [Wilson 95%]','Sample NPV [Wilson 95%]','No liquidity / buy failed / unknown'],rows,source,'real_0.01_eth; honeypot-or-high-tax vs sellable; pooled raw metrics sample-conditional, not population recall; <30 trial metrics withheld')
    table('blind300_weighted_table',['Year','Venue','Weighted precision [approx. 95%]','Weighted NPV [approx. 95%]','Weighted sensitivity [approx. 95% envelope]','Kish predicted harm / not-harm'],weighted,source,'real_0.01_eth; two-stage original census weight times predicted-cell N/n; sensitivity Bonferroni Wilson component envelope; conservative support minimum; unknowns excluded from binary domain')
    methods=(f'The independent harness freezes {int(sample.n.sum())} verdicts before opening predicted labels. For creation years 2024–2026, a seeded draw takes 30 per available year × venue × predicted-harm/not-harm cell, or all when fewer exist; 2024 V4 is structurally absent. The pinned predictions precede the real-liquidity rebuild. Every buy starts with 0.01 ETH at archive parent first liquidity +300; buy/approval/sale share synthetic block +301, unlike production’s later-block sell ladder. V4 real principal is reconstructed independently from all position events. Known-outcome performance excludes no-liquidity, failed-buy and unresolved states; their counts remain explicit. Sampling weights are original census weight × source prediction-cell N/replay n. Precision and NPV condition on those predicted strata; sensitivity uses the weighted confusion totals and an approximate Bonferroni-Wilson component envelope that preserves uncertainty about unseen missed positives. These intervals omit original census uncertainty, launch dependence and classification error. Owner-control capabilities, predictions outside the binary frame, alternative callers, and later exits are outside scope. Rates below 30 effective trials are withheld.')
    add('blind300_methods',methods,[sample_source,manifest_source,base+'README.md'],'saved independent sampling, blinding, fixed-amount single-block execution, weighting and uncertainty policy')
    rounding=one(summary,year='all',venue='all',domain='real_0.01_eth_nominal_rounding')
    results=(f'Across the replayed real-floor domain, {allr.n_decidable}/{allr.n} states have binary outcomes, with TP/FP/FN/TN = {allr.tp}/{allr.fp}/{allr.fn}/{allr.tn}. Pooled unweighted precision and NPV describe the balanced verification sample, not the launch population. The tax-boundary sensitivity allows one base unit of rounding per measured side: its TP/FP/FN/TN is {rounding.tp}/{rounding.fp}/{rounding.fn}/{rounding.tn}. The strict false positives in this real-floor comparison disappear under nominal rounding; they reflect the 10% boundary rather than tax-free trading. The missed V2 case is a same-block sale restriction after a positive buy; later-block exits were not independently tested. Zero observed misses in other sampled normal strata do not establish 100% population recall. The all-sample and virtual-floor summaries remain saved diagnostics; the paper uses the real-floor domain.')
    add('blind300_results',results,source,'all years/venues; compare real_0.01_eth with real_0.01_eth_nominal_rounding; retain confusion counts and conditional scope')


def delayed_numbers():
    """Add matched one-day results without altering any launch-state ledger key."""
    base='data/verify/delayed/'
    verification=json.loads((ROOT/base/'verification.json').read_text())
    if verification.get('status')!='PASS' or verification.get('issues'):
        raise RuntimeError('Delayed-outcome evidence verification is incomplete')
    within_source=base+'within_day_harm.csv'
    transition_source=base+'transitions_by_year.csv'
    coverage_source=base+'coverage.csv'
    within=read(within_source); transitions=read(transition_source)
    coverage=read(coverage_source)
    for year in [2024,2025,2026]:
        filters=dict(year=year,venue='All',followup='exact_plus7200')
        for tag,metric in [('launch','harm_plus300'),('within_day','harm_plus300_or_delayed')]:
            r=one(within,**filters,metric=metric)
            value=number(r['share'])
            add(f'delayed_{year}_all_{tag}_share',pct(value),within_source,
                f'{filters},metric={metric}; column=share; same launch-measurable, late-liquidity-known denominator, including late depleted pools',value)
        c=one(coverage,**filters); value=number(c.normal_followup_weight_coverage)
        add(f'delayed_{year}_normal_followup_coverage',pct(value),coverage_source,
            f'{filters}; column=normal_followup_weight_coverage; matched launch-normal weight / all launch-normal weight',value)
        filters.update(venue='V1/V2',normal_definition='normal_or_owner_controlled')
        r=one(transitions,**filters,metric='delayed_trap_given_survives')
        value=number(r['share'])
        add(f'delayed_{year}_v1v2_surviving_share',supported_pct(r),transition_source,
            f'{filters},metric=delayed_trap_given_survives; column=share; conditional on late quote >=0.01 ETH',value)
        for column in ['n_positive','n_sampled','positive_kish_n_eff','denominator_kish_n_eff']:
            if year==2026 and column in ['n_sampled','denominator_kish_n_eff']: continue
            val=number(r[column]);add(f'delayed_{year}_v1v2_surviving_{column}',f'{val:.0f}',transition_source,f'{filters},metric=delayed_trap_given_survives; column={column}; small-support diagnostic counts',val)
        if year==2026:
            for column in ['ci_low','ci_high','n_sampled','denominator_kish_n_eff']:
                value=number(r[column])
                display=supported_pct(r,column) if column.startswith('ci_') else f'{value:.0f}'
                add(f'delayed_{year}_v1v2_surviving_{column}',display,transition_source,
                    f'{filters},metric=delayed_trap_given_survives; column={column}; stratified cluster bootstrap/FPC interval; denominator support',value)
        if year in [2024,2025,2026]:
            r=one(transitions,**filters,metric='below_floor_given_normal'); value=number(r['share'])
            add(f'delayed_{year}_v1v2_below_floor_share',pct(value),transition_source,
                f'{filters},metric=below_floor_given_normal; column=share; conditional on known late liquidity; descriptive, not rug attribution',value)
    source=base+'case_counts.csv'; cases=read(source)
    reviewed=cases[cases.hand_reviewed.astype(str).str.lower().eq('true')]
    add('delayed_reviewed_cases_n',int(reviewed.n.sum()),source,'sum(n) where hand_reviewed=true; purposive mechanism sample, not population prevalence',int(reviewed.n.sum()))
    n=int(reviewed.loc[reviewed.mechanism_group.eq('fee_recipient_eip7702_activation'),'n'].sum())
    add('delayed_reviewed_eip7702_n',n,source,'sum(n) where hand_reviewed=true AND mechanism_group=fee_recipient_eip7702_activation; delegation installation, no intent attribution',n)


def figures():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    out=ARTICLE/'figures';out.mkdir(exist_ok=True)
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':180})
    a=read(T2+'annual_shares.csv');v=read(T2+'venue_by_year.csv');std=read(T2+'standardized.csv')
    fig,(ax,mixax)=plt.subplots(2,1,figsize=(10,7),sharex=True,gridspec_kw={'height_ratios':[3,1]})
    for venue,color in [('All venues','#102f4e'),('V1/V2','#b64b25'),('V3','#167a73'),('V4','#9462a4')]:
        d=a[(a.denominator=='measurable_pooled')&(a.label==HARM)] if venue=='All venues' else v[(v.denominator=='measurable_pooled')&(v.venue==venue)]
        d=d.copy(); d['year']=d.year.astype(int);d=d.sort_values('year'); y=pd.to_numeric(d.share,errors='coerce')*100
        if venue=='All venues':
            sparse=pd.to_numeric(d.effective_n,errors='coerce')<30
            y=y.mask(sparse)
        else:
            y=y.mask((d.year<2020)|(pd.to_numeric(d.effective_n,errors='coerce')<30))
        complete=d.year<2026
        ax.plot(d.year[complete],y[complete],marker='o',label=('V1/V2 + Sushi' if venue=='V1/V2' else venue) if y.notna().any() else None,color=color)
        ax.plot(d.year.tail(2),y.tail(2),ls='--',color=color)
        ax.plot(d.year[~complete],y[~complete],marker='o',ls='none',markerfacecolor='white',color=color)
        if venue=='All venues': ax.fill_between(d.year,pd.to_numeric(d.ci_low,errors='coerce').mask(sparse)*100,pd.to_numeric(d.ci_high,errors='coerce').mask(sparse)*100,alpha=.12,color=color)
    standardized=pd.to_numeric(std.share,errors='coerce').mask(pd.to_numeric(std.effective_n,errors='coerce')<30)*100
    if standardized.notna().any():ax.plot(std.year.astype(int),standardized,'--',color='#555555',label='Fixed 2024 venue mix')
    ax.set_ylabel('Could not sell or tax ≥10% (%)');ax.legend(ncols=3,loc='upper right',fontsize=8);ax.set_ylim(bottom=0)
    years=sorted(a.year.astype(int).unique()); bottom=np.zeros(len(years))
    for venue,color in [('V1/V2','#b64b25'),('V3','#167a73'),('V4','#9462a4')]:
        vals=[]
        for year in years:
            d=v[(v.year.astype(int)==year)&(v.denominator=='measurable_pooled')];total=pd.to_numeric(d.frame_denominator_weight).sum(); rr=d[d.venue==venue]
            vals.append(100*pd.to_numeric(rr.frame_denominator_weight).sum()/total if total else 0)
        mixax.bar(years,vals,bottom=bottom,color=color,label=venue);bottom+=vals
    mixax.set_ylabel('Venue mix (%)');mixax.set_xlabel('Token creation year (2026: January–September)');mixax.set_xticks(years)
    mixax.legend(ncols=3,fontsize=8,loc='upper left')
    fig.tight_layout();fig.savefig(out/'annual_headline.png');plt.close(fig)
    s=read(T2+'sensitivity.csv');fig,ax=plt.subplots(figsize=(10,4.5))
    for scenario,label in [('primary','Launch +300, ≥0.01 ETH'),('floor_1e9_wei','Launch +300, tiny floor'),('floor_0.1_eth','All-venue ≥0.1 ETH'),('virtual_V4_liquidity','Virtual V4 floor (sensitivity)'),('legacy_selected_pool_plus7200','Legacy selection +7200, ≥0.01 ETH')]:
        d=s[(s.frequency=='annual')&(s.denominator=='measurable_pooled')&(s.scenario==scenario)].copy();d['period']=d.period.astype(int);d=d[d.period>=2020].sort_values('period');ys=pd.to_numeric(d.share,errors='coerce').mask(pd.to_numeric(d.effective_n,errors='coerce')<30)*100
        line,=ax.plot(d.loc[d.period<2026,'period'],ys[d.period<2026],'o-',label=label)
        ax.plot(d.period.tail(2),ys.tail(2),'--',color=line.get_color())
        ax.plot(d.loc[d.period==2026,'period'],ys[d.period==2026],'o',markerfacecolor='white',color=line.get_color())
    ax.set_ylabel('Trade harm (%)');ax.set_xlabel('Token creation year (2026: January–September)');ax.legend(fontsize=8);ax.set_ylim(bottom=0);fig.tight_layout();fig.savefig(out/'sensitivity.png');plt.close(fig)
    d=read(T2+'monthly_shares.csv');d=d[(d.denominator=='measurable_pooled')&(d.label==HARM)].copy();d=d.sort_values('month');x=pd.to_datetime(d.month)
    en=pd.to_numeric(d.effective_n,errors='coerce');y=pd.to_numeric(d.share,errors='coerce').mask(en<30)*100;fig,ax=plt.subplots(figsize=(11,4.5))
    ax.plot(x,y,color='#4b6578',lw=.8);ax.fill_between(x,pd.to_numeric(d.ci_low,errors='coerce').mask(en<30)*100,pd.to_numeric(d.ci_high,errors='coerce').mask(en<30)*100,color='#5c91ae',alpha=.18)
    ax.scatter(x[en>=30],y[en>=30],s=12,color='#102f4e',label='Effective support ≥30; other rates withheld')
    ax.set_ylabel('Trade harm (%)');ax.set_xlabel('Ethereum token creation month');ax.legend(fontsize=8);ax.set_ylim(bottom=0);ax.set_xlim(x.min(),x.max()+pd.offsets.MonthEnd(0));fig.tight_layout();fig.savefig(out/'monthly_series.png');plt.close(fig)
    sources=[T2+p for p in ['annual_shares.csv','venue_by_year.csv','standardized.csv','sensitivity.csv','monthly_shares.csv','cohort_weights.csv']]
    (out/'sources.json').write_text(json.dumps({'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources},'generator':'article/build_numbers.py','scope':'completed launch-state estimates; no preview data'},indent=2)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--static-only',action='store_true');p.add_argument('--prepare-site-counts',action='store_true',help='Preliminary ledger for site count preparation; never signals readiness');args=p.parse_args()
    static_numbers()
    if args.static_only:
        (ARTICLE/'static_numbers.json').write_text(json.dumps(ENTRIES,indent=2,sort_keys=True)+'\n');print(f'{len(ENTRIES)} static keys validated');return
    tier2_numbers()
    revision_numbers()
    launch_blind_numbers()
    delayed_numbers()
    if not args.prepare_site_counts: site_count_numbers()
    template=(ARTICLE/'paper.template.md').read_text()
    needed=set(re.findall(r'\{\{([a-zA-Z0-9_]+)\}\}',template))
    if needed-ENTRIES.keys():raise ValueError(f'Missing keys: {needed-ENTRIES.keys()}')
    paper=re.sub(r'\{\{([a-zA-Z0-9_]+)\}\}',lambda m:render_value(m[1]),template)
    if re.search(r'\{\{|\}\}',paper):raise ValueError('Unrendered placeholder')
    if re.search(r'\b(?:C[0-9]+|W[0-9])\b|notes/',paper):raise ValueError('Internal project jargon in paper')
    figures()
    (ARTICLE/'numbers.json').write_text(json.dumps(ENTRIES,indent=2,sort_keys=True,allow_nan=False)+'\n')
    (ARTICLE/'paper.md').write_text(paper)
    print(f'Built {len(ENTRIES)} evidence-backed keys, paper.md and three figures')

if __name__=='__main__':main()
