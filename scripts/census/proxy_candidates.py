"""Monthly counts of contracts carrying a proxy marker (ERC-1967 / OZ-unstructured / beacon / UUPS slot constants) that are NOT
counted as ERC-20 (not statically ERC-20, not an EIP-1167 clone).  validate_dynamic.py measured that ~10-13% of them respond like an
ERC-20 (token behind a proxy) -> candidate recall gap.  Writes data/census/proxy_candidates_monthly.csv
(n_proxy_marker_noncore, est_hidden_erc20_lo/mid/hi using Wilson 95% of the dynamic share from validate_dynamic.json)."""
import json, pathlib, duckdb, pandas as pd
ROOT = pathlib.Path(__file__).resolve().parents[2]; C = ROOT / "data/census/cache"
bm = pd.read_csv(ROOT / "data/census/blockmap.csv")
con = duckdb.connect(); con.register("bm", bm)
p = con.execute(f"""SELECT b.month, count(*) n FROM read_parquet('{C}/paradigm_slim/cand_*.parquet') c JOIN bm b ON c.block_number BETWEEN b.first_block AND b.last_block
   WHERE c.proxy_slot AND NOT c.c20_core AND c.clone_impl IS NULL GROUP BY 1""").df()
z = pd.read_parquet(C / "zellic_cand.parquet"); f = pd.read_parquet(C / "zellic_flagged_hashes.parquet").set_index("bytecode_hash")
z = z.join(f[["proxy_slot", "c20_core", "clone_impl"]], on="bytecode_hash")
z = z[(z.proxy_slot == True) & (z.c20_core != True) & z.clone_impl.isna() & z.blocknum.between(16_800_000, 21_849_999)].copy()
z["month"] = pd.cut(z.blocknum, bins=[bm.first_block.iloc[0] - 1] + list(bm.last_block), labels=bm.month, right=True).astype(str)
d = pd.concat([p, z.groupby("month").size().rename("n").reset_index()]).groupby("month").n.sum().reset_index().rename(columns={"n": "n_proxy_marker_noncore"})
dyn = json.load(open(ROOT / "data/census/evidence/validate_dynamic.json"))["B_proxy_slot"]
k = sum(v["dyn"] for v in dyn.values()); n = sum(v["n"] for v in dyn.values())
import math
pz = k / n; zz = 1.96; dd = 1 + zz * zz / n; c = (pz + zz * zz / (2 * n)) / dd; h = zz * math.sqrt(pz * (1 - pz) / n + zz * zz / (4 * n * n)) / dd
d["est_hidden_erc20_lo"] = (d.n_proxy_marker_noncore * (c - h)).round(0); d["est_hidden_erc20_mid"] = (d.n_proxy_marker_noncore * pz).round(0); d["est_hidden_erc20_hi"] = (d.n_proxy_marker_noncore * (c + h)).round(0)
d.to_csv(ROOT / "data/census/proxy_candidates_monthly.csv", index=False)
print(f"dynamic ERC-20 share among proxy-marked: {k}/{n} = {pz:.3f} (Wilson {c-h:.3f}-{c+h:.3f})"); print(d.tail(8).to_string(index=False))
