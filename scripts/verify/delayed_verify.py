#!/usr/bin/env python3
"""Independent C23 source, ratio, domain and raw-hash checks."""
import gzip
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'data/verify/delayed'
sys.path.insert(0,str(ROOT/'scripts/sim'))
import classify as CL

def load(p):
 out={}
 for line in Path(p).read_text().splitlines():
  r=json.loads(line);out[int(r['sample_id'])]=r
 return out

def main():
 d=pd.read_parquet(ROOT/'data/tier2/v2/token_results.parquet').set_index('sample_id',drop=False)
 x=pd.read_parquet(OUT/'matched_rows.parquet').set_index('sample_id',drop=False)
 t=pd.read_csv(OUT/'transitions_by_year.csv',dtype={'year':str})
 w=pd.read_csv(OUT/'within_day_harm.csv',dtype={'year':str})
 early=load(ROOT/'data/tier2/v2/early_results.jsonl');late=load(ROOT/'data/tier2/v2/legacy_results.jsonl')
 issues=[];checked_labels=0;seen={};hash_checks=0
 def evidence(ev,source):
  nonlocal hash_checks
  if not isinstance(ev,dict) or not ev.get('raw'):return
  path=ROOT/ev['raw'];expected=ev.get('sha256')
  if (str(path),expected) in seen:return
  try:
   body=gzip.open(path,'rb').read() if path.suffix=='.gz' else path.read_bytes()
   h=hashlib.sha256(body).hexdigest();hash_checks+=1
   if expected and h!=expected:issues.append(dict(source=source,path=ev['raw'],error='hash_mismatch',expected=expected,actual=h))
   seen[(str(path),expected)]=h
  except Exception as exc:issues.append(dict(source=source,path=ev['raw'],error=repr(exc)))
 for sid,r in x[x.same_later].iterrows():
  assert r.weight==d.loc[sid,'weight']
  assert early[sid]['token'].lower()==late[sid]['token'].lower()
  assert early[sid]['pool'].lower()==late[sid]['pool'].lower()
  assert early[sid]['block']==int(d.loc[sid,'first_liquidity_block'])+300
  assert late[sid]['block']>early[sid]['block']
  for key,rec in [('early',early[sid]),('late',late[sid])]:
   label=CL.classify({**rec,'sells':rec.get('sells') or []},rec.get('control'),None,10**16)[1]
   expected=d.loc[sid,'category'] if key=='early' else r.legacy_category
   if label!=expected:issues.append(dict(source=f'{key}:{sid}',error='label_mismatch',expected=expected,actual=label))
   evidence(rec.get('evidence'),f'{key}:{sid}')
   for ev in rec.get('liquidity_reuse_evidence') or []:evidence(ev,f'{key}-liquidity:{sid}')
   checked_labels+=1
 for frame in [t,w]:
  valid=frame.n_sampled>0
  assert frame.loc[~valid,'share'].isna().all()
  assert frame.loc[valid,'share'].between(0,1).all()
  assert np.allclose(frame.loc[valid,'share'],frame.loc[valid,'positive_weight']/frame.loc[valid,'denominator_weight'])
  assert (frame.n_positive<=frame.n_sampled).all()
  assert (frame.loc[valid,'ci_low']<=frame.loc[valid,'share']+1e-12).all()
  assert (frame.loc[valid,'ci_high']>=frame.loc[valid,'share']-1e-12).all()
  assert (frame.loc[valid,'wilson_ci_low']<=frame.loc[valid,'share']+1e-12).all()
  assert (frame.loc[valid,'wilson_ci_high']>=frame.loc[valid,'share']-1e-12).all()
 # Cross-table probability identity: c's increment = launch-normal fraction * (1-drain) * a.
 identities=[]
 for (y,v,f),g in w[w.followup.ne('full_launch_frame')].groupby(['year','venue','followup']):
  a=g[g.metric.eq('harm_plus300')].iloc[0];b=g[g.metric.eq('harm_plus300_or_delayed')].iloc[0]
  z=g[g.metric.eq('increment_from_delayed')].iloc[0]
  if not a.n_sampled:continue
  assert a.denominator_weight==b.denominator_weight
  assert abs((b.share-a.share)-z.share)<1e-12
  transition=t[(t.year==y)&(t.venue==v)&(t.followup==f)&t.normal_definition.eq('normal_or_owner_controlled')]
  dt=transition[transition.metric.eq('delayed_trap_given_survives')].iloc[0]
  if dt.n_sampled:assert abs(dt.positive_weight-z.positive_weight)<1e-8
  identities.append((y,v,f))
 upstream_hash_checks=hash_checks
 cases=pd.read_csv(OUT/'cases.csv',dtype={'year':str})
 assert len(cases)>=20 and cases.hand_reviewed.all()
 assert set(cases.year)=={'2023','2024','2025','2026'}
 for row in cases.itertuples():
  assert row.early_category in {'normal','owner_controlled'}
  assert row.late_category in {'honeypot','high_tax'}
  assert row.quote_early_eth>=.01 and row.quote_late_eth>=.01
  assert row.token_history_complete
  for ev in json.loads(row.evidence):evidence(ev,f'case:{row.sample_id}')
 diagnostic={}
 for line in (OUT/'diagnostic_sim_results.jsonl').read_text().splitlines():
  rec=json.loads(line);diagnostic[(rec['sample_id'],rec['offset_blocks'])]=rec
 assert len(diagnostic)==48
 for (sid,offset),rec in diagnostic.items():
  assert rec['block']==int(d.loc[sid,'first_liquidity_block'])+offset
  assert rec['pool'].lower()==d.loc[sid,'selected_pool'].lower()
  assert rec['rules']['version']=='3.2'
  assert CL.classify(rec,rec.get('control'),None,10**16)[1]==rec['primary_label']
  evidence(rec['evidence'],f'diagnostic:{sid}:{offset}')
 for rec in json.loads((OUT/'activation_summary.json').read_text()):
  assert rec['nonempty_code_block']==rec['empty_code_block']+1
  assert rec['code_before']=='0x' and rec['code_after']!='0x'
  if rec['eip7702_designator']:
   assert rec['code_after'].startswith('0xef0100') and len(rec['code_after'])==48
 new_http=0;new_transport_errors=0;new_rpc_errors=0
 for p in (OUT/'raw').rglob('*.json.gz'):
  r=json.load(gzip.open(p,'rt'))
  if isinstance(r,list):continue
  assert 'request' in r and 'retrieved_at' in r
  new_http+=1
  new_transport_errors+=int('transport_error' in r)
  new_rpc_errors+=int('error' in (r.get('response') or {}))
  if 'response_body' in r:
   assert hashlib.sha256(r['response_body'].encode()).hexdigest()==r['body_sha256']
 # Primary full-frame launch point must reproduce the frozen publication table.
 ann=pd.read_csv(ROOT/'data/tier2/v2/annual_shares.csv',dtype={'year':str})
 ann=ann[ann.denominator.eq('measurable_pooled')&ann.label.eq('headline_trade_harm')]
 primary=w[w.venue.eq('All')&w.followup.eq('full_launch_frame')]
 chk=ann.merge(primary,on='year');assert np.allclose(chk.share_x,chk.share_y,equal_nan=True)
 manifest=json.loads((OUT/'analysis_manifest.json').read_text())
 assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in manifest['provenance'].items())
 out=dict(status='PASS' if not issues else 'FAIL',ratio_rows_checked=len(t)+len(w),
          cross_table_identities=len(identities),source_labels_checked=checked_labels,
          distinct_upstream_raw_evidence_hashes_checked=upstream_hash_checks,
          distinct_raw_evidence_hashes_checked=hash_checks,
          manually_reviewed_cases=len(cases),diagnostic_simulations_checked=len(diagnostic),
          new_http_attempts_checked=new_http,saved_transport_error_attempts=new_transport_errors,
          saved_rpc_error_attempts=new_rpc_errors,issues=issues)
 (OUT/'verification.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
 if issues:raise SystemExit(1)
if __name__=='__main__':main()
