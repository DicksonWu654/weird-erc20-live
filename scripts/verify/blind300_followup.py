"""Blind C27 approval diagnostics and numerical 10% boundary review.

Run after replay finishes and BEFORE label unsealing. No predicted-label reads.
"""
import json
from blind300_rpc import OUT,ROOT
from blind300_replay import Harness,EOA,P2,MAX,c,enc,approve,ok,uint,approve_ok


def main():
    if (OUT/'verdict_freeze.json').exists():
        raise SystemExit('Verdicts already frozen; refusing post-unblinding replay changes')
    done=[]
    for path in sorted((OUT/'details').glob('*.json')):
        d=json.loads(path.read_text());h=Harness({k:d[k] for k in ['sample_id','year','venue','token','pool','dex','block','first_liquidity_block','pool_created_block']})
        changed=False
        if d['verdict']=='unprobeable' and 'derived fresh EOA already holds token' in d['reason']:
            h=Harness(dict(h.row,allow_nonzero_initial_balance=True));old_evidence=d['evidence']
            d=h.verify();d['evidence']=old_evidence+d['evidence']
            d['initial_balance_caveat']='Derived EOA has a nonzero reported initial token balance; buy and sell use incremental wallet deltas and sell only the acquired increment. No token balance override.'
            changed=True
        if d['verdict']=='unprobeable' and d.get('buy',{}).get('ok') and int(d.get('buy',{}).get('acquired','0'))>0 and any((a.get('quote_out') or 0)>0 for a in d.get('sell_attempts',[])) and all(not a.get('approvals_ok') for a in d.get('sell_attempts',[])):
            h.metadata();prefix=h.buy_prefix();token=d['token'];spender=P2 if d['dex']=='uniswap_v4' else h.router();n=int(d['buy']['acquired']);checks=[]
            for amount in list(dict.fromkeys([0,1,n//2,n,MAX])):
                rc=h.sim(prefix+[h.trade(h.amount,True),approve(token,spender,amount),c(token,enc('allowance(address,address)',['address','address'],[EOA,spender]))])
                checks.append(dict(amount=str(amount),approval=rc[-2],allowance=uint(rc[-1]),ok=approve_ok(rc[-2])))
            follow=OUT/'followups';follow.mkdir(exist_ok=True)
            p=follow/(str(d['sample_id'])+'-approvals.json')
            evidence=dict(sample_id=d['sample_id'],spender=spender,checks=checks,evidence=h.evidence)
            p.write_text(json.dumps(evidence,indent=2)+'\n')
            d['approval_followup']=str(p.relative_to(ROOT));d['evidence']+=h.evidence
            if all(not ok(x['approval']) and x['approval'].get('error',{}).get('code')==3 for x in checks):
                d.update(verdict='honeypot',reason='Buy acquires tokens and positive sell quotes exist; token approve reverts for zero, one, half, full and maximum allowance, preventing the canonical-route exit.')
                changed=True
            elif all(not x['ok'] and not x['allowance'] for x in checks):
                d.update(verdict='honeypot',reason='Buy acquires tokens and positive sell quotes exist; token approve refuses zero/one/half/full/max and never grants canonical-route allowance.')
                changed=True
            d['approval_diagnostic_completed']=True
            if d['verdict']=='unprobeable' and any(x['amount']==str(MAX) and x['ok'] for x in checks):
                previous=d;h=Harness(dict(h.row,max_approval=True));d=h.verify()
                d['evidence']=previous['evidence']+d['evidence'];d['approval_followup']=previous['approval_followup']
                d['approval_mode_followup']='Exact/limited allowance reverts but MAX succeeds; reran complete fixed-input roundtrip using MAX ERC20 approval.'
            changed=True
        # Classify >=10% using exact integer products, avoiding a floating
        # boundary decision; save a separate one-base-unit rounding sensitivity.
        exact=[]
        for name,a in [('buy',d.get('buy',{}))]+[('sell',x) for x in d.get('sell_attempts',[]) if x.get('ok') and int(x.get('received_quote','0'))>0]:
            cr=a.get('core');spent=int(a.get('spent','0'));received=int(a.get('acquired' if name=='buy' else 'received_quote','0'))
            if cr and spent and cr[1]:
                denominator=spent*cr[1];numerator=denominator-cr[0]*received
                exact.append(dict(leg=name,tax_numerator=str(max(0,numerator)),tax_denominator=str(denominator),strict_ge_10_percent=10*numerator>=denominator,rounding_tolerance_product=str(spent+cr[1]),rounding_adjusted_ge_10_percent=10*(numerator+spent+cr[1])>=denominator,ge_10_percent=10*numerator>=denominator))
        tr=d.get('transfer',{})
        if tr.get('ok') and int(tr.get('requested','0')):
            den=int(tr['requested']);num=den-int(tr['received']);exact.append(dict(leg='transfer',tax_numerator=str(max(0,num)),tax_denominator=str(den),strict_ge_10_percent=10*num>=den,rounding_tolerance_product='1',rounding_adjusted_ge_10_percent=10*(num+1)>=den,ge_10_percent=10*num>=den))
        d['exact_tax_checks']=exact;d['tax_boundary_rounding']=any(x['rounding_adjusted_ge_10_percent'] and not x['strict_ge_10_percent'] for x in exact);changed=True
        if d['verdict'] in ['sellable','unprobeable'] and any(x['ge_10_percent'] for x in exact):
            was_unknown=d['verdict']=='unprobeable'
            d.update(verdict='high_tax',reason='Exact observed integer ratio establishes token/hook tax >=10%; core LP fee excluded.'+(' Sell outcome remains unresolved; the observed buy/transfer tax alone establishes the harm task.' if was_unknown else ' Positive wallet sell proceeds.'))
            if was_unknown:d['sell_outcome_unresolved']=True
        if d['verdict']=='high_tax' and not any(x['ge_10_percent'] for x in exact):
            d.update(verdict='sellable',reason='Positive wallet sell proceeds; exact observed integer tax ratios all below 10%; core LP fee excluded.')
        if d['verdict']=='sellable':
            d['reason']='Positive wallet sell proceeds at fixed 0.01 ETH buy; measured buy/sell/transfer taxes below 10%; core LP fee excluded.'
            if not tr.get('ok'):d['reason']+=' Plain transfer failed; no numeric transfer tax inferred.'
        d['rounding_adjusted_verdict']='high_tax' if d['verdict']!='honeypot' and any(x['rounding_adjusted_ge_10_percent'] for x in exact) else d['verdict']
        if changed:
            archive=OUT/'details/history';archive.mkdir(exist_ok=True)
            before=archive/(path.stem+'-before-followup.json')
            if not before.exists():before.write_bytes(path.read_bytes())
            path.write_text(json.dumps(d,indent=2)+'\n')
        done.append((d['sample_id'],d['verdict']))
    print('Blind follow-ups complete:',len(done))


if __name__=='__main__':main()
