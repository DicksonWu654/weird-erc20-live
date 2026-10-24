"""Exact monthly ERC-20 counts for blocks 16.8M .. 21,849,999 from the reduced Zellic tables (same static classifier as Paradigm).
Reads cache/zellic_{flagged_hashes,cand,blocks}.parquet.  Writes cache/zellic_tokens.parquet and data/census/zellic_monthly.csv"""
import pathlib, sys
import duckdb, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from classify import kind as kind_of
ROOT = pathlib.Path(__file__).resolve().parents[2]
C = ROOT / "data/census/cache"
LO, HI = 16_800_000, 21_849_999
LP = {"lp_v2", "lp_v1", "lp_balancer"}
fl = pd.read_parquet(C / "zellic_flagged_hashes.parquet")
fl["kind"] = [kind_of(r) if r["c20_core"] else None for r in fl.to_dict("records")]
cand = pd.read_parquet(C / "zellic_cand.parquet")
cand = cand[cand.blocknum >= 0]
byhash = fl.set_index("bytecode_hash")
cand = cand.join(byhash[["c20_core", "c20_loose", "clone_impl", "kind", "proxy_slot", "code_len"]], on="bytecode_hash")
print("cand rows", len(cand), "dup addresses", cand.address.duplicated().sum())
addr2kind = cand.drop_duplicates("address").set_index("address")[["c20_core", "kind"]]
# direct tokens
d = cand[(cand.c20_core == True) & cand.blocknum.between(LO, HI)].copy(); d["is_clone"] = False
# clones resolved through implementation
cl = cand[cand.clone_impl.notna() & (cand.c20_core != True) & cand.blocknum.between(LO, HI)].copy()
imp = addr2kind.reindex(cl.clone_impl.to_numpy())
cl["kind"] = imp.kind.to_numpy(); ok = imp.c20_core.fillna(False).to_numpy().astype(bool)
cl = cl[ok].copy(); cl["is_clone"] = True
tok = pd.concat([d, cl], ignore_index=True)[["address", "bytecode_hash", "blocknum", "kind", "is_clone"]].rename(columns={"blocknum": "block"})
bm = pd.read_csv(ROOT / "data/census/blockmap.csv")
tok["month"] = pd.cut(tok.block, bins=[bm.first_block.iloc[0] - 1] + list(bm.last_block), labels=bm.month, right=True).astype(str)
tok.to_parquet(C / "zellic_tokens.parquet")
loose = cand[(cand.c20_loose == True) & (cand.c20_core != True) & cand.clone_impl.isna() & cand.blocknum.between(LO, HI)].copy()
loose["month"] = pd.cut(loose.blocknum, bins=[bm.first_block.iloc[0] - 1] + list(bm.last_block), labels=bm.month, right=True).astype(str)
zb = pd.read_parquet(C / "zellic_blocks.parquet"); zb = zb[zb.block.between(LO, HI)]
zb["month"] = pd.cut(zb.block, bins=[bm.first_block.iloc[0] - 1] + list(bm.last_block), labels=bm.month, right=True).astype(str)
rows = []
for m, n_all in zb.groupby("month").n_rows.sum().items():
    t = tok[tok.month == m]
    rows.append(dict(month=m, n_create_all=int(n_all), n_erc20_all=len(t), n_erc20_excl_lp=int((~t.kind.isin(LP)).sum()),
                     n_erc20_excl_lp_vault_receipt=int(t.kind.isin(["plain", "wrapped_native"]).sum()), n_lp=int(t.kind.isin(LP).sum()),
                     n_vault_receipt=int(t.kind.isin(["vault", "receipt"]).sum()), n_clones=int(t.is_clone.sum()),
                     n_loose_selector_only_not_dispatched=int((loose.month == m).sum())))
out = pd.DataFrame(rows).sort_values("month")
out.to_csv(ROOT / "data/census/zellic_monthly.csv", index=False)
print(out.to_string())
