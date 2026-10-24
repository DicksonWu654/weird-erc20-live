"""Offline C27 evidence, liquidity reconstruction, blinding and statistics audit."""
import ast
import argparse
import collections
import hashlib
import json
import math
from decimal import Decimal,localcontext
from pathlib import Path
import re

import pandas as pd
from eth_abi import decode
from eth_utils import keccak
from blind300_rpc import ROOT,OUT,ALLOWED
from blind300_replay import EOA,BAL,MODIFY,SV


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--partial',action='store_true');args=ap.parse_args()
    detail_snapshot={p.name:json.loads(p.read_text()) for p in (OUT/'details').glob('*.json')}
    checks={};errors=[];raw={};starts=collections.defaultdict(list);attempts=0
    for p in (OUT/'raw').glob('*.json'):
        r=json.loads(p.read_text());raw[str(p.relative_to(ROOT))]=r
        req=r['request'];assert req['method'] in ALLOWED
        assert req['jsonrpc']=='2.0' and isinstance(req['params'],list)
        if 'response_body' in r:
            assert hashlib.sha256(r['response_body'].encode()).hexdigest()==r['sha256'],p
            try:assert json.loads(r['response_body'])==r['response']
            except json.JSONDecodeError:assert 'response' not in r
            assert 'http_status' in r and 'retrieved_at' in r
        else:assert r.get('transport_error')
        if '-attempt' in p.name:
            attempts+=1;starts[r['url']].append(r['request_start_monotonic'])
        if req['method']=='eth_simulateV1':
            assert req['params'][1].startswith('0x')
            specs=req['params'][0]['blockStateCalls'];assert len(specs)==1
            assert set(specs[0]['stateOverrides'])=={EOA,BAL}
            assert set(specs[0]['stateOverrides'][EOA])=={'balance','nonce'}
            assert set(specs[0]['stateOverrides'][BAL])=={'code'}
            assert all(c['from']==EOA and c['gasPrice']=='0x0' for c in specs[0]['calls'])
    checks['raw_files']=len(raw);checks['http_attempts']=attempts
    checks['endpoint_rate_checks']={}
    for url,ts in starts.items():
        ts=sorted(ts);gap=min((b-a for a,b in zip(ts,ts[1:])),default=math.inf)
        assert gap>=1/1.5-.01,(url,gap)
        checks['endpoint_rate_checks'][url]=dict(attempts=len(ts),minimum_start_gap_seconds=gap)
    sample=pd.read_csv(OUT/'sample.csv');assert sample.sample_id.is_unique
    assert ((sample.block-sample.first_liquidity_block)==300).all()
    manifest=json.loads((OUT/'sample_manifest.json').read_text())
    assert sha(OUT/'sample.csv')==manifest['sample_sha256']
    assert sha(OUT/'inputs/token_results.parquet')==manifest['input_sha256']
    assert not any('predicted' in k or 'category' in k for k in sample.columns)
    details=[];v4checks=[]
    for row in sample.to_dict('records'):
        detail_path=OUT/'details'/f'{row["sample_id"]}.json'
        if args.partial and detail_path.name not in detail_snapshot:continue
        d=detail_snapshot[detail_path.name];details.append(d)
        assert all(d[k]==row[k] for k in row)
        assert int(d['probe_eth_wei'])==10**16
        assert d['synthetic_execution_block']==d['block']+1
        assert d['reason']
        for path in d['evidence']:assert path in raw,path
        if d['verdict']=='sellable':
            assert d['buy']['ok'] and int(d['buy']['acquired'])>0
            assert any(a['ok'] and int(a['received_quote'])>0 and int(a['spent'])>0 for a in d['sell_attempts'])
        if d['verdict']=='high_tax' and 'exact_tax_checks' in d:
            assert any(x['ge_10_percent'] for x in d['exact_tax_checks'])
        if d['verdict']=='honeypot':
            assert int(d['buy']['acquired'])>0
            assert any((a['quote_out'] or 0)>0 for a in d['sell_attempts'])
        if row['venue']=='V4' and 'real_quote_raw' in d:
            logs=[];tick=None
            for path in set(d['evidence']):
                env=raw[path];req=env['request']
                if req['method']=='eth_getLogs' and req['params'][0]['topics'][0]==MODIFY:
                    logs+=env['response']['result']
                if req['method']=='eth_simulateV1':
                    for tx,rc in zip(req['params'][0]['blockStateCalls'][0]['calls'],env['response']['result'][0]['calls']):
                        if tx['to']==SV and tx['data'][:10]=='0x'+keccak(text='getSlot0(bytes32)')[:4].hex() and rc.get('status')=='0x1':
                            tick=decode(['uint160','int24','uint24','uint24'],bytes.fromhex(rc['returnData'][2:]))[1]
            assert tick is not None
            pos=collections.defaultdict(int)
            seen=set()
            for l in logs:
                identity=(l['transactionHash'],l['logIndex'])
                if identity in seen:continue
                seen.add(identity)
                lo,hi,delta,salt=decode(['int24','int24','int256','bytes32'],bytes.fromhex(l['data'][2:]))
                pos[(l['topics'][2],lo,hi,salt.hex())]+=delta
            active=sum(L for (_,lo,hi,_),L in pos.items() if lo<=tick<hi)
            assert active==int(d['active_liquidity']),(row['sample_id'],active,d['active_liquidity'])
            assert all(v>=0 for v in pos.values())
            v4checks.append(dict(sample_id=row['sample_id'],tick=tick,positions=sum(v>0 for v in pos.values()),
                                 active_liquidity=str(active),stateview_active_liquidity=d['active_liquidity'],
                                 real_quote_raw=d['real_quote_raw'],virtual_quote_raw=d['virtual_quote_raw'],match=True))
    pd.DataFrame(v4checks).to_csv(OUT/'v4_liquidity_checks.csv',index=False)
    checks.update(n_sample=len(sample),n_details=len(details),verdict_counts=dict(collections.Counter(d['verdict'] for d in details)),
                  v4_position_active_liquidity_matches=len(v4checks))
    # AST import audit: all project-code imports must be the C27 harness itself.
    imports=[]
    for p in (ROOT/'scripts/verify').glob('blind300_*.py'):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n,ast.ImportFrom):imports.append(n.module)
            elif isinstance(n,ast.Import):imports += [a.name for a in n.names]
    assert not any(x and (x.startswith('scripts.sim') or x.startswith('scripts.tier2') or x in ['classify','sim','tier2']) for x in imports)
    checks['production_imports']=False
    if (OUT/'report_manifest.json').exists():
        f=pd.read_csv(OUT/'comparisons.csv');s=pd.read_csv(OUT/'summary.csv',dtype={'year':str})
        assert len(f)==len(sample)
        assert (f.analysis_weight-(f.census_weight*f.cell_N/f.cell_n)).abs().max()<1e-7
        sm=json.loads((OUT/'verdict_freeze.json').read_text())
        assert sha(OUT/'verdicts.csv')==sm['verdicts_sha256']
        assert all(sha(ROOT/p)==h for p,h in sm['details'].items())
        assert not any('predicted' in k or 'category' in k for k in pd.read_csv(OUT/'verdicts.csv').columns)
        for domain in s.domain.unique():
            p=s[(s.year=='all')&(s.venue=='all')&(s.domain==domain)].iloc[0]
            cells=s[(s.year!='all')&(s.venue!='all')&(s.domain==domain)]
            for k in ['n','n_decidable','tp','fp','fn','tn','weighted_tp','weighted_fp','weighted_fn','weighted_tn']:
                assert abs(cells[k].sum()-p[k])<1e-6,(domain,k)
            if p.weighted_tp+p.weighted_fn:
                assert abs(p.weighted_sensitivity-p.weighted_tp/(p.weighted_tp+p.weighted_fn))<1e-12
            if p.weighted_tn+p.weighted_fp:
                assert abs(p.weighted_specificity-p.weighted_tn/(p.weighted_tn+p.weighted_fp))<1e-12
        # Recreate SRS membership from the pinned labels, independently of the
        # selection script and without changing the frozen sample.
        ff=pd.read_parquet(OUT/'inputs/token_results.parquet')
        ff['year']=ff.analysis_month.str[:4].astype(int)
        ff['predicted']=ff.category.map({'honeypot':'harm','high_tax':'harm','normal':'not-harm','owner_controlled':'not-harm'})
        ff=ff[ff.year.isin([2024,2025,2026])&ff.venue.isin(['V1/V2','V3','V4'])&ff.predicted.notna()&((ff.sim_block-ff.first_liquidity_block)==300)]
        expected=[]
        for (y,v,p),g in ff.groupby(['year','venue','predicted']):
            expected += sorted(g.sample_id,key=lambda sid:hashlib.sha256(f'{manifest["seed"]}|{y}|{v}|{p}|{sid}'.encode()).hexdigest())[:30]
        assert set(expected)==set(sample.sample_id)
        checks.update(frozen_before_label_join=True,summary_pooled_totals_match=True,selection_reproduced=True,
                      analysis_weight_max_error=float((f.analysis_weight-f.census_weight*f.cell_N/f.cell_n).abs().max()))
    from blind300_report import summarize
    fixture=[]
    for prediction,pop,truth in [('harm',100,True),('not-harm',10000,False)]:
        for i in range(30):
            fixture.append(dict(year=2026,venue='V3',predicted=prediction,verdict='high_tax' if truth else 'sellable',
                                binary=True,truth_harm=truth,truth_not_harm=not truth,analysis_weight=pop/30))
    fixture_cells=pd.DataFrame([dict(year=2026,venue='V3',predicted='harm',N=100),dict(year=2026,venue='V3',predicted='not-harm',N=10000)])
    fx=summarize(pd.DataFrame(fixture),2026,'V3','synthetic_fixture',fixture_cells)
    assert fx['weighted_sensitivity']==1 and fx['weighted_sensitivity_ci_low']<.1
    checks['synthetic_case_control_zero_miss_fixture_pass']=True
    checks['synthetic_fixture_sensitivity_lower_ci']=fx['weighted_sensitivity_ci_low']
    checks['status']='partial_pass' if args.partial else 'pass'
    (OUT/('partial_audit.json' if args.partial else 'audit.json')).write_text(json.dumps(checks,indent=2)+'\n')
    print(json.dumps(checks,indent=2))


if __name__=='__main__':main()
