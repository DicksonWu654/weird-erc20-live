"""Join sealed labels only after independent trades; report conditional metrics."""
import json
import math
from pathlib import Path

import pandas as pd
from dust_rpc import V3


def wilson(k,n):
    if not n:return None,None
    z=1.959963984540054;p=k/n;c=(p+z*z/(2*n))/(1+z*z/n)
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return max(0,c-h),min(1,c+h)


def main():
    samples=pd.read_csv(V3/'blind_sample.csv');labels=pd.read_csv(V3/'sealed_labels.csv')
    details=[]
    for r in samples.to_dict('records'):
        p=V3/'blind_details'/(str(r['sample_id'])+'.json')
        if not p.exists():continue
        d=json.loads(p.read_text());details.append(dict(**r,independent_label=d['verdict'],buy_tax=d.get('buy_tax'),sell_tax=d.get('sell_tax'),
                                                   reason=d.get('reason',d.get('exception')),evidence_path=str(p.relative_to(V3.parents[2]))))
    out=pd.DataFrame(details).merge(labels,on='sample_id',validate='one_to_one')
    out.to_csv(V3/'blind_verdicts.csv',index=False)
    metrics=[]
    for venue,g in out.groupby('dex'):
        eligible=g[g.independent_label.isin(['sellable','honeypot','high_tax'])]
        for task,positive in [('honeypot',{'honeypot'}),('high_tax',{'high_tax'}),('honeypot_or_high_tax',{'honeypot','high_tax'})]:
            actual=eligible.independent_label.isin(positive);pred=eligible.production_label.isin(positive)
            tp=int((actual&pred).sum());fp=int((~actual&pred).sum());fn=int((actual&~pred).sum());tn=int((~actual&~pred).sum())
            for metric,k,n in [('precision',tp,tp+fp),('recall',tp,tp+fn),('specificity',tn,tn+fp),('negative_predictive_value',tn,tn+fn)]:
                lo,hi=wilson(k,n)
                metrics.append(dict(venue=venue,task=task,metric=metric,n_success=k,n_trials=n,estimate=k/n if n else None,
                                    ci_low=lo,ci_high=hi,tp=tp,fp=fp,fn=fn,tn=tn,n_sampled=len(g),n_decidable=len(eligible),
                                    no_liquidity=int(g.independent_label.eq('no_liquidity').sum()),unprobeable=int(g.independent_label.eq('unprobeable').sum()),
                                    ci_method='Wilson 95%; sample-conditional, unweighted',
                                    scope='2025-2026; all V3 production positives (cap30), SRS60 V3 normal, SRS30 V4 normal; owner-controlled excluded; no venue-population recall identification',
                                    limitation='undefined when denominator=0; no positive V4 stratum; Wilson does not correct case-control sampling'))
    pd.DataFrame(metrics).to_csv(V3/'recall.csv',index=False)
    print(pd.crosstab(out.stratum,out.independent_label).to_string())
    print(pd.DataFrame(metrics)[lambda x:x.task.eq('honeypot_or_high_tax')].to_string(index=False))


if __name__=='__main__':main()
