"""Follow up a failed approval independently of the attempted sell size."""
import json
import pandas as pd
from dust_rpc import V3,enc,uint
import v3_blind as h


def main():
    row=next(r for r in pd.read_csv(V3/'blind_sample.csv').to_dict('records') if r['sample_id']==47723)
    d=json.loads((V3/'blind_details/47723.json').read_text());k=tuple(d['pool_key']);q=d['quote'];amt=d['buy_attempts'][0]['amount'];bought=d['buy_attempts'][0]['acquired']
    out=[]
    for approval in [0,1,bought//2,bought,2**256-1]:
        calls=h.trade(row,k,q,amt,True)+[h.c(row['token'],enc('approve(address,uint256)',['address','uint256'],[h.V3R,approval]))]
        rr,p=h.simulate(row['block'],[dict(calls=calls)])
        rc=rr[0]['calls'][-1]
        out.append(dict(amount=str(approval),ok=h.ok(rc),error=rc.get('error'),return_data=rc.get('returnData'),evidence=p))
    result=dict(sample_id=47723,approval_checks=out,conclusion='honeypot' if all(not a['ok'] and a.get('error',{}).get('code')==3 for a in out) else 'unprobeable')
    (V3/'approval_checks.json').write_text(json.dumps(result,indent=2))
    if result['conclusion']=='honeypot':
        d.update(verdict='honeypot',reason='Buy acquires tokens; positive sell quotes exist; token approve reverts for 0, 1, half, full and max allowance. Token-side approval restriction prevents the tested fresh EOA from selling.',approval_followup='data/verify/v3/approval_checks.json')
        (V3/'blind_details/47723.json').write_text(json.dumps(d,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
