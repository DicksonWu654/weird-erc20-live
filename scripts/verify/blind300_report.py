"""C27 freeze first; only then unseal labels and report design-weighted metrics.

Wilson intervals for raw precision/NPV. Approximate design-weighted Wilson
intervals use Kish n. Sensitivity/specificity intervals propagate Bonferroni
Wilson component limits across predicted-label sampling cells; this preserves
uncertainty about missed positives when no miss was observed in sampled negatives.
"""
import argparse
import hashlib
import json
import math
from datetime import datetime,timezone
from statistics import NormalDist
import pandas as pd
from blind300_rpc import ROOT,OUT

HARM={'honeypot','high_tax'}
BINARY=HARM|{'sellable'}
KEY=['year','venue','predicted']


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ratio(a,b):return a/b if b else math.nan
def kish(w):return float(w.sum()**2/(w*w).sum()) if len(w) and (w*w).sum() else 0.
def wilson(p,n,z=1.959963984540054):
    if not n or not math.isfinite(p):return math.nan,math.nan
    den=1+z*z/n;center=(p+z*z/(2*n))/den
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return max(0,center-half),min(1,center+half)


def freeze():
    dest=OUT/'verdict_freeze.json'
    if dest.exists():
        old=json.loads(dest.read_text())
        assert sha(OUT/'verdicts.csv')==old['verdicts_sha256']
        assert all(sha(ROOT/p)==h for p,h in old['details'].items())
        print('Existing frozen verdicts verified; no rewrite.')
        return
    sample=pd.read_csv(OUT/'sample.csv')
    manifest=json.loads((OUT/'sample_manifest.json').read_text())
    assert sha(OUT/'sample.csv')==manifest['sample_sha256']
    result=[];details={}
    for row in sample.to_dict('records'):
        p=OUT/'details'/f'{row["sample_id"]}.json'
        d=json.loads(p.read_text())
        assert d['verdict'] in BINARY|{'unprobeable','could_not_buy','no_liquidity'}
        assert all(d[k]==row[k] for k in row)
        assert d['harness_version']==2
        assert d['block']-d['first_liquidity_block']==300
        details[str(p.relative_to(ROOT))]=sha(p)
        result.append(dict(**row,verdict=d['verdict'],reason=d['reason'],
            buy_tax=d.get('buy_tax'),sell_tax=d.get('sell_tax'),transfer_tax=d.get('transfer_tax'),
            quote=d.get('quote'),quote_liquidity_raw=d.get('quote_liquidity_raw'),
            quote_liquidity_eth=d.get('quote_liquidity_eth'),virtual_quote_raw=d.get('virtual_quote_raw'),
            virtual_quote_eth=d.get('virtual_quote_eth'),real_quote_raw=d.get('real_quote_raw'),
            real_quote_eth=d.get('real_quote_eth'),lp_fee=d.get('lp_fee'),
            tax_rounding_changes_verdict=d.get('rounding_adjusted_verdict',d['verdict'])!=d['verdict'],
            tax_boundary_rounding=d.get('tax_boundary_rounding',False),rounding_adjusted_verdict=d.get('rounding_adjusted_verdict',d['verdict']),
            strict_observed_tax_ge10=any(x.get('strict_ge_10_percent',False) for x in d.get('exact_tax_checks',[])),
            probe_eth_wei=d['probe_eth_wei'],execution_block=d['synthetic_execution_block'],
            evidence_path=str(p.relative_to(ROOT))))
    pd.DataFrame(result).to_csv(OUT/'verdicts.csv',index=False)
    m=dict(frozen_at_utc=datetime.now(timezone.utc).isoformat(),sample_sha256=sha(OUT/'sample.csv'),verdicts_sha256=sha(OUT/'verdicts.csv'),details=details,
           sources={str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'scripts/verify').glob('blind300_*.py')},
           statement='All independent verdicts frozen before report() reads sealed predicted labels.')
    dest=OUT/'verdict_freeze.json'
    if dest.exists():
        old=json.loads(dest.read_text())
        assert old['verdicts_sha256']==m['verdicts_sha256'],'Never silently refreeze after unblinding'
    else:dest.write_text(json.dumps(m,indent=2)+'\n')
    print('Frozen',len(result),'verdicts; no labels read.')


def weighted_interval(g,truth_col):
    if g.empty:return math.nan,math.nan,math.nan,0.
    w=g.analysis_weight;p=ratio(float((w*g[truth_col]).sum()),float(w.sum()));n=kish(w)
    lo,hi=wilson(p,n)
    return p,lo,hi,n


def components(g):
    """HT-estimated decidable mass and weighted harm score in each sampling cell."""
    groups=list(g.groupby(KEY,dropna=False))
    z=NormalDist().inv_cdf(1-.05/(2*max(1,len(groups))))
    out=[]
    for key,c in groups:
        w=c.analysis_weight;mass=float(w.sum());p=float((w*c.truth_harm).sum()/mass)
        lo,hi=wilson(p,kish(w),z)
        out.append(dict(predicted=key[2],mass=mass,p=p,lo=lo,hi=hi))
    return out


def summarize(g,year,venue,domain,cells):
    b=g[g.binary].copy();pos=b[b.predicted=='harm'];neg=b[b.predicted=='not-harm']
    tp=int(pos.truth_harm.sum());fp=len(pos)-tp;fn=int(neg.truth_harm.sum());tn=len(neg)-fn
    def weight(c,truth):return float(c.loc[c.truth_harm==truth,'analysis_weight'].sum())
    TP,FP,FN,TN=weight(pos,True),weight(pos,False),weight(neg,True),weight(neg,False)
    row=dict(year=year,venue=venue,domain=domain,n=len(g),n_decidable=len(b),
        n_harm_predictions=int((g.predicted=='harm').sum()),n_not_harm_predictions=int((g.predicted=='not-harm').sum()),
        tp=tp,fp=fp,fn=fn,tn=tn,weighted_tp=TP,weighted_fp=FP,weighted_fn=FN,weighted_tn=TN,
        sampled_census_mass=float(g.analysis_weight.sum()),decidable_census_mass=float(b.analysis_weight.sum()),
        point_scope='known binary outcomes only; excludes no_liquidity/could_not_buy/unprobeable')
    row['n_tax_boundary_rounding']=int(g.tax_boundary_rounding.sum()) if 'tax_boundary_rounding' in g else 0
    row['n_tax_rounding_changes_verdict']=int(g.tax_rounding_changes_verdict.sum()) if 'tax_rounding_changes_verdict' in g else 0
    for verdict in ['sellable','honeypot','high_tax','no_liquidity','could_not_buy','unprobeable']:
        row['n_'+verdict]=int((g.verdict==verdict).sum())
    for name,k,n,sub,col in [('precision_harm',tp,tp+fp,pos,'truth_harm'),('npv_not_harm',tn,tn+fn,neg,'truth_not_harm')]:
        p=ratio(k,n);lo,hi=wilson(p,n)
        row.update({name:p,name+'_ci_low':lo,name+'_ci_high':hi})
        wp,wl,wh,ne=weighted_interval(sub,col)
        row.update({'weighted_'+name:wp,'weighted_'+name+'_ci_low':wl,'weighted_'+name+'_ci_high':wh,
                    name+'_kish_n':ne})
    row['weighted_sensitivity']=ratio(TP,TP+FN);row['weighted_specificity']=ratio(TN,TN+FP)
    co=components(b)
    tplo=sum(x['mass']*x['lo'] for x in co if x['predicted']=='harm')
    tphi=sum(x['mass']*x['hi'] for x in co if x['predicted']=='harm')
    fnlo=sum(x['mass']*x['lo'] for x in co if x['predicted']=='not-harm')
    fnhi=sum(x['mass']*x['hi'] for x in co if x['predicted']=='not-harm')
    tnlo=sum(x['mass']*(1-x['hi']) for x in co if x['predicted']=='not-harm')
    tnhi=sum(x['mass']*(1-x['lo']) for x in co if x['predicted']=='not-harm')
    fplo=sum(x['mass']*(1-x['hi']) for x in co if x['predicted']=='harm')
    fphi=sum(x['mass']*(1-x['lo']) for x in co if x['predicted']=='harm')
    if TP+FN:
        row['weighted_sensitivity_ci_low']=ratio(tplo,tplo+fnhi)
        row['weighted_sensitivity_ci_high']=ratio(tphi,tphi+fnlo)
    if TN+FP:
        row['weighted_specificity_ci_low']=ratio(tnlo,tnlo+fphi)
        row['weighted_specificity_ci_high']=ratio(tnhi,tnhi+fplo)
    row['sensitivity_specificity_ci_method']='approximate 95% Bonferroni Wilson component envelope; Kish weighted cell n; HT mass'
    # Identification bounds keep every unresolved observation as unresolved.
    up=float(g.loc[(~g.binary)&(g.predicted=='harm'),'analysis_weight'].sum())
    un=float(g.loc[(~g.binary)&(g.predicted=='not-harm'),'analysis_weight'].sum())
    row.update(weighted_unresolved_pred_harm=up,weighted_unresolved_pred_not_harm=un,
        precision_unknown_lower=ratio(TP,TP+FP+up),precision_unknown_upper=ratio(TP+up,TP+FP+up),
        npv_unknown_lower=ratio(TN,TN+FN+un),npv_unknown_upper=ratio(TN+un,TN+FN+un),
        sensitivity_unknown_lower=ratio(TP,TP+FN+un),sensitivity_unknown_upper=ratio(TP+up,TP+up+FN),
        specificity_unknown_lower=ratio(TN,TN+FP+up),specificity_unknown_upper=ratio(TN+un,TN+un+FP))
    cc=cells
    if year!='all':cc=cc[cc.year==year]
    if venue!='all':cc=cc[cc.venue==venue]
    row['frame_harm_N']=int(cc.loc[cc.predicted=='harm','N'].sum())
    row['frame_not_harm_N']=int(cc.loc[cc.predicted=='not-harm','N'].sum())
    return row


def report():
    freeze_manifest=json.loads((OUT/'verdict_freeze.json').read_text())
    assert sha(OUT/'verdicts.csv')==freeze_manifest['verdicts_sha256']
    assert all(sha(ROOT/p)==h for p,h in freeze_manifest['details'].items())
    sm=json.loads((OUT/'sample_manifest.json').read_text())
    assert sha(OUT/'sealed/predicted_labels.csv')==sm['labels_sha256']
    assert sha(OUT/'sealed/cells.csv')==sm['cells_sha256']
    # FIRST label read in this workflow, after verification above.
    unsealed_at=datetime.now(timezone.utc).isoformat()
    labels=pd.read_csv(OUT/'sealed/predicted_labels.csv');cells=pd.read_csv(OUT/'sealed/cells.csv')
    f=pd.read_csv(OUT/'verdicts.csv').merge(labels,on='sample_id',validate='one_to_one')
    f['binary']=f.verdict.isin(BINARY);f['truth_harm']=f.verdict.isin(HARM);f['truth_not_harm']=f.verdict=='sellable'
    f['virtual_floor']=(f.quote_liquidity_eth>=.01)
    f.loc[f.venue=='V4','virtual_floor']=f.loc[f.venue=='V4','virtual_quote_eth']>=.01
    f['real_floor']=f.quote_liquidity_eth>=.01
    f.loc[f.venue=='V4','real_floor']=f.loc[f.venue=='V4','real_quote_eth']>=.01
    f.to_csv(OUT/'comparisons.csv',index=False)
    scopes=[(y,v) for y in [2024,2025,2026] for v in ['V1/V2','V3','V4']]
    scopes += [(y,'all') for y in [2024,2025,2026]]+ [('all',v) for v in ['V1/V2','V3','V4','all']]
    rows=[]
    for domain in ['all_sample','virtual_0.01_eth','real_0.01_eth','all_sample_nominal_rounding','real_0.01_eth_nominal_rounding']:
        for y,v in scopes:
            g=f
            if y!='all':g=g[g.year==y]
            if v!='all':g=g[g.venue==v]
            if domain.startswith('virtual'):g=g[g.virtual_floor]
            elif domain.startswith('real'):g=g[g.real_floor]
            if domain.endswith('nominal_rounding'):
                g=g.copy();g['verdict']=g.rounding_adjusted_verdict
                g['binary']=g.verdict.isin(BINARY);g['truth_harm']=g.verdict.isin(HARM);g['truth_not_harm']=g.verdict=='sellable'
            rows.append(summarize(g,y,v,domain,cells))
    s=pd.DataFrame(rows);s.to_csv(OUT/'summary.csv',index=False)
    cells.to_csv(OUT/'sampling_cells.csv',index=False)
    manifest=dict(unsealed_at_utc=unsealed_at,sample_sha256=sha(OUT/'sample.csv'),labels_sha256=sm['labels_sha256'],
        verdict_freeze_sha256=sha(OUT/'verdict_freeze.json'),summary_sha256=sha(OUT/'summary.csv'),
        analysis_weight='original census weight * predicted-cell N/n (two-stage HT expansion)',
        confidence='95% Wilson for raw precision/NPV; Kish Wilson for weighted precision/NPV; Bonferroni Wilson component envelope for sensitivity/specificity',
        limitations='Known outcomes only; unknown assignment ranges are identification bounds, not CIs. Approximate weighted CIs condition on original sampled census frame; omit census estimation, dependence, and classification error. No FPC applied, including fully replayed cells.',
        label_unsealed_after_verdict_freeze=True)
    (OUT/'report_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(s.loc[(s.year=='all')&(s.venue=='all'),['domain','n','n_decidable','tp','fp','fn','tn','weighted_precision_harm','weighted_npv_not_harm','weighted_sensitivity','weighted_sensitivity_ci_low','weighted_specificity']].to_string(index=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--freeze',action='store_true');ap.add_argument('--report',action='store_true');a=ap.parse_args()
    if a.freeze:freeze()
    if a.report:report()
