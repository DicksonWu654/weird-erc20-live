#!/usr/bin/env python3
"""Finite-population stress check of the v2 ratio bootstrap, synthetic only."""
import json
import numpy as np
import pandas as pd
from reestimate_v2 import Bootstrap,BM,COUNTS,OUT
from reaggregate_revision import base_interval


def run(replicates=200,bootstrap_reps=300):
    rng=np.random.default_rng(1720261023)
    month='2026-04';first=max(int(BM.loc[month,'first_block']),21850000)
    N=int(BM.loc[month,'last_block'])-first+1;n=int(COUNTS.loc[month,'n_blocks_sampled'])
    rows=[];details=[]
    for scenario in ('ordinary_clusters','rare_bursty_clusters'):
        sizes=rng.poisson(.06,N)
        if scenario=='rare_bursty_clusters':
            burst=rng.choice(N,size=max(1,N//1000),replace=False)
            sizes[burst]+=rng.integers(20,80,len(burst))
        blockp=rng.beta(1.5,8.5,N)
        if scenario=='rare_bursty_clusters':blockp[burst]=.85
        parent=np.repeat(np.arange(N),sizes)
        by=rng.binomial(1,blockp[parent]);bd=rng.binomial(1,.65,len(parent)).astype(bool)
        offsets=np.r_[0,np.cumsum(sizes)]
        P=6000;npool=400;py=rng.binomial(1,.22,P);pd_=rng.binomial(1,.7,P).astype(bool)
        truth={'all':float((by.sum()+py.sum())/(len(by)+P)),
               'measurable':float((by[bd].sum()+py[pd_].sum())/(bd.sum()+pd_.sum()))}
        covered={'all':0,'measurable':0}
        for rep in range(replicates):
            blocks=rng.choice(N,n,replace=False)
            bi=np.concatenate([np.arange(offsets[b],offsets[b+1]) for b in blocks])
            pi=rng.choice(P,npool,replace=False)
            f=pd.DataFrame(dict(month=month,source=['blocksample']*len(bi)+['pool_linked']*len(pi),
                                stratum=['B']*len(bi)+['A']*len(pi),
                                weight=np.r_[np.repeat(N/n,len(bi)),np.repeat(P/npool,len(pi))],
                                n_stratum_sampled=np.r_[np.repeat(n,len(bi)),np.repeat(npool,len(pi))],
                                N_stratum=np.r_[np.repeat(N,len(bi)),np.repeat(P,len(pi))],
                                creation_block=np.r_[first+parent[bi],first+pi]))
            y=np.r_[by[bi],py[pi]];domain=np.r_[bd[bi],pd_[pi]]
            boot=Bootstrap(f,bootstrap_reps,seed=1720261023+rep)
            for label,mask in [('all',np.ones(len(f),dtype=bool)),('measurable',domain)]:
                d=f[mask];v=y[mask];point=float(d.weight.to_numpy() @ v/d.weight.sum())
                den=boot.values(d.index);num=boot.values(d.index,v)
                vals=np.divide(num,den,out=np.full_like(num,np.nan),where=den>0)
                low,high=np.nanquantile(vals,[.025,.975])
                if point in (0.,1.) or high-low<1e-14:
                    low,high,_=base_interval(d,v,point,BM,COUNTS,frame=f)
                low=min(low,point);high=max(high,point)
                hit=low<=truth[label]<=high;covered[label]+=hit
                details.append(dict(scenario=scenario,replicate=rep,domain=label,truth=truth[label],
                                    share=point,ci_low=low,ci_high=high,covered=hit))
            if (rep+1)%50==0:print(scenario,rep+1,covered,flush=True)
        for label in covered:
            rows.append(dict(scenario=scenario,domain=label,replicates=replicates,
                             bootstrap_replicates=bootstrap_reps,empirical_coverage=covered[label]/replicates,
                             true_share=truth[label],synthetic_only=True,seed=1720261023))
    pd.DataFrame(details).to_csv(OUT/'bootstrap_check_replicates.csv',index=False)
    pd.DataFrame(rows).to_csv(OUT/'bootstrap_check.csv',index=False)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__=='__main__':run()
