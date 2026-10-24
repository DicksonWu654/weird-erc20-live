#!/usr/bin/env python3
"""C23 manual case adjudications plus their saved evidence index."""
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
import pandas as pd
from delayed_cases import IDS,read_evidence,TOPICS
from delayed_rpc import ROOT
OUT=ROOT/'data/verify/delayed'
# Manual review of decoded interval calls/storage changes, both saved ladders,
# the reproduced late call tree, and targeted +1800/+7200 results.
REVIEW={
29157:('pool_switch_and_supply_change','Selector 0xdc72aa27 writes pool to slot 5 and clears a packed trading bit; two burn(address,uint256)-selector calls increase supply slot 6. Late exit fails in token with transfer-to-zero reason. Quote falls from 5.0319 to 0.01977 ETH through recorded swaps, with no Burn event.','observed_switch_and_token_revert'),
29393:('tax_setter','0xdef1186b call at block 16721327 writes slot 7 to 99. Sell tax changes from zero to about 99%; liquidity remains about 2.8 ETH.','observed_tax_state_change'),
29656:('token_arithmetic_or_dynamic_limit','Early buy/sell tax is 3%; late token transferFrom fails with SafeMath subtraction overflow. Slots 21/22/23 decrease during interval swap-back transactions. No named maxTx setter observed; the exact arithmetic/limit trigger remains unresolved.','trigger_unresolved'),
32374:('tax_setter','0x17e1df5b(0,0,50,50) writes tax slots 10..13, followed by ownership renunciation. Transfer tax rises from 4% to 100%; quoted exits yield no useful output.','observed_tax_state_change'),
33231:('preexisting_external_gate','Late token transferFrom calls dependency 0xdc5b.. and both revert. Dependency runtime is unchanged; interval dependency calls have selector 0x1dc437b1. No dependency storage change identified in those calls; exact trigger unresolved. Quote falls 4.3093 to 0.05360 ETH without an LP Burn.','trigger_unresolved'),
33621:('tax_setter','0xa2a957bb(...,99) changes slot 12 from 5 to 99; subsequent swaps use that fee state. Late sell tax is about 99%. Other calls redirect fee recipients.','observed_tax_state_change'),
34079:('token_arithmetic_or_dynamic_limit','No nonstandard token call in the complete interval. Late transferFrom panics with division/modulo by zero, while +1800 remains normal. Liquidity rises rather than drains; exact state-dependent arithmetic trigger unresolved.','trigger_unresolved'),
34481:('tax_setter','removeLimits() precedes 0x29e7d7cb(0,99), which writes slot 6 to 99. Early zero-tax owner-controlled result becomes about 99% sell tax.','observed_tax_state_change'),
36200:('preexisting_external_gate','Dependency 0x1de3.. receives 0xb43afc46 at block 20200597 and slot 3 changes 0 to 1. Late transferFrom calls that dependency and reverts; token shows arithmetic panic. Quote remains exactly 6.5335 ETH.','observed_external_switch_and_revert'),
37184:('tax_setter','0x29e7d7cb(0,99) writes slot 6 to 99. Launch trades were zero-tax; late sell tax is about 99%.','observed_tax_state_change'),
38374:('swapback_cost_threshold','Buy tax remains 5%; the first late sell executes two pool swaps, including a 200000-token swap-back that removes 0.472905 ETH before the probe sale. The quote/received estimator reports 10.0732%, while smaller exits are about 5%. This is a cost-threshold case, not evidence of a setter raising token tax to 10%.','observed_swapback_threshold_not_malicious_attribution'),
38574:('preexisting_external_gate','0xad9bdc3e redirects slot 13 to existing dependency 0xce098..; dependency receives address-list/configuration writes. Late sell fails when an empty-calldata payment to it consumes its 2300-gas stipend. Router has ample gas: this is a recipient callback failure, not the global sell cap.','observed_recipient_redirect_and_stipend_failure'),
40123:('preexisting_external_gate','0x798e5036 redirects token slot 4 to existing dependency 0x2100..; dependency is configured with token and pool addresses. Late exit reverts in that dependency. Quote falls from 17.06 to 0.01503 ETH without an LP Burn; legacy buy uses the simulator integer-lot retry of 1 ETH.','observed_recipient_redirect_and_revert'),
42282:('fee_recipient_eip7702_activation','Fee recipient has empty code at +300 and an EIP-7702 delegation at +7200. Its interval calls install address-list storage flags; the late sell reaches it through an empty-calldata 2300-gas ETH payment and reverts. +1800 is still normal. One LP Burn occurs but late quote remains 29.45 ETH.','observed_code_activation_and_failed_payment'),
42358:('fee_recipient_eip7702_activation','Previously empty fee recipient gains an EIP-7702 delegation and address-list storage flags. Its empty-calldata 2300-gas ETH payment reverts during late sell; +1800 is normal. Quote falls 35.78 to 0.776 ETH without an LP Burn.','observed_code_activation_and_failed_payment'),
42373:('fee_recipient_contract_activation','Previously empty fee recipient gains 3049 bytes of ordinary runtime. The late sell payment to it exhausts its 2300-gas stipend. +1800 is normal; no LP Burn. Code appearance and failure are proved; deployment route and intent are not attributed.','observed_code_activation_and_stipend_failure'),
42447:('fee_recipient_contract_activation','Previously empty fee recipient gains 1538 bytes of ordinary runtime. Late sell fails on the 2300-gas ETH payment to that recipient. +1800 is normal; no LP Burn. Deployment route and intent are not attributed.','observed_code_activation_and_stipend_failure'),
43283:('fee_recipient_eip7702_activation','Empty fee recipient gains EIP-7702 delegation; interval address-list/configuration calls write flags. A 2300-gas payment to it reverts in late sell, while +1800 is normal. Quote falls to 0.949 ETH without an LP Burn.','observed_code_activation_and_failed_payment'),
44188:('fee_recipient_eip7702_activation','Empty fee recipient gains EIP-7702 delegation to the recurrent 0x08bf.. implementation; address-list flags are written. Its 2300-gas payment reverts. Already harmful at +1800; no LP Burn.','observed_code_activation_and_failed_payment'),
44245:('fee_recipient_eip7702_activation','Empty fee recipient gains EIP-7702 delegation to 0x08bf.. and address-list flags. Late sell fails on its 2300-gas payment; already harmful at +1800. No LP Burn.','observed_code_activation_and_failed_payment'),
44387:('fee_recipient_eip7702_activation','Empty fee recipient gains EIP-7702 delegation to 0x08bf.. and address-list flags. Its 2300-gas payment reverts at +7200; +1800 remains normal. No LP Burn and quote rises from 3.11 to 8.85 ETH.','observed_code_activation_and_failed_payment'),
44547:('fee_recipient_eip7702_activation','Empty fee recipient gains EIP-7702 delegation to 0x08bf.. and address-list flags. Its 2300-gas payment reverts; already harmful at +1800. One Mint and one Burn occur; no insider/withdrawal attribution is inferred.','observed_code_activation_and_failed_payment'),
44809:('fee_recipient_eip7702_activation','Empty fee recipient gains EIP-7702 delegation to 0x935e..; empty-calldata payment with 2300 gas reverts during sell. Harmful at +1800; no LP Burn.','observed_code_activation_and_failed_payment'),
45070:('fee_recipient_eip7702_activation','Empty fee recipient gains EIP-7702 delegation to 0x08bf..; its empty-calldata 2300-gas payment reverts during sell. Harmful at +1800; no LP Burn.','observed_code_activation_and_failed_payment'),
}

def run():
 sims={}
 for l in (OUT/'diagnostic_sim_results.jsonl').read_text().splitlines():
  r=json.loads(l);sims[(r['sample_id'],r['offset_blocks'])]=r
 codes={r['sample_id']:r for r in json.loads((OUT/'dependency_code.json').read_text())}
 rows=[]
 for sid in IDS:
  p=OUT/'case_details'/f'{sid}.json';c=json.loads(p.read_text());mech,review,certainty=REVIEW[sid]
  assert c['collection_complete']
  trace=OUT/'trace_review/origin_details'/f'{sid}.json'
  tr=json.loads(trace.read_text()) if trace.exists() else {}
  dep=OUT/'dependency_details'/f'{sid}.json';de=json.loads(dep.read_text()) if dep.exists() else {}
  act=OUT/'activation_details'/f'{sid}.json';ac=json.loads(act.read_text()) if act.exists() else {}
  failed_dependency_call={}
  if tr and tr.get('origin') in ('token_dependency','out_of_gas'):
   tree=read_evidence(tr['evidence'][-2])['response']['result']
   def find_call(n):
    if n.get('to','').lower()==tr['origin_address']:return n
    for child in n.get('calls',[]):
     value=find_call(child)
     if value is not None:return value
   failed_dependency_call=find_call(tree) or {}
  assert not tr or (tr['status']=='ok' and tr.get('matched_saved_balance') and tr.get('reproduced_outer_revert'))
  assert sims[(sid,7200)]['primary_label'] in {'honeypot','high_tax'}
  evs=[c['early_evidence'],c['late_evidence']]+c['evidence']+tr.get('evidence',[])+de.get('evidence',[])+codes.get(sid,{}).get('evidence',[])+ac.get('evidence',[])
  evs += [sims[(sid,o)]['evidence'] for o in [1800,7200]]
  for ev in evs:read_evidence(ev)
  ns=[t for t in c['nonstandard_calls'] if t['action'].get('input','0x')!='0x']
  rows.append(dict(sample_id=sid,year=c['year'],venue='V1/V2',token=c['token'],pool=c['pool'],
       first_liquidity_block=c['first_liquidity_block'],early_block=c['early_block'],late_block=c['late_block'],
       late_offset=c['late_offset'],early_category=c['early_category'],late_category=c['late_category'],
       diagnostic_plus1800=sims[(sid,1800)]['primary_label'],diagnostic_exact_plus7200=sims[(sid,7200)]['primary_label'],
       quote_early_eth=int(c['early_quote_wei'])/1e18,quote_late_eth=int(c['late_quote_wei'])/1e18,
       early_successful_sells=sum(s.get('ok',False) for s in c['early_sells']),
       late_successful_sells=sum(s.get('ok',False) for s in c['late_sells']),
       late_taxes=json.dumps(c['late_taxes']),mechanism_group=mech,manual_finding=review,certainty=certainty,
       hand_reviewed=True,n_token_interval_traces=len(c['token_traces']),token_history_complete=c['trace_filter_complete'],
       dependency_history_complete=de.get('history_complete'),dependency_state_tx_limit_hit=de.get('state_tx_limit_hit'),
       token_state_tx_limit_hit=c.get('state_change_tx_limit_hit'),
       pool_burn_events=c['log_counts'].get('Burn(address,uint256,uint256,address)',0),
       nonstandard_selectors=json.dumps(sorted(set(t['action']['input'][:10] for t in ns))),
       candidate_state_change_txs=json.dumps([r['tx'] for r in c['state_change_transactions']]),
       sell_failure_origin=tr.get('origin'),failure_dependency=tr.get('origin_address'),
       dependency_failed_call_gas=int(failed_dependency_call.get('gas','0x0'),16) if failed_dependency_call else None,
       dependency_failed_call_value_wei=str(int(failed_dependency_call.get('value','0x0'),16)) if failed_dependency_call else None,
       dependency_failed_call_input=failed_dependency_call.get('input'),
       dependency_code_early_bytes=codes.get(sid,{}).get('code_early_nbytes'),
       dependency_code_late_bytes=codes.get(sid,{}).get('code_late_nbytes'),
       delegation_target=('0x'+codes[sid]['code_late'][8:]) if sid in codes and codes[sid]['code_late'].startswith('0xef0100') else None,
       recipient_code_activation_block=ac.get('nonempty_code_block'),recipient_code_activation_offset=ac.get('activation_offset'),
       review_details=str(p.relative_to(ROOT)),trace_details=str(trace.relative_to(ROOT)) if trace.exists() else None,
       dependency_details=str(dep.relative_to(ROOT)) if dep.exists() else None,
       activation_details=str(act.relative_to(ROOT)) if act.exists() else None,
       evidence=json.dumps(evs),scope='purposive internal cases; no population mechanism prevalence'))
 df=pd.DataFrame(rows);df.to_csv(OUT/'cases.csv',index=False)
 summary=dict(n_cases=len(df),year_counts=dict(Counter(df.year)),mechanisms=dict(Counter(df.mechanism_group)),
     plus1800_categories=dict(Counter(df.diagnostic_plus1800)),exact_plus7200_confirmed=int(df.diagnostic_exact_plus7200.isin(['honeypot','high_tax']).sum()),
     reproduced_failed_sell_traces=int(df.sell_failure_origin.notna().sum()),no_pool_burn_cases=int(df.pool_burn_events.eq(0).sum()),
     observed_delegation_implementations=dict(Counter(df.delegation_target.dropna())),
     modern_recipient_activations_zero_value_failure=int((df.mechanism_group.isin(['fee_recipient_eip7702_activation','fee_recipient_contract_activation'])&df.dependency_failed_call_value_wei.eq('0')).sum()),
     raw_evidence_references=sum(len(json.loads(v)) for v in df.evidence))
 (OUT/'cases_summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
 # Independently decode the borderline case's raw pool swaps and arithmetic.
 c=json.loads((OUT/'case_details/38374.json').read_text())
 env=next(e for e in read_evidence(c['late_evidence']) if e.get('label')=='phase2')
 plans=env['request']['params'][0]['blockStateCalls']
 blocks=[b for b in env['response']['result'] if b.get('calls')]
 found=None
 for bi,(plan,block) in enumerate(zip(plans,blocks)):
  for ci,(call,response) in enumerate(zip(plan['calls'],block['calls'])):
   if call.get('data','').startswith('0x791ac947'):
    swaps=[dict(pool=l['address'],amounts=[int(l['data'][2+i*64:66+i*64],16) for i in range(4)])
       for l in response.get('logs',[]) if TOPICS.get(l['topics'][0],'').startswith('Swap(')]
    found=dict(block_index=bi,call_index=ci,swaps=swaps);break
  if found:break
 s=c['late_sells'][0];tax=1-s['received_eth']/s['expected_eth']
 assert abs(tax-c['late_taxes']['sell'])<1e-6 and len(found['swaps'])==2
 check=dict(sample_id=38374,evidence=c['late_evidence'],decoded_first_sell=found,
            expected_eth_wei=str(s['expected_eth']),received_eth_wei=str(s['received_eth']),
            independently_computed_cost_fraction=tax,saved_tax_fraction=c['late_taxes']['sell'])
 (OUT/'manual_formula_checks.json').write_text(json.dumps(check,indent=2)+'\n')
if __name__=='__main__':run()
