"""Monte-Carlo validation of the block-sampling estimators against the complete Paradigm enumeration (pre-2023-03 months).
For each month, series and sample size n: R replicate simple random samples of n blocks; report coverage of the true total
by the nominal-95% CI, mean relative bias and mean relative CI half-width, for srs / boot / reg estimators.
Writes data/census/evidence/estimator_sim.csv"""
import pathlib, sys
import numpy as np, pandas as pd, duckdb
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import estimators as E
ROOT = pathlib.Path(__file__).resolve().parents[2]
C = ROOT / "data/census/cache"
bm = pd.read_csv(ROOT / "data/census/blockmap.csv"); bm = bm[bm.last_block < 16800000]
con = duckdb.connect()
tok = con.execute(f"""SELECT block_number, count(*) all_, count(*) FILTER (WHERE kind NOT IN ('lp_v2','lp_v1','lp_balancer')) exlp,
    count(*) FILTER (WHERE kind IN ('lp_v2','lp_v1','lp_balancer')) lp FROM read_parquet('{C}/paradigm_tokens.parquet') GROUP BY 1""").df().set_index("block_number")
pools = pd.read_parquet(ROOT / "data/census/dex_pools.parquet")
pools = pools[pools.dex.isin(["uniswap_v2", "sushiswap_v2"])].groupby("block").size()
rng = np.random.default_rng(11)
rows = []
R, B = 100, 100
for _, r in bm.iterrows():
    N = r.n_blocks
    idx = np.arange(r.first_block, r.last_block + 1)
    Y = {s: tok[s].reindex(idx).fillna(0).to_numpy(float) for s in ("all_", "exlp", "lp")}
    X = pools.reindex(idx).fillna(0).to_numpy(float); Xtot = X.sum()
    for s, y in Y.items():
        truth = y.sum()
        if truth == 0: continue
        for n in (500, 1000, 2000):
            acc = {k: [] for k in ("srs", "boot", "reg", "dec")}
            for _ in range(R):
                ii = rng.choice(N, n, replace=False)
                ys, xs = y[ii], X[ii]
                acc["srs"].append(E.srs(ys, N))
                acc["reg"].append(E.reg(ys, xs, N, Xtot) if Xtot > 0 else E.srs(ys, N))
                if s in ("all_", "lp"):  # decomposition: exact known part (pair-creation events) + sampled remainder
                    e0, l0, h0 = E.srs(ys - xs, N); acc["dec"].append((Xtot + e0, Xtot + l0, Xtot + h0))
                if n == 1000: acc["boot"].append(E.boot(ys, N, B=B, rng=rng))
            for k, v in acc.items():
                if not v: continue
                a = np.array(v)
                rows.append(dict(month=r.month, series=s, n=n, est=k, truth=truth, coverage=float(((a[:, 1] <= truth) & (truth <= a[:, 2])).mean()),
                                 rel_bias=float(a[:, 0].mean() / truth - 1), rel_halfwidth=float(((a[:, 2] - a[:, 1]) / 2 / truth).mean())))
    print(r.month, flush=True)
d = pd.DataFrame(rows); d.to_csv(ROOT / "data/census/evidence/estimator_sim.csv", index=False)
d["year"] = d.month.str[:4]
print(d[(d.n == 1000) & d.est.isin(["srs", "dec"])].groupby(["series", "year", "est"]).agg(cov=("coverage", "mean"), hw=("rel_halfwidth", "median")).unstack("est").round(2).to_string())
g = d.groupby(["series", "n", "est"]).agg(coverage=("coverage", "mean"), rel_bias=("rel_bias", "mean"), rel_halfwidth=("rel_halfwidth", "median"), months=("month", "nunique"))
print(g.to_string())
