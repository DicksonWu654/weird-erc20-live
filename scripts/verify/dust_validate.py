"""Final C16 completeness, evidence integrity and arithmetic checks (offline)."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from dust_rpc import ROOT,DUST,V3


def main():
    checks=[]
    def check(name,value,expected=True):
        passed=bool(value==expected);checks.append(dict(check=name,value=str(value),expected=str(expected),passed=passed))
    rawpins=[]
    drift=[]
    for source in json.loads((DUST/'input_manifest.json').read_text())+json.loads((DUST/'code_manifest.json').read_text()):
        name=source.get('source',source.get('path'));p=ROOT/name
        current=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        drift.append(dict(path=name,pinned_sha256=source['sha256'],current_sha256=current,changed=current!=source['sha256'],
                          note='C16 uses frozen inputs; concurrent upstream edits do not change this audit'))
    (DUST/'upstream_source_drift.json').write_text(json.dumps(drift,indent=2))
    for base in [DUST,V3]:
        for folder in ['raw','raw_fee','raw_balance','raw_blind']:
            for p in (base/folder).glob('*.json'):
                env=json.loads(p.read_text());body=env.get('response_body')
                if body is not None:
                    good=hashlib.sha256(body.encode()).hexdigest()==env['sha256']
                    if not good:raise AssertionError('raw body hash mismatch '+str(p))
                method=env['request']['method']
                if method.startswith(('eth_send','personal_','wallet_')):raise AssertionError('non-read-only request')
                rawpins.append(dict(path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size,
                                    retrieved_at=env['retrieved_at'],response_body_sha256=env.get('sha256')))
    (DUST/'raw_manifest.json').write_text(json.dumps(rawpins,indent=2))
    check('all_raw_envelopes_have_body_and_hash',all(bool(p.get('response_body_sha256')) for p in rawpins))
    sample=pd.read_csv(DUST/'reserve_sample.csv');r=pd.read_csv(DUST/'reserve_checks.csv')
    check('v2_sample_rows',len(sample),120);check('v2_complete_rows',len(r),120)
    check('v2_all_saved_reserves_exact_match',r.saved_reserve_exact_match.fillna(False).all())
    check('v2_all_saved_dust_labels_correct',r.recorded_label_correct.fillna(False).all())
    for year in ['2025','2026']:check('v2_'+year+'_at_least_40',int(r.analysis_month.str.startswith(year).sum())>=40)
    r[~r.saved_reserve_exact_match.fillna(False)|~r.recorded_label_correct.fillna(False)].to_csv(DUST/'confirmed_reserve_or_label_errors.csv',index=False)
    raw=json.loads((DUST/'raw_sample_checks.json').read_text());check('original_raw_hashes_match',sum(x['hash_matches'] for x in raw),120)
    check('original_parent_blocks_match',all(all(int(p['parent'],16)==x['sim_block'] for p in x['phases']) for x in raw))
    f=pd.read_csv(DUST/'fee_mint_formula.csv');check('fee_formula_10_receipts',len(f),10);check('fee_formula_exact_all',f.formula_matches.all())
    v=pd.read_csv(V3/'v3_dust.csv');check('v3_balance_rows',len(v),2181);check('v3_complete_unique',v.sample_id.nunique(),2181)
    check('v3_balance_no_transport_errors',v.status.eq('ok').all())
    b=pd.read_csv(V3/'blind_verdicts.csv');check('blind_rows',len(b),95)
    check('blind_v3_positive',b.stratum.eq('uniswap_v3_positive').sum(),5)
    check('blind_v3_normal',b.stratum.eq('uniswap_v3_normal').sum(),60)
    check('blind_v4_normal',b.stratum.eq('uniswap_v4_normal').sum(),30)
    check('blind_all_determinate',b.independent_label.isin(['sellable','honeypot','high_tax','no_liquidity']).all())
    for d in list((DUST/'reserve_details').glob('*.json'))+list((V3/'balance_details').glob('*.json'))+list((V3/'blind_details').glob('*.json')):
        rec=json.loads(d.read_text())
        for evidence in rec.get('evidence',[]):
            if not (ROOT/evidence).is_file():raise AssertionError('missing evidence '+evidence)
    tab=pd.read_csv(DUST/'venue_by_year.csv');frame=pd.read_parquet(DUST/'inputs/data__tier2__token_results.parquet')
    frame=frame[~((frame.frame_basis=='first_pool_month')&(frame.analysis_month<'2025-02'))]
    for year in [2024,2025,2026]:
        x=frame[frame.analysis_month.str.startswith(str(year))&frame.pool_ever&frame.base_category.isin(['honeypot','high_tax','normal','owner_controlled','rugged'])]
        a=tab[(tab.year==year)&tab.selected_venue.eq('all')].iloc[0]
        check(str(year)+'_headline_ratio_reproduces',np.isclose(a.honeypot_or_high_tax_share,x.loc[x.category.isin(['honeypot','high_tax']),'weight'].sum()/x.weight.sum(),rtol=0,atol=1e-14))
    pd.DataFrame(checks).to_csv(DUST/'validation.csv',index=False)
    summary=dict(checks=len(checks),failed=sum(not r['passed'] for r in checks),raw_envelopes=len(rawpins),
                 v3_below_floor=int(v.below_1e9_wei_floor.eq(True).sum()),v3_current_measurable_below_floor=int(v[v.category.isin(['normal','high_tax','honeypot','owner_controlled','rugged'])].below_1e9_wei_floor.eq(True).sum()),
                 v2_verified=len(r),blind_verified=len(b))
    (DUST/'validation_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
    if summary['failed']:raise SystemExit('C16 validation failed')


if __name__=='__main__':main()
