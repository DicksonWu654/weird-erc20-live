"""Assemble data/census/monthly_counts.csv (long format, one row per month x series).

 blocks < 16.8M      : complete enumeration (Paradigm contract-creation traces, CC0) + static classifier -> exact under the definition
 16.8M .. 21.85M     : complete enumeration (Zellic all-ethereum-contracts snapshot, bytecode hash per contract) + same classifier
 >= 21.85M (2025-02-15): stratified random block sampling (trace_block); a straddling month = exact head + sampled tail
Series: all | excl_lp | excl_lp_vault_receipt | lp | factory_created_all (all ERC-20-like created by a contract rather than top-level tx).
 'all' and 'lp' use the decomposition estimator: exact V2/Sushi PairCreated count (complete event scan; 100% pair recall verified)
 + sampled remainder; 'excl_lp' and 'excl_lp_vault_receipt' use the plain expansion estimator N*mean(y) with normal-theory 95% CI
 (empirical coverage of that CI ~94% in Monte-Carlo on 74 fully-enumerated months; see estimator_sim.csv).
Columns: month, series, n_est, ci_low, ci_high, n_blocks_sampled, n_tokens_in_sample, ci_low_boot, ci_high_boot, method.
"""
import pathlib, sys
import numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import estimators as E
ROOT = pathlib.Path(__file__).resolve().parents[2]
C = ROOT / "data/census/cache"
P_END, Z_END = 16_800_000, 21_850_000     # first block not covered by Paradigm / Zellic
bm = pd.read_csv(ROOT / "data/census/blockmap.csv")
pm = pd.read_csv(ROOT / "data/census/paradigm_monthly.csv").set_index("month")
zm = pd.read_csv(ROOT / "data/census/zellic_monthly.csv").set_index("month")
sb = pd.read_parquet(C / "sampled_blocks.parquet") if (C / "sampled_blocks.parquet").exists() else pd.DataFrame(columns=["month", "block"])
pools = pd.read_parquet(ROOT / "data/census/dex_pools.parquet")
v2 = pools[pools.dex.isin(["uniswap_v2", "sushiswap_v2"])].groupby("block").size()
SER = {"all": ("n_tok_all", "n_erc20_all"), "excl_lp": ("n_tok_excl_lp", "n_erc20_excl_lp"),
       "excl_lp_vault_receipt": ("n_tok_plain", "n_erc20_excl_lp_vault_receipt"), "lp": ("n_tok_lp", "n_lp")}
ZSRC = "complete_enumeration_zellic_contracts+static_classifier"
PSRC = "complete_enumeration_paradigm_traces+static_classifier"
rows = []
for _, r in bm.iterrows():
    m = r.month
    first, last = int(r.first_block), int(r.last_block)
    # exact head of the month (blocks covered by a complete dataset); for a straddling month the two datasets split it
    ex = {s: 0.0 for s in SER}; ex_src = []
    if first < P_END and m in pm.index:
        for s, (_, pc) in SER.items(): ex[s] += float(pm.loc[m, pc])
        ex_src.append("paradigm_head")
    if last >= P_END and first < Z_END and m in zm.index:
        for s, (_, pc) in SER.items(): ex[s] += float(zm.loc[m, pc])
        ex_src.append("zellic")
    if last < Z_END:     # entirely exact
        for s in SER:
            v = ex[s]
            rows.append(dict(month=m, series=s, n_est=v, ci_low=v, ci_high=v, n_blocks_sampled=0, n_tokens_in_sample=None, ci_low_boot=v, ci_high_boot=v,
                             method=(PSRC if last < P_END else ZSRC) if len(ex_src) == 1 else "complete_enumeration_paradigm+zellic"))
        continue
    lo = max(first, Z_END); hi = last; N = hi - lo + 1
    s_ = sb[(sb.month == m) & (sb.block >= lo)]
    if len(s_) < 50: continue
    idx = np.arange(lo, hi + 1); Xtot = float(v2.reindex(idx).fillna(0).sum()); xs = v2.reindex(s_.block).fillna(0).to_numpy(float)
    for s, (col, _) in SER.items():
        y = s_[col].to_numpy(float)
        if s in ("all", "lp"):
            e, l, h = E.srs(y - xs, N); e += Xtot; l += Xtot; h += Xtot
            eb, lb, hb = E.boot(y - xs, N, rng=np.random.default_rng(5)); lb += Xtot; hb += Xtot
            meth = "block_sample_decomposition(exact_v2_sushi_events+sampled_remainder)"
        else:
            e, l, h = E.srs(y, N); eb, lb, hb = E.boot(y, N, rng=np.random.default_rng(5))
            meth = "block_sample_srs_expansion"
        if ex_src: meth += "+exact_" + "_".join(ex_src)
        rows.append(dict(month=m, series=s, n_est=e + ex[s], ci_low=max(l, 0) + ex[s], ci_high=h + ex[s], n_blocks_sampled=len(y), n_tokens_in_sample=int(y.sum()),
                         ci_low_boot=max(lb, 0) + ex[s], ci_high_boot=hb + ex[s], method=meth))
d = pd.DataFrame(rows)
d.round(2).to_csv(ROOT / "data/census/monthly_counts.csv", index=False)
print(d[d.series.isin(["all", "excl_lp"])].round(0).tail(30).to_string())
