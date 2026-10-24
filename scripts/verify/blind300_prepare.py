"""C27 deterministic frame selection. Labels leave this process only in sealed/.

No simulator/classifier imports. Do not open sealed labels until all replay verdicts
have been frozen by blind300_report.py.
"""
import hashlib
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/verify/blind300'
SEED = 202610220027


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if (OUT / 'sample.csv').exists():
        raise SystemExit('Refusing to replace an existing blind sample')
    (OUT / 'inputs').mkdir(parents=True, exist_ok=True)
    (OUT / 'sealed').mkdir(exist_ok=True)
    src = ROOT / 'data/tier2/v2/token_results.parquet'
    snapshot = OUT / 'inputs/token_results.parquet'
    snapshot.write_bytes(src.read_bytes())
    f = pd.read_parquet(snapshot)
    f['year'] = f.analysis_month.str[:4].astype(int)
    f['predicted'] = f.category.map({'honeypot':'harm', 'high_tax':'harm',
                                    'normal':'not-harm', 'owner_controlled':'not-harm'})
    f = f[f.year.isin([2024, 2025, 2026]) & f.venue.isin(['V1/V2','V3','V4']) &
          f.predicted.notna() & f.sim_block.notna() & f.first_liquidity_block.notna() &
          ((f.sim_block - f.first_liquidity_block) == 300)].copy()
    # Intentionally no measurable/floor filter, including V4. Pin the category
    # column before concurrent C26 changes; floor-masked nonbinary rows are not
    # silently turned into predictions.
    pools = pd.read_parquet(ROOT / 'data/census/dex_pools.parquet', columns=['pool_address','block'])
    init = pools.groupby('pool_address').block.min().to_dict()
    samples, labels, cells = [], [], []
    for year in [2024, 2025, 2026]:
        for venue in ['V1/V2','V3','V4']:
            for predicted in ['harm','not-harm']:
                g=f[(f.year==year)&(f.venue==venue)&(f.predicted==predicted)].copy()
                g['rank']=g.sample_id.map(lambda sid: hashlib.sha256(
                    f'{SEED}|{year}|{venue}|{predicted}|{sid}'.encode()).hexdigest())
                take=g.sort_values(['rank','sample_id']).head(30)
                n,N=len(take),len(g)
                cells.append(dict(year=year,venue=venue,predicted=predicted,N=N,n=n,
                                  census_weight=float(g.weight.sum())))
                for r in take.itertuples():
                    samples.append(dict(sample_id=r.sample_id,year=year,venue=venue,
                        token=r.address.lower(),pool=r.selected_pool.lower(),dex=r.selected_dex,
                        block=int(r.sim_block),first_liquidity_block=int(r.first_liquidity_block),
                        pool_created_block=int(init.get(r.selected_pool, r.first_liquidity_block))))
                    labels.append(dict(sample_id=r.sample_id,category=r.category,predicted=predicted,
                        census_weight=r.weight,cell_N=N,cell_n=n,inclusion_probability=n/N,
                        analysis_weight=r.weight*N/n,frame_measurable=bool(r.measurable),
                        frame_quote_liquidity_eth=r.quote_liquidity_eth,
                        frame_liquidity_method=r.quote_liquidity_method))
    s=pd.DataFrame(samples)
    s['order']=s.sample_id.map(lambda sid:hashlib.sha256(f'{SEED}|blind-order|{sid}'.encode()).hexdigest())
    s.sort_values('order').drop(columns='order').to_csv(OUT/'sample.csv',index=False)
    pd.DataFrame(labels).sort_values('sample_id').to_csv(OUT/'sealed/predicted_labels.csv',index=False)
    pd.DataFrame(cells).to_csv(OUT/'sealed/cells.csv',index=False)
    manifest=dict(seed=SEED,selection='smallest SHA256(seed|year|venue|predicted|sample_id), cap 30',
        blind_order='SHA256(seed|blind-order|sample_id)',n=len(s),
        input_path=str(src.relative_to(ROOT)),input_sha256=sha(snapshot),
        sample_sha256=sha(OUT/'sample.csv'),labels_sha256=sha(OUT/'sealed/predicted_labels.csv'),
        cells_sha256=sha(OUT/'sealed/cells.csv'),
        prediction_field='category; no explicit measurability filter at any venue',
        note='Pre-C26 pinned predictions; nonbinary floor/unknown labels excluded; no claim about excluded rows')
    (OUT/'sample_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if 'labels' not in k and 'cells' not in k}))


if __name__=='__main__':main()
