#!/usr/bin/env python3
"""Independent offline checks of paper joins, domains, labels, and provenance."""
from __future__ import annotations
import base64
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
ARTICLE=ROOT/'article'
T2=ROOT/'data/tier2/v2'
HARM='headline_trade_harm'

def close(a,b):
    assert math.isclose(float(a),float(b),rel_tol=1e-9,abs_tol=1e-11),(a,b)

def read(path): return pd.read_csv(path,keep_default_na=False)
def truth(s): return s.astype(str).str.lower().eq('true')

def check_b20(ledger,matrix_path):
    """Check public count projection; upstream API/body join verified at assembly."""
    counts=read(ROOT/'data/matrix/b20_membership_counts.csv')
    for _,r in counts.iterrows():close(ledger[r.metric]['numeric_value'],r.n)
    assert 'Coinbase-listed B20 (API address join)' in ledger['security_composition']['value']
    assert '| Coinbase B20 |' not in ledger['security_composition']['value']

def check_revision(ledger,active):
    """Independent domains for every first-funding/depletion/combined table cell."""
    # First-liquidity predicates were validated privately before grouping.
    saved=read(T2/'launch_liquidity_by_year_venue.csv')
    for _,r in saved.iterrows():
        g=active[active.analysis_month.str.startswith(str(r.year))]
        if r.venue!='all':g=g[g.venue.eq(r.venue)]
        pooled=g[g.pool_ever];funded=pooled[pooled.first_liquidity_ge_floor]
        emptied=funded[funded.base_category.eq('no_meaningful_liquidity')]
        metric=r.metric
        if metric=='first_liquidity_measured_share_of_pooled':d=pooled;out=d.first_liquidity_known
        elif metric=='first_funded_share_of_pooled_observed_lower':d=pooled;out=d.first_liquidity_ge_floor
        elif metric=='first_funded_share_of_first_measured':d=pooled[pooled.first_liquidity_known];out=d.first_liquidity_ge_floor
        elif metric=='below_floor_at300_share_of_pooled_observed_lower':d=pooled;out=d.base_category.eq('no_meaningful_liquidity')
        elif metric=='below_floor_by300_given_first_funded':d=funded;out=d.base_category.eq('no_meaningful_liquidity')
        elif metric=='harmful_or_emptied_within_hour_given_first_funded':d=funded;out=d.headline_trade_harm|d.base_category.eq('no_meaningful_liquidity')
        elif metric=='harmful_or_below_floor_at300_share_of_pooled_observed_lower':d=pooled;out=d.headline_trade_harm|d.base_category.eq('no_meaningful_liquidity')
        elif metric.endswith('_given_first_funded_then_below'):d=emptied;out=d.reserve_direction.eq(metric.split('_given_')[0])
        else:raise AssertionError(metric)
        assert int(d.n.sum())==int(r.n_sampled)
        close(d.weight.sum(),r.frame_denominator_weight)
        if d.weight.sum():
            point=d.loc[out,'weight'].sum()/d.weight.sum();close(point,r.share)
            close(d.loc[out,'weight'].sum(),r.est_count)
            assert float(r.est_count_ci_low)<=float(r.est_count)<=float(r.est_count_ci_high)
    annual=read(T2/'annual_shares.csv')
    for year in [2024,2025,2026]:
        for label,tag,den in [(HARM,'harm','measurable_pooled'),('no_meaningful_liquidity','below_floor','pooled_tokens')]:
            r=annual[annual.year.eq(year)&annual.denominator.eq(den)&annual.label.eq(label)].iloc[0]
            close(float(r.share)*float(r.denominator_est_count),r.est_count)
            for col in ['est_count','est_count_ci_low','est_count_ci_high']:close(ledger[f'v2_{year}_{tag}_{col}']['numeric_value'],r[col])
    good=read(T2/'blind_pair_counts.csv');pooled=read(T2/'pooled_blind_metrics.csv')
    for _,r in pooled.iterrows():
        predicted=r.metric=='precision';cut=good[truth(good.predicted_harm).eq(predicted)]
        assert int(cut.n.sum())==int(r.n_trials)
        assert int(cut.loc[truth(cut.truth_harm).eq(predicted),'n'].sum())==int(r.n_success)
    ranges=read(T2/'pooled_assumption_ranges.csv')
    for _,r in ranges[ranges.denominator.eq('measurable_pooled')].iterrows():
        g=active[active.analysis_month.str.startswith(str(r.year))&active.measurable]
        if not len(g):continue
        harm=g.loc[g.headline_trade_harm,'weight'].sum()/g.weight.sum()
        normal=g.loc[g.base_category.isin(['normal','owner_controlled']),'weight'].sum()/g.weight.sum()
        close(harm*float(r.precision_lower),r.pooled_assumption_low)
        close(min(1.,harm+normal*(1-float(r.npv_lower))),r.pooled_assumption_high)
    coverage=read(T2/'blind_coverage_by_year.csv')
    for _,r in coverage.iterrows():
        cells=json.loads(r.cells_json);mass=sum(c['weight'] for c in cells)
        close(mass,r.domain_weight)
        if not mass:continue
        close(sum(c['weight'] for c in cells if c['n_any_blind']==0)/mass,r.weight_share_cells_without_any_blind_check)
        close(sum(c['weight'] for c in cells if c['n_jointly_decidable']>0)/mass,r.weight_share_cells_with_jointly_decidable_check)
        close(ledger[f'v21_{r.year}_unverified_cell_share']['numeric_value'],r.weight_share_cells_without_any_blind_check)
    counts=ledger.get('site_counts',{})
    assert isinstance(counts.get('value'),dict) and counts['value']
    assert all(isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v) for v in counts['value'].values())
    assert 'article/numbers.json' not in counts['source_sha256']

def check_launch_blind(ledger):
    base=ROOT/'data/verify/blind300';summary=read(base/'summary.csv');c=read(base/'comparison_groups.csv')
    report=json.loads((base/'report_manifest.json').read_text());audit=json.loads((base/'audit.json').read_text())
    assert audit['status']=='pass' and report['label_unsealed_after_verdict_freeze']
    assert report['summary_sha256']==hashlib.sha256((base/'summary.csv').read_bytes()).hexdigest()
    assert report['comparison_groups_sha256']==hashlib.sha256((base/'comparison_groups.csv').read_bytes()).hexdigest()
    assert int(c.n.sum())==int(ledger['blind300_sample_n']['numeric_value'])
    for _,r in summary.iterrows():
        g=c
        if str(r.year)!='all':g=g[g.year.astype(str).eq(str(r.year))]
        if r.venue!='all':g=g[g.venue.eq(r.venue)]
        if r.domain.startswith('real'):g=g[truth(g.real_floor)]
        elif r.domain.startswith('virtual'):g=g[truth(g.virtual_floor)]
        verdict=g.rounding_adjusted_verdict if r.domain.endswith('nominal_rounding') else g.verdict
        binary=verdict.isin(['honeypot','high_tax','sellable']);harm=verdict.isin(['honeypot','high_tax']);pred=g.predicted.eq('harm')
        assert int(g.n.sum())==int(r.n) and int(g.loc[binary,"n"].sum())==int(r.n_decidable)
        masks={'tp':binary&pred&harm,'fp':binary&pred&~harm,'fn':binary&~pred&harm,'tn':binary&~pred&~harm}
        for k,mask in masks.items():
            assert int(g.loc[mask,"n"].sum())==int(r[k]);close(g.loc[mask,'analysis_weight'].sum(),r['weighted_'+k])
        for name,a,b in [('precision_harm',int(r.tp),int(r.tp)+int(r.fp)),('npv_not_harm',int(r.tn),int(r.tn)+int(r.fn))]:
            if b:close(a/b,r[name])
        for metric,a,b in [('weighted_precision_harm',float(r.weighted_tp),float(r.weighted_tp)+float(r.weighted_fp)),('weighted_npv_not_harm',float(r.weighted_tn),float(r.weighted_tn)+float(r.weighted_fn)),('weighted_sensitivity',float(r.weighted_tp),float(r.weighted_tp)+float(r.weighted_fn))]:
            if b:close(a/b,r[metric])

def check_delayed(ledger):
    """Recompute published matched-domain points from grouped weight moments, not saved CSV ratios."""
    base=ROOT/'data/verify/delayed'
    report=json.loads((base/'verification.json').read_text())
    assert report['status']=='PASS' and not report['issues']
    x=read(base/'matched_weights.csv')
    assert (x.n > 0).all()
    assert (x.normal_trade==(x.measurable & x.category.isin(['normal','owner_controlled']))).all()
    assert not (x.late_below & x.late_above).any()
    w=read(base/'within_day_harm.csv'); t=read(base/'transitions_by_year.csv')
    def row(frame,year,venue,metric):
        rows=frame[frame.year.eq(year)&frame.venue.eq(venue)&frame.followup.eq('exact_plus7200')&frame.metric.eq(metric)]
        if 'normal_definition' in rows:
            rows=rows[rows.normal_definition.eq('normal_or_owner_controlled')]
        assert len(rows)==1
        return rows.iloc[0]
    def scalar(key,value,percent=True):
        close(ledger[key]['numeric_value'],value)
        if not str(ledger[key]['value']).startswith('Not reported'):
            assert ledger[key]['value']==(f'{100*value:.1f}%' if percent else f'{value:.0f}'),key
    def ratio(frame,outcome,saved):
        close(frame.weight.sum(),saved.denominator_weight)
        close(frame.loc[outcome,'weight'].sum(),saved.positive_weight)
        assert int(frame.n.sum())==int(saved.n_sampled)
        point=frame.loc[outcome,'weight'].sum()/frame.weight.sum()
        close(point,saved.share)
        return point
    for year in [2024,2025,2026]:
        cohort=x[x.year.astype(str).eq(str(year))]
        known=cohort[cohort.exact7200 & cohort.late_known]
        domain=known[known.measurable]
        early=domain.category.isin(['honeypot','high_tax'])
        later=domain.normal_trade & domain.late_above & domain.legacy_category.isin(['honeypot','high_tax'])
        for tag,metric,positive in [('launch','harm_plus300',early),('within_day','harm_plus300_or_delayed',early|later)]:
            scalar(f'delayed_{year}_all_{tag}_share',ratio(domain,positive,row(w,year,'All',metric)))
        coverage=known.loc[known.normal_trade,'weight'].sum()/cohort.loc[cohort.normal_trade,'weight'].sum()
        scalar(f'delayed_{year}_normal_followup_coverage',coverage)
        normal=known[known.normal_trade & known.venue.eq('V1/V2')]
        survive=normal[normal.late_above]
        saved=row(t,year,'V1/V2','delayed_trap_given_survives')
        scalar(f'delayed_{year}_v1v2_surviving_share',ratio(survive,survive.legacy_category.isin(['honeypot','high_tax']),saved))
        if year==2026:
            assert saved.ci_method=='stratified_cluster_bootstrap_FPC'
            for column in ['ci_low','ci_high']:
                scalar(f'delayed_{year}_v1v2_surviving_{column}',float(saved[column]))
            scalar(f'delayed_{year}_v1v2_surviving_n_sampled',int(survive.n.sum()),False)
            kish=survive.weight.sum()**2/survive.weight_sq.sum()
            scalar(f'delayed_{year}_v1v2_surviving_denominator_kish_n_eff',kish,False)
        if year in [2024,2025,2026]:
            saved=row(t,year,'V1/V2','below_floor_given_normal')
            scalar(f'delayed_{year}_v1v2_below_floor_share',ratio(normal,normal.late_below,saved))
    cases=read(base/'case_counts.csv')
    reviewed=cases[truth(cases.hand_reviewed)]
    assert reviewed.diagnostic_exact_plus7200.isin(['honeypot','high_tax']).all()
    scalar('delayed_reviewed_cases_n',int(reviewed.n.sum()),False)
    scalar('delayed_reviewed_eip7702_n',int(reviewed.loc[reviewed.mechanism_group.eq('fee_recipient_eip7702_activation'),'n'].sum()),False)
    assert not truth(t.classification_error_included).any()
    assert not truth(t.matching_selection_error_included).any()

def check():
    assert (ROOT/'data/verify/blind300/summary.csv').is_file()
    assert json.loads((T2/'verification.json').read_text())['complete'] is True
    assert json.loads((T2/'verification.json').read_text())['rules_version']=='3.3'
    ledger=json.loads((ARTICLE/'numbers.json').read_text())
    text=(ARTICLE/'paper.md').read_text();template=(ARTICLE/'paper.template.md').read_text()
    assert not re.search(r'\{\{|\}\}|\bTODO\b|\bTBD\b|Pending paper|PLACEHOLDER',text,re.I)
    without_code=re.sub(r'```.*?```','',text,flags=re.S)
    assert not re.search(r'\b(?:C[0-9]+|W[0-9])\b|notes/',without_code)
    for key in re.findall(r'\{\{([A-Za-z0-9_]+)\}\}',template): assert key in ledger,key
    def display(key):
        value=ledger[key]['value']
        return f'{int(value):,}' if isinstance(value,str) and re.fullmatch(r'\d+',value) else str(value)
    rendered=re.sub(r'\{\{([A-Za-z0-9_]+)\}\}',lambda m:display(m[1]),template)
    assert text==rendered,'paper differs from template/ledger'
    inputs={}
    for key,item in ledger.items():
        assert item['source_file'] and item['query'],key
        sources=item['source_file'] if isinstance(item['source_file'],list) else [item['source_file']]
        for path in sources:
            assert 'preview/' not in path,key
            assert not path.startswith('notes/'),key
            assert key=='site_counts' or not (path.startswith('data/tier2/') and not path.startswith('data/tier2/v2/')),key
            if path not in inputs: inputs[path]=hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
            assert inputs[path]==item['source_sha256'][path],(key,path,'source changed')
    check_delayed(ledger)
    # Independent weighted recomputation from grouped weight moments, separate from producer ratios.
    tokens=read(T2/'cohort_weights.csv')
    assert (tokens.n > 0).all()
    active=tokens[~((tokens.frame_basis=='first_pool_month')&(tokens.analysis_month<'2025-02'))].copy()
    category='base_category'
    assert active[category].isin(['honeypot','high_tax','owner_controlled','normal','could_not_buy','v3_fot_incompatible','no_meaningful_liquidity','no_pool','late_pool','unprobeable']).all()
    is_harm=active[category].isin(['honeypot','high_tax'])
    assert (active.headline_trade_harm.astype(bool)==is_harm).all()
    assert active.loc[is_harm,'measurable'].astype(bool).all()
    # Liquidity/rule invariants are checked before grouping at release assembly.
    annual=read(T2/'annual_shares.csv')
    for year in annual.year.astype(str).unique():
        cohort=active[active.analysis_month.str.startswith(year)]
        for den in ['headline_excl_lp','pooled_tokens','measurable_pooled','tradable_ge_0_1_eth']:
            domain=cohort
            if den=='headline_excl_lp' and year=='2025':domain=domain[domain.analysis_month!='2025-02']
            if den=='pooled_tokens':domain=domain[domain.pool_ever.astype(bool)]
            if den=='measurable_pooled':domain=domain[domain.measurable.astype(bool)]
            if den=='tradable_ge_0_1_eth':domain=domain[domain.tradable_ge_0_1_eth.astype(bool)&domain.venue.eq('V1/V2')]
            rows=annual[(annual.year.astype(str)==year)&(annual.denominator==den)&(annual.label==HARM)]
            assert len(rows)==1
            row=rows.iloc[0]
            if not domain.weight.sum():assert row.share=='';continue
            point=domain.loc[domain[category].isin(['honeypot','high_tax']),'weight'].sum()/domain.weight.sum()
            close(point,row.share)
            if den in ['headline_excl_lp','measurable_pooled']:
                tag='all_new' if den=='headline_excl_lp' else 'measurable'
                expected=f'{100*point:.1f}%' if float(row.effective_n)>=30 else 'Not reported (effective support <30)'
                assert ledger[f'v2_{year}_{tag}_share']['value']==expected
                close(ledger[f'v2_{year}_{tag}_share']['numeric_value'],point)
    pooled=active[active.pool_ever.astype(bool)]
    below=pooled.loc[pooled[category]=='no_meaningful_liquidity','weight'].sum()/pooled.weight.sum()
    close(below,ledger['v2_overall_below_floor']['numeric_value'])
    check_revision(ledger,active)
    check_launch_blind(ledger)
    check_b20(ledger,matrix_path=ROOT/'data/matrix/matrix.parquet')
    cadence_cells=0
    for cadence,period in [('annual','year'),('quarterly','quarter'),('monthly','month')]:
        d=read(T2/(cadence+'_shares.csv'))
        assert not truth(d.classification_error_included).any()
        assert not d.duplicated([period,'denominator','label']).any()
        cells={(str(r[period]),r.denominator,r.label):r for _,r in d.iterrows()}
        for _,r in d.iterrows():
            if str(r.identified).lower()=='false':assert r.share==r.ci_low==r.ci_high==''
            if r.share!='':
                assert -1e-12<=float(r.share)<=1+1e-12
                if r.ci_low!='' and r.ci_high!='':assert float(r.ci_low)-1e-12<=float(r.share)<=float(r.ci_high)+1e-12
            if r.label==HARM and r.share!='':
                members=[cells[(str(r[period]),r.denominator,label)] for label in ['honeypot','high_tax']]
                close(r.share,sum(float(x.share) for x in members));cadence_cells+=1
            if cadence=='quarterly' and str(r.quarterly_publishable).lower()=='true' and r.label==HARM:
                assert float(r.effective_n)>=30 and r.period_coverage=='full_frame_scope'
        if cadence=='monthly':
            feb=cells[('2025-02','headline_excl_lp',HARM)]; assert feb.share==''
    # Cross-check confusion metrics independently from four cells, including base-rate effects.
    h=read(ROOT/'data/validate/round3/headline.csv')
    h=h[(h.dataset=='round3_v2')&(h.stratum=='all')&(h.lag_group=='all')&(h.weighting=='unweighted')]
    assert len(h)==9
    for _,r in h.iterrows():
        a,b,c,d=[int(r[k]) for k in ['n00','n01','n10','n11']];n=a+b+c+d
        assert n==int(r.n)
        po=(a+d)/n;pe=((a+b)*(a+c)+(c+d)*(b+d))/n**2
        close(po,r.agreement);close((po-pe)/(1-pe),r.kappa);close(2*po-1,r.pabak)
        if 2*d+b+c:close(2*d/(2*d+b+c),r.positive_specific_agreement)
        if 2*a+b+c:close(2*a/(2*a+b+c),r.negative_specific_agreement)
        assert ledger[f'validation_{r.comparison}_{r.label}_n']['numeric_value']==n
    g=h[(h.comparison=='sim_vs_goplus')&(h.label=='honeypot')].iloc[0]
    assert int(g.n11)==0 and float(g.kappa)<=0
    # Matrix: extras excluded, zero archive support retained as unknown, fee correction consumed.
    subsets=json.loads((ROOT/'data/matrix/subsets.json').read_text())['subsets']
    assert subsets['ranked']['n_tokens']+subsets['extras']['n_tokens']==subsets['inventory']['n_tokens']
    main=read(ROOT/'data/matrix/prevalence_by_chain_rank.csv')
    r=main[(main.chain=='all')&(main.rank_band=='all_market_cap')&(main.feature=='fee_on_transfer_observed')].iloc[0]
    assert int(r.n_yes)==29
    for _,r in main.iterrows():
        assert int(r.n_yes)+int(r.n_no)==int(r.n_measurable)
        assert int(r.n_measurable)+int(r.n_unmeasurable)==int(r.n_tokens)
    tests=read(ROOT/'data/matrix/cross_chain_tests.csv')
    assert tests.rank_band.eq('1-500').all()
    # Do not silently change a significant saved result into a nonsignificant sentence.
    r=tests[(tests.analysis_set=='market_cap')&(tests.chain_a=='ethereum')&(tests.chain_b=='rest')&(tests.feature=='ret_missing_transfer')].iloc[0]
    if 'not significant after Holm' in text:
        assert float(r.p_holm)>=.05,'Requested nonsignificance conflicts with current source; needs corrected evidence'
    close(float(ledger['missing_return_holm']['numeric_value']),r.p_holm)
    # Third-party bibliographic HTTP bodies are not redistributed in this package.
    evidence_n=0
    for p in (ARTICLE/'reference_evidence').glob('*.json.gz'):
        o=json.load(gzip.open(p));body=base64.b64decode(o['body_base64'])
        assert hashlib.sha256(body).hexdigest()==o['sha256']
        assert o['request']['method']=='GET' and o['retrieved_at']
        evidence_n+=1
    bib=(ARTICLE/'refs.bib').read_text();keys=set(re.findall(r'@\w+\{([^,]+),',bib))
    cites=set(re.findall(r'@([A-Za-z0-9_]+)',text))
    assert cites<=keys,cites-keys
    assert 'Huynh, Tuan' not in bib and 'Daza, Victor' not in bib and 'Cernera, Matteo' not in bib
    for path in re.findall(r'!\[[^\]]*\]\(([^)]+)\)',text):assert (ARTICLE/path).is_file(),path
    assert all(len(re.findall(r'!\[Figure '+str(n)+r'\.',text))==1 for n in [1,2,3])
    print(f'PASS: {len(ledger)} ledger keys / {len(inputs)} hashed inputs; {cadence_cells} trade-union cells; all annual grouped weighted domains; delayed grouped points/support/case counts; 9 confusion matrices; {len(cites)} citation keys; {evidence_n} HTTP evidence envelopes; no placeholders/internal codes')

if __name__=='__main__':check()
