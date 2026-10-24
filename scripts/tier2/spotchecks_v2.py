#!/usr/bin/env python3
"""Decode a reproducible cross-venue sample directly from saved RPC evidence."""
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from early_liquidity import ROOT,OUT,load_jsonl,positive


def decode(rec,sel):
    ev=rec['evidence'];body=gzip.open(ROOT/ev['raw'],'rb').read();envs=json.loads(body)
    phase2=next((e for e in envs if e.get('label') in ('phase2','phase2:v4')),None)
    statuses=[];gas=[]
    router=rec['pool_info']['router'].lower()
    if phase2:
        plans=phase2['request']['params'][0]['blockStateCalls']
        blocks=[b for b in phase2['response']['result'] if b.get('calls')]
        for plan,block in zip(plans,blocks):
            for call,response in zip(plan['calls'],block['calls']):
                if call['to'].lower()==router and int(call.get('gas','0x0'),16)==2500000 and int(call.get('value','0x0'),16)==0:
                    statuses.append(response.get('status')=='0x1');gas.append(int(response.get('gasUsed','0x0'),16))
    event=None
    for e in sel.get('evidence',[]):
        raw=json.loads(gzip.open(ROOT/e['raw'],'rb').read())
        values=raw.get('response',[])
        for response in values if isinstance(values,list) else [values]:
            val=response.get('result')
            logs=val.get('logs',[]) if isinstance(val,dict) else val if isinstance(val,list) else []
            for log in logs:
                if log.get('transactionHash')==sel.get('liquidity_tx') and int(log.get('logIndex','0x0'),16)==sel.get('liquidity_log_index'):
                    event=log
    buy=rec.get('buy') or {};sells=rec.get('sells') or [];pi=rec['pool_info']
    return dict(sample_id=rec['sample_id'],token=rec['token'],venue=rec.get('dex'),
                category=rec['primary_label'],reason=rec.get('label_reason'),
                first_liquidity_block=sel['first_liquidity_block'],sim_block=rec['block'],
                offset_correct=rec['block']==sel['first_liquidity_block']+300,
                positive_liquidity_event_saved=positive(sel['dex'],event) if event else False,
                quote_wei=pi.get('quote_liquidity_eth_equiv_wei'),
                quote_method=pi.get('quote_liquidity_method'),probe_wei=str(buy.get('amount_in_wei')),
                acquired_units=str(buy.get('received')),buy_ok=buy.get('ok'),
                buy_tax=(rec.get('taxes') or {}).get('buy'),sell_tax=(rec.get('taxes') or {}).get('sell'),
                transfer_tax=(rec.get('taxes') or {}).get('transfer'),
                raw_sell_attempts=len(statuses),raw_sell_successes=sum(statuses),
                saved_sell_statuses_match=statuses==[s['ok'] for s in sells],
                saved_sell_gas_match=gas==[s['gas'] for s in sells],
                positive_quote_sells=sum((s.get('expected_eth') or 0)>0 for s in sells),
                raw_sha256_match=hashlib.sha256(body).hexdigest()==ev['sha256'],evidence=ev['raw'],
                first_liquidity_evidence=json.dumps(sel['evidence']))


def run():
    from reestimate_v2 import classify_record,measured_record,liquidity_measurements
    records=load_jsonl(OUT/'early_results.jsonl');selections=load_jsonl(OUT/'liquidity_selection.jsonl')
    candidates=[measured_record(r,liquidity_measurements().get(r['sample_id'])) for r in records.values()
                if r.get('evidence') and (r.get('pool_info') or {}).get('router')]
    for r in candidates:
        label,why,flags,taxes=classify_record(r)
        r.update(primary_label=label,label_reason=why,flags=flags,taxes=taxes)
    candidates.sort(key=lambda r:hashlib.sha256(f'1720261024:{r["sample_id"]}'.encode()).hexdigest())
    chosen={}
    for r in candidates:
        group=(r.get('dex'),r['primary_label'])
        chosen.setdefault(group,r)
    sample={r['sample_id']:r for r in chosen.values()}
    for r in candidates:
        if len(sample)>=30:break
        sample.setdefault(r['sample_id'],r)
    decoded=[]
    for sid,r in sorted(sample.items()):
        row=decode(r,selections[sid])
        row['reserve_measurement_evidence']=json.dumps((liquidity_measurements().get(sid) or {}).get('evidence',[]))
        row['reserve_measurement_checkpoint']='data/tier2/v2/liquidity_v21_early.jsonl'
        decoded.append(row)
    table=pd.DataFrame(decoded)
    for col in ('offset_correct','positive_liquidity_event_saved','saved_sell_statuses_match','saved_sell_gas_match','raw_sha256_match'):
        assert table[col].all(),table[~table[col]][['sample_id',col]].to_dict('records')
    table.to_csv(OUT/'spotchecks.csv',index=False)
    print(table.drop(columns=['evidence','first_liquidity_evidence','reserve_measurement_evidence']).to_string(index=False))


if __name__=='__main__':run()
