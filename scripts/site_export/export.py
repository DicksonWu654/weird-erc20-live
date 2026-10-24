#!/usr/bin/env python3
"""Rebuild the publication site offline from the shipped compact data and source."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def require_release(root):
    path=root/'RELEASE-REPORT.json'
    if not path.is_file():raise ValueError('Deployable site must be built from the release copy: missing RELEASE-REPORT.json')
    report=json.loads(path.read_text())
    if report.get('deployment_scope')!='release-copy-only' or report.get('deployable_site')!='site/dist':
        raise ValueError('Deployable site must be the audited release copy')


def export():
    require_release(ROOT)
    from full_export import read_csv, compact_number
    from tier2_export import compact_shares, attach_ledger
    from revision_export import revised_aggregates
    from count_contract import bind_counts
    source=ROOT/'data/site'; target=ROOT/'site/dist'
    if target.exists():shutil.rmtree(target)
    target.mkdir(parents=True,exist_ok=True)
    for p in source.iterdir():
        dst=target/'data'/p.name
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,dst)
    index=json.loads((source/'index.json').read_text())
    numbers_path=ROOT/'article/numbers.json'
    numbers=json.loads(numbers_path.read_text())
    index['numbers']={k:v for k,v in numbers.items() if k in {'tier2_annual_table','tier2_quarterly_table','site_counts'} or
        (len(str(v.get('value','')))<100 and '{{' not in str(v.get('value','')) and '\n' not in str(v.get('value','')))}
    index['numbers_source']={'path':'article/numbers.json','sha256':hashlib.sha256(numbers_path.read_bytes()).hexdigest()}
    # The underlying compact tables are a publication snapshot. Refuse stale
    # joins instead of attaching a new source hash to old values.
    for r in index.get('aggregate_sources',[]):
        p=ROOT/r['path']
        if not p.is_file():raise ValueError('Missing publication input: '+r['path'])
        if r['path']!='article/numbers.json' and hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256']:
            raise ValueError('Compact publication data is stale: '+r['path'])
        if p.exists():r['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    # Rebuild current aggregate views from the public CSVs, preserving only
    # the compact per-chain matrix/evidence snapshot and editorial shell.
    for period,filename in [('month','monthly_shares.csv'),('quarter','quarterly_shares.csv'),('year','annual_shares.csv')]:
        path=ROOT/'data/tier2/v2'/filename
        rows=attach_ledger(compact_shares(read_csv(path),compact_number,period),period,path,numbers)
        if period=='month':index['scam_shares']=rows
        else:index['tier2'][period]=rows
    index['revision'],revision_sources=revised_aggregates(ROOT,read_csv,compact_number)
    sources={r['path']:r for r in index['aggregate_sources']}
    sources.update({r['path']:r for r in revision_sources})
    index['aggregate_sources']=[sources[k] for k in sorted(sources)]
    bind_counts(index,numbers)
    (target/'data/index.json').write_text(json.dumps(index,separators=(',',':'),ensure_ascii=False,sort_keys=True))
    # Rebuild the reviewed launch site against public inputs and public hashes.
    launch=ROOT/'site-v5'
    subprocess.run([sys.executable,str(launch/'tools/build.py')],check=True,cwd=ROOT)
    subprocess.run([sys.executable,str(launch/'tools/check_numbers.py')],check=True,cwd=ROOT)
    shutil.rmtree(target)
    shutil.copytree(launch/'dist',target)
    # Generated launch intermediates are not a second deployable artifact.
    shutil.rmtree(launch/'dist')
    shutil.rmtree(launch/'og')
    print('rebuilt site/dist from site-v5 and shipped publication data')

if __name__=='__main__':export()
