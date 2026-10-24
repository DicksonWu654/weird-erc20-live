"""Integrity, domain, interval and sensitivity checks for C17 outputs."""
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from reestimate_v2 import ROOT,OUT,LABELS,HARM
from early_liquidity import load_jsonl,positive


REQUIRED=['token_results.parquet','annual_shares.csv','quarterly_shares.csv','monthly_shares.csv',
          'venue_by_year.csv','method_by_year.csv','standardized.csv','sensitivity.csv','bounds.csv',
          'misclass_adjusted.csv','launch_liquidity_by_year_venue.csv',
          'unresolved_dilution_by_year_venue.csv','blind_coverage_by_year.csv','pooled_assumption_ranges.csv']


def verify(directory=OUT,preview=False):
    out=Path(directory)
    for name in REQUIRED:assert (out/name).is_file(),name
    d=pd.read_parquet(out/'token_results.parquet')
    frame=pd.read_parquet(ROOT/'data/census/sample_frame.parquet')
    assert len(d)==len(frame)==47963
    assert d.sample_id.tolist()==list(range(len(frame)))
    assert np.array_equal(d.weight.to_numpy(),frame.weight.to_numpy())
    assert d.address.tolist()==frame.address.tolist()
    assert d.creation_block.tolist()==frame.creation_block.tolist()
    assert set(d.category)<=set(LABELS)
    assert d.category.notna().all()
    assert d.loc[d.replay_consistent.eq(False)&d.measurable,'category'].eq('unprobeable').all()
    assert d.loc[d.creation_method.eq('unknown'),'creation_method_group'].eq('unknown').all()
    assert d.loc[d.creation_method.eq('factory'),'creation_method_group'].eq('factory-launcher').all()
    assert d.loc[d.creation_method.eq('direct_deploy'),'creation_method_group'].eq('direct').all()
    assert d.headline_trade_harm.eq(d.category.isin(HARM)).all()
    assert d.loc[d.headline_trade_harm,'measurable'].all()
    assert (~d.loc[d.category.eq('no_meaningful_liquidity'),'measurable']).all()
    assert d.loc[d.category.eq('no_meaningful_liquidity'),'liquidity_known'].all()
    assert d.loc[d.measurable,'liquidity_known'].all()
    assert all(int(x)>=10**16 for x in d.loc[d.measurable,'quote_reserve_eth_equiv_wei'])
    assert d.loc[d.category.isin(['could_not_buy','late_pool','unprobeable']),'unprobeable_reason'].notna().all()
    records=load_jsonl(OUT/'early_results.jsonl')
    selection=load_jsonl(OUT/'liquidity_selection.jsonl')
    if not preview:
        from liquidity_v21 import saved_logs,reconstruct,V4_MANAGER,raw_envelopes,TOPICS
        assert len(records)==len(selection)==int(frame.pool_ever.sum())==27091
        assert not d.simulation_status.eq('pending').any()
    # Snapshot keys: collection may still advance while a preview is checked.
    ids=d.loc[d.simulation_status.eq('complete'),'sample_id']
    selected=0;rawchecked=0;replayed=0
    for sid in ids:
        r=records[int(sid)];s=selection[int(sid)]
        if s['status']=='selected':
            assert r['block']==s['first_liquidity_block']+300
            assert r['pool'].lower()==s['pool'].lower()
            assert (r.get('extra') or {}).get('first_liquidity_block')==s['first_liquidity_block']
            selected+=1
        else:
            assert r['unprobeable'] and not r.get('pool')
        e=json.loads(d.loc[int(sid),'sim_evidence'])
        if not preview:
            assert e==r.get('evidence'),(sid,'token table does not cite latest evidence')
        if e:
            body=gzip.open(ROOT/e['raw'],'rb').read()
            assert hashlib.sha256(body).hexdigest()==e['sha256'],e['raw']
            envs=json.loads(body)
            assert envs and all('request' in v and 'response' in v and 'retrieved_at' in v for v in envs)
            assert all(v['request']['method'] not in ['eth_sendTransaction','eth_sendRawTransaction'] for v in envs)
            rawchecked+=1
    tablecounts={}
    for frequency,col in [('annual','year'),('quarterly','quarter'),('monthly','month')]:
        t=pd.read_csv(out/f'{frequency}_shares.csv')
        assert not t.duplicated([col,'denominator','label']).any()
        assert t.share.dropna().between(0,1).all()
        valid=t.share.notna()
        assert np.isfinite(t.loc[valid,['ci_low','ci_high']]).all().all()
        assert (t.loc[valid,'ci_low']<=t.loc[valid,'share']+1e-12).all()
        assert (t.loc[valid,'ci_high']>=t.loc[valid,'share']-1e-12).all()
        assert (t.low_n==(t.effective_n<10)).all()
        assert t.loc[~t.identified,'share'].isna().all()
        assert (~t.classification_error_included).all()
        assert (t.loc[valid,'est_count_ci_low']<=t.loc[valid,'est_count']+1e-9).all()
        assert (t.loc[valid,'est_count_ci_high']>=t.loc[valid,'est_count']-1e-9).all()
        head=t[t.label.eq('headline_trade_harm')].set_index([col,'denominator'])
        hp=t[t.label.eq('honeypot')].set_index([col,'denominator'])
        tax=t[t.label.eq('high_tax')].set_index([col,'denominator'])
        assert np.allclose(head.share.fillna(-1),(hp.share+tax.share).fillna(-1),atol=1e-12)
        for (p,den),g in t.groupby([col,'denominator']):
            h=g[g.label.eq('headline_trade_harm')].iloc[0]
            if h.identified:
                assert abs(g[g.label.isin(LABELS)].share.sum()-1)<1e-10,(frequency,p,den)
        tablecounts[frequency]=len(t)
    m=pd.read_csv(out/'monthly_shares.csv')
    assert m.month.nunique()==117
    feb=m[m.month.eq('2025-02')&m.denominator.eq('headline_excl_lp')]
    assert feb.share.isna().all()
    y=pd.read_csv(out/'annual_shares.csv')
    assert y[y.year.eq(2025)&y.denominator.eq('headline_excl_lp')].period_coverage.eq('restricted_excludes_2025-02').all()
    sens=pd.read_csv(out/'sensitivity.csv')
    assert {'primary','floor_1e9_wei','floor_0.1_eth','tax_gt10_strict','tax_ge5','LP_fee_as_tax',
            'virtual_V4_liquidity','known_trade_outcomes_only'}<=set(sens.scenario)
    primary=sens[sens.scenario.eq('primary')&sens.frequency.eq('annual')].set_index(['period','denominator'])
    h=y[y.label.eq('headline_trade_harm')&y.denominator.isin(['headline_excl_lp','measurable_pooled'])].set_index(['year','denominator'])
    primary.index=pd.MultiIndex.from_tuples([(int(a),b) for a,b in primary.index])
    assert np.allclose(primary.sort_index().share.fillna(-1),h.sort_index().share.fillna(-1),atol=1e-12)
    old=pd.read_csv(OUT/'_pre_c26/annual_shares.csv')
    old=old[old.label.eq('headline_trade_harm')&old.denominator.isin(['headline_excl_lp','measurable_pooled'])].set_index(['year','denominator'])
    virtual=sens[sens.scenario.eq('virtual_V4_liquidity')&sens.frequency.eq('annual')].copy()
    virtual['period']=virtual.period.astype(int)
    virtual=virtual.set_index(['period','denominator'])
    assert np.allclose(virtual.sort_index().share.fillna(-1),old.sort_index().share.fillna(-1),atol=1e-12)
    bounds=pd.read_csv(out/'bounds.csv')
    b=bounds[bounds.lower.notna()]
    assert (b.lower<=b.upper+1e-12).all() and (b.upper<=b.all_unresolved_upper+1e-12).all()
    assert b.all_unresolved_upper.le(1+1e-12).all()
    mis=pd.read_csv(out/'misclass_adjusted.csv')
    good=mis.adjusted_low.notna()
    assert (mis.loc[good,'adjusted_low']<=mis.loc[good,'adjusted_high']+1e-12).all()
    assert (~mis.population_misclassification_identified).all()
    metrics=pd.read_csv(out/'blind_metrics.csv')
    assert metrics.loc[metrics.n_trials.eq(0),['estimate','ci_low','ci_high']].isna().all().all()
    std=pd.read_csv(out/'standardized.csv')
    assert std.support_flag.eq(np.where(std.low_n,'low_support','supported')).all()
    assert std.low_n.eq(std.effective_n<10).all()
    applicable=std.share.notna()
    assert (std.loc[applicable,'ci_low']<=std.loc[applicable,'share']+1e-12).all()
    assert (std.loc[applicable,'ci_high']>=std.loc[applicable,'share']-1e-12).all()
    if not preview:
        rules=json.loads((out/'RULES_V2_READY').read_text())
        assert rules['version']=='3.3'
        real=load_jsonl(OUT/'liquidity_v21_early.jsonl')
        v4=d[d.venue.eq('V4') & d.selection_status.eq('selected')]
        assert len(v4)==1170
        assert v4.quote_liquidity_method.eq('v4_all_position_principal_at_sim_price').all()
        initialize={r['pool']:r for line in (OUT/'v4_initialize_index.jsonl').open() if (r:=json.loads(line))}
        replayed=0
        for legacy in (False,True):
            measurements=load_jsonl(OUT/('liquidity_v21_legacy.jsonl' if legacy else 'liquidity_v21_early.jsonl'))
            for sid,mr in measurements.items():
                if mr.get('kind')!='v4' or mr.get('status')!='ok':continue
                pool=mr['pool'];init=initialize[pool];final=mr['final']
                ranges=[]
                for ref in mr['evidence']:
                    for env in raw_envelopes(ref['raw'],ref['sha256']):
                        req=env['request'];res=env['response']
                        if req['method']!='eth_getLogs' or 'result' not in res or len(res['result'])>=19990:continue
                        f=req['params'][0];tt=f.get('topics',[])
                        if f.get('address','').lower()==V4_MANAGER and tt==[TOPICS['uniswap_v4'],pool]:
                            ranges.append((int(f['fromBlock'],16),int(f['toBlock'],16)))
                cursor=mr['history_from_block']
                for lo,hi in sorted(ranges):
                    if lo<=cursor:cursor=max(cursor,hi+1)
                assert cursor>mr['block'],(sid,legacy,'ModifyLiquidity history has an uncovered block')
                logs=[l for l in saved_logs(mr['evidence'],V4_MANAGER,pool)
                      if mr['history_from_block']<=int(l['blockNumber'],16)<=mr['block']]
                first_event=None if legacy else (selection[sid]['first_liquidity_block'],selection[sid]['liquidity_log_index'])
                first,computed=reconstruct(logs,'v4',first_event,init['sqrt_price_x96'],final['sqrt_price_x96'])
                for key in ('amount0','amount1','positions'):assert computed[key]==final[key],(sid,legacy,key)
                if first is not None:
                    for key in ('amount0','amount1','positions','sqrt_price_x96'):assert first[key]==mr['first'][key],(sid,key)
                replayed+=1
        assert replayed==1170+1358,replayed
        for r in v4.itertuples():
            measured=real[int(r.sample_id)]
            assert measured['status']=='ok',(r.sample_id,measured.get('reason'))
            assert measured['pool']==r.selected_pool.lower() and measured['block']==int(r.sim_block)
            final=measured['final'];quote0=measured['quote'].lower()==selection[int(r.sample_id)]['token0'].lower()
            for side in (0,1):
                assert int(final[f'amount{side}'])==sum(int(p[f'amount{side}']) for p in final['positions'])
            assert final['reserve_quote']==final['amount0' if quote0 else 'amount1']
            if final['quote_eth_equiv_wei'] is None:
                assert pd.isna(r.quote_reserve_eth_equiv_wei) and not r.liquidity_known
            else:
                assert r.quote_reserve_eth_equiv_wei==final['quote_eth_equiv_wei']
            assert all(int(p['liquidity'])>=0 for p in final['positions'])
            init=initialize[r.selected_pool.lower()]
            assert str(selection[int(r.sample_id)]['sqrt_price_x96'])==init['sqrt_price_x96']
            assert init['block']==measured['history_from_block']
        validation=pd.read_csv(OUT/'v4_stateview_validation.csv')
        assert validation.pool.nunique()>=20
        for col in ('position_liquidity_matches','price_matches','active_liquidity_matches','principal_le_shared_manager_balance'):
            assert validation[col].all(),col
        descriptions=pd.read_csv(out/'launch_liquidity_by_year_venue.csv')
        assert not descriptions.duplicated(['year','venue','metric']).any()
        identified=descriptions.share.notna()
        assert descriptions.loc[identified,'share'].between(0,1).all()
        assert descriptions.loc[identified,'ci_low'].le(descriptions.loc[identified,'share']+1e-12).all()
        assert descriptions.loc[identified,'ci_high'].ge(descriptions.loc[identified,'share']-1e-12).all()
        assert descriptions.loc[identified,'identification_upper'].ge(descriptions.loc[identified,'share']-1e-12).all()
        for _,g in descriptions[descriptions.metric.str.endswith('_given_first_funded_then_below')].groupby(['year','venue']):
            if g.share.notna().any():assert abs(g.share.sum()-1)<1e-10
        scoped=d[~((d.source=='pool_linked')&(d.analysis_month<'2025-02'))]
        for rr in descriptions.itertuples():
            g=scoped[scoped.analysis_month.str[:4].eq(str(rr.year)) & scoped.pool_ever]
            if rr.venue!='all':g=g[g.venue.eq(rr.venue)]
            if rr.metric=='first_funded_share_of_pooled_observed_lower':
                expected=float(g.loc[g.first_liquidity_ge_floor,'weight'].sum())/g.weight.sum() if g.weight.sum() else np.nan
                assert np.isclose(rr.share,expected,equal_nan=True)
            if rr.metric=='harmful_or_emptied_within_hour_given_first_funded':
                g=g[g.first_liquidity_ge_floor]
                expected=float(g.loc[g.headline_trade_harm|g.category.eq('no_meaningful_liquidity'),'weight'].sum())/g.weight.sum() if g.weight.sum() else np.nan
                assert np.isclose(rr.share,expected,equal_nan=True)
        ranges=pd.read_csv(out/'pooled_assumption_ranges.csv')
        known=ranges.pooled_assumption_low.notna()
        assert ranges.loc[known,'pooled_assumption_low'].le(ranges.loc[known,'pooled_assumption_high']).all()
        assert ranges.loc[known,'pooled_assumption_high'].le(ranges.loc[known,'all_unresolved_upper']+1e-12).all()
        coverage=pd.read_csv(out/'blind_coverage_by_year.csv')
        assert coverage.weight_share_cells_with_any_blind_check.dropna().between(0,1).all()
        assert np.allclose((coverage.weight_share_cells_with_any_blind_check+coverage.weight_share_cells_without_any_blind_check).dropna(),1)
        manifest=json.loads((out/'build_manifest.json').read_text())
        for path,digest in {**manifest['inputs'],**manifest['source_hashes']}.items():
            assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest,path
        mis_manifest=json.loads((out/'misclassification_manifest.json').read_text())
        for path,digest in mis_manifest['sources'].items():
            assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest,path
        origins=pd.read_csv(OUT/'origin_attribution.csv')
        sample=pd.read_csv(OUT/'origin_sample.csv')
        assert len(sample)==sample.sample_id.nunique()==50
        assert set(origins.sample_id)==set(sample.sample_id)
        assert origins.loc[origins.status.eq('ok'),'reproduced_outer_revert'].all()
        assert origins.loc[origins.status.eq('ok'),'matched_saved_balance'].all()
        for sid in sample.sample_id:
            assert 'TRANSFER_FROM_FAILED' in str(records[int(sid)].get('label_reason'))
        spots=pd.read_csv(OUT/'spotchecks.csv')
        assert len(spots)>=20
        assert spots[['offset_correct','positive_liquidity_event_saved','saved_sell_statuses_match',
                      'saved_sell_gas_match','raw_sha256_match']].all().all()
        assert spots.category.tolist()==d.set_index('sample_id').loc[spots.sample_id,'category'].tolist()
        compare=pd.read_csv(OUT/'headline_old_vs_new.csv')
        expected=y[y.label.eq('headline_trade_harm')&y.denominator.isin(['headline_excl_lp','measurable_pooled'])]
        joined=compare.merge(expected[['year','denominator','share']],on=['year','denominator'],validate='one_to_one')
        assert len(joined)==len(expected)==20
        assert np.allclose(joined.share_new.fillna(-1),joined.share.fillna(-1),atol=1e-12)
        transition=pd.read_parquet(OUT/'offset_transitions.parquet')
        assert transition.sample_id.nunique()==len(transition)==27090
        later=transition[transition.legacy_state_is_later]
        assert (later.legacy_block>later.new_block).all() and later.pool_matches.all()
        timing=pd.read_csv(OUT/'offset_transition_by_year.csv')
        assert timing.n_early_harm_later_below_floor.le(timing.n_early_harm).all()
        assert timing.share_early_harm_later_below_floor.dropna().between(0,1).all()
        provenance=json.loads((OUT/'report_manifest.json').read_text())
        for name in ('old','new'):
            assert hashlib.sha256((ROOT/provenance[name+'_source']).read_bytes()).hexdigest()==provenance[name+'_sha256']
    httpchecked=0
    for dr in (ROOT/'data/raw/sim').glob('c17-*'):
        if 'rpc' not in dr.name and dr.name!='c17-origin':continue
        for p in dr.rglob('*.json.gz'):
            env=json.loads(gzip.open(p,'rb').read())
            if not isinstance(env,dict):continue
            assert env.get('url') and env.get('request') and env.get('retrieved_at'),str(p)
            if 'response_body' in env:
                assert hashlib.sha256(env['response_body'].encode()).hexdigest()==env['body_sha256'],str(p)
            else:assert env.get('transport_error'),str(p)
            httpchecked+=1
    rawbytes=sum(p.stat().st_size for dr in (ROOT/'data/raw/sim').glob('c17-*') for p in dr.rglob('*') if p.is_file())
    assert rawbytes<=2_000_000_000,rawbytes
    c26checked=0
    for p in (OUT/'raw').rglob('*.json.gz'):
        env=json.loads(gzip.open(p,'rb').read())
        assert env.get('url') and env.get('request') and env.get('retrieved_at'),str(p)
        requests=env['request'] if isinstance(env['request'],list) else [env['request']]
        assert all(r['method'] in ['eth_call','eth_getLogs','eth_getBalance'] for r in requests)
        if 'response_body' in env:assert hashlib.sha256(env['response_body'].encode()).hexdigest()==env['body_sha256']
        else:assert env.get('transport_error')
        c26checked+=1
    report=dict(complete=not preview,rules_version='3.3',n_rows=len(d),n_pooled=int(d.pool_ever.sum()),
                completed_snapshot=len(ids),selected_snapshot=selected,pair_raw_hashes_checked=rawchecked,
                http_attempt_hashes_checked=httpchecked,c26_http_attempt_hashes_checked=c26checked,
                real_v4_state_replays_checked=replayed,
                stateview_pools_checked=int(validation.pool.nunique()) if not preview else 0,
                n_first_liquidity_known=int(d.first_liquidity_known.sum()),
                table_rows=tablecounts,raw_bytes=rawbytes)
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return report
