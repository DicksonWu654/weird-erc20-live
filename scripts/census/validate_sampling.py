"""Method-vs-method validation on months covered by BOTH the live trace_block block sample and a complete enumeration
(Paradigm for blocks < 16.8M, Zellic for 16.8M .. 21.85M).
(1) block level: #ERC-20-like tokens found by RPC trace in sampled blocks vs the complete table (set equality of addresses);
    #successful create traces vs #contract rows (Paradigm counts creates inside reverted txs: 'ghosts');
(2) month level: sampled estimate (+95% CI, normal & bootstrap, decomposition for 'all') vs the exact count.
Writes data/census/evidence/validate_sampling_blocks.json and data/census/validation_sampling_months.csv"""
import json, pathlib, sys
import numpy as np, pandas as pd, duckdb
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import estimators as E
ROOT = pathlib.Path(__file__).resolve().parents[2]
C = ROOT / "data/census/cache"
sb = pd.read_parquet(C / "sampled_blocks.parquet"); st = pd.read_parquet(C / "sampled_tokens.parquet")
bm = pd.read_csv(ROOT / "data/census/blockmap.csv").set_index("month")
LP = ("lp_v2", "lp_v1", "lp_balancer")
con = duckdb.connect()
pt = con.execute(f"SELECT lower('0x'||hex(contract_address)) address, block_number block, kind FROM read_parquet('{C}/paradigm_tokens.parquet') WHERE block_number >= 3000000 AND kind <> 'lp_v1'").df()
zt = pd.read_parquet(C / "zellic_tokens.parquet")[["address", "block", "kind"]]
truth = pd.concat([pt, zt], ignore_index=True)
pblk = con.execute(f"SELECT block_number block, n_with_code n FROM read_parquet('{C}/paradigm_slim/blocks_*.parquet')").df().set_index("block").n
zblk = pd.read_parquet(C / "zellic_blocks.parquet").set_index("block").n_rows
pools = pd.read_parquet(ROOT / "data/census/dex_pools.parquet")
v2 = pools[pools.dex.isin(["uniswap_v2", "sushiswap_v2"])].groupby("block").size()
months = sorted(m for m in sb.month.unique() if bm.loc[m, "last_block"] < 21_850_000 and bm.loc[m, "first_block"] >= 2_900_000)
out, rows = {}, []
for m in months:
    s = sb[(sb.month == m)].copy()
    lo, hi = int(bm.loc[m, "first_block"]), int(bm.loc[m, "last_block"])
    N = hi - lo + 1
    src = pblk if hi < 16_800_000 else zblk
    s["truth_creates"] = src.reindex(s.block).fillna(0).to_numpy()
    tm = truth[truth.block.between(lo, hi)]
    A = set(tm[tm.block.isin(s.block)].address); B = set(st[st.month == m].address)
    out[m] = dict(truth_source="paradigm" if hi < 16_800_000 else "zellic", blocks=len(s), blocks_create_count_equal=int((s.n_create_ok == s.truth_creates).sum()),
                  rpc_create_total=int(s.n_create_ok.sum()), truth_create_rows_total=int(s.truth_creates.sum()),
                  tokens_rpc=len(B), tokens_truth=len(A), tokens_both=len(A & B), only_rpc=len(B - A), only_truth=len(A - B))
    idx = np.arange(lo, hi + 1)
    for series, f, col in [("all", lambda k: True, "n_tok_all"), ("excl_lp", lambda k: k not in LP, "n_tok_excl_lp")]:
        tr = int(tm.kind.map(f).sum()); y = s[col].to_numpy(float)
        e, l, h = E.srs(y, N); eb, lb, hb = E.boot(y, N, rng=np.random.default_rng(3))
        row = dict(month=m, series=series, n_blocks=len(y), truth=tr, est_srs=e, lo_srs=l, hi_srs=h, lo_boot=lb, hi_boot=hb,
                   covered_srs=bool(l <= tr <= h), covered_boot=bool(lb <= tr <= hb), rel_err=e / tr - 1)
        if series == "all":
            xs = v2.reindex(s.block).fillna(0).to_numpy(float); Xtot = v2.reindex(idx).fillna(0).sum()
            ed, ld, hd = E.srs(y - xs, N); row.update(est_dec=Xtot + ed, lo_dec=Xtot + ld, hi_dec=Xtot + hd, covered_dec=bool(Xtot + ld <= tr <= Xtot + hd), rel_err_dec=(Xtot + ed) / tr - 1)
        rows.append(row)
pd.DataFrame(rows).to_csv(ROOT / "data/census/validation_sampling_months.csv", index=False)
(ROOT / "data/census/evidence/validate_sampling_blocks.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1)); print(pd.DataFrame(rows).round(3).to_string())
