"""Assemble pool_frame.py outputs: per-month estimate of the number of NEW (<=30 d old at first pool) tokens that got a DEX pool,
with Wilson 95% CI, and the token rows (weight = M_m / n_cand).  When a month is also covered by the complete Zellic table, validate
creation blocks and the old/new split against it.   Writes data/census/pool_linked_new_tokens_monthly.csv,
cache/poolframe_tokens.parquet, data/census/evidence/validate_pool_frame.json"""
import glob, json, math, pathlib, sys
import pandas as pd
ROOT = pathlib.Path(__file__).resolve().parents[2]; C = ROOT / "data/census/cache"
W = 216_000
def wilson(k, n):
    if n == 0: return (float("nan"),) * 2
    p, z = k / n, 1.96; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h
rows, toks = [], []
for mp in sorted(glob.glob(str(C / "poolframe/*.meta.json"))):
    meta = json.load(open(mp)); m = meta["month"]; p = C / f"poolframe/{m}.jsonl"
    if not p.exists(): continue
    r = pd.DataFrame([json.loads(l) for l in open(p)])
    if r.empty: continue
    n = len(r); k = int((r.status == "new").sum()); M = meta["M_population"]; lo, hi = wilson(k, n)
    traced = r[(r.status == "new") & (r.get("creation_trace") == "ok")]
    rows.append(dict(month=m, M_first_pooled_tokens=M, n_candidates_checked=n, n_new=k, n_old=int((r.status == "old").sum()),
                     n_new_traced=len(traced), N_new_pooled_est=M * k / n, ci_low=M * lo, ci_high=M * hi,
                     direct_deploy_share_of_new=float(traced.direct_deploy.mean()) if len(traced) else None,
                     static_erc20_share_of_new=float(traced.static_c20.mean()) if len(traced) else None))
    t = traced.copy(); t["weight"] = M / n; t["M_population"] = M; t["n_candidates_checked"] = n; toks.append(t)
out = pd.DataFrame(rows); out.to_csv(ROOT / "data/census/pool_linked_new_tokens_monthly.csv", index=False)
print(out.round(3).to_string())
if toks:
    T = pd.concat(toks, ignore_index=True); T.to_parquet(C / "poolframe_tokens.parquet")
    zc, zf = C / "zellic_cand.parquet", C / "zellic_flagged_hashes.parquet"
    if zc.exists():
        z = pd.read_parquet(zc).drop_duplicates("address").set_index("address").blocknum
        allr = pd.concat([pd.DataFrame([json.loads(l) for l in open(f)]).assign(m=pathlib.Path(f).stem) for f in glob.glob(str(C / "poolframe/*.jsonl")) if not f.endswith("failed.jsonl")])
        allr = allr[allr.first_pool_block <= 21_849_999]
        allr["zblock"] = allr.token.map(z)
        res = {}
        new = allr[allr.status == "new"]
        have = new[new.zblock.notna() & new.creation_block.notna()]
        res["new_candidates"] = len(new); res["new_found_in_zellic"] = len(have)
        res["creation_block_exact_match"] = int((have.creation_block == have.zblock).sum())
        old = allr[allr.status == "old"]; oh = old[old.zblock.notna()]
        res["old_found_in_zellic"] = len(oh); res["old_confirmed_(zellic_block< P-W-1)"] = int((oh.zblock < oh.first_pool_block - W).sum())
        nz = new[new.zblock.notna()]; res["new_confirmed_(zellic_block>=P-W)"] = int((nz.zblock >= nz.first_pool_block - W).sum()); res["new_with_zellic_row"] = len(nz)
        print(json.dumps(res, indent=1)); (ROOT / "data/census/evidence/validate_pool_frame.json").write_text(json.dumps(res, indent=1))
