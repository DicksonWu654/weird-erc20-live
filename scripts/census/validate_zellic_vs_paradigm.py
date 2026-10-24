"""Two independent complete datasets on the overlap range (blocks 14,000,000-16,799,999): compare contract-creation counts and the
sets of ERC-20 tokens produced by the identical static classifier.  Writes data/census/evidence/validate_zellic_vs_paradigm.json"""
import json, pathlib, sys
import duckdb, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from classify import kind as kind_of
ROOT = pathlib.Path(__file__).resolve().parents[2]; C = ROOT / "data/census/cache"
LO, HI = 14_000_000, 16_799_999
fl = pd.read_parquet(C / "zellic_flagged_hashes.parquet")
fl["kind"] = [kind_of(r) if r["c20_core"] else None for r in fl.to_dict("records")]
cand = pd.read_parquet(C / "zellic_cand.parquet"); cand = cand[cand.blocknum >= 0]
cand = cand.join(fl.set_index("bytecode_hash")[["c20_core", "clone_impl", "kind"]], on="bytecode_hash")
a2 = cand.drop_duplicates("address").set_index("address")[["c20_core", "kind"]]
d = cand[(cand.c20_core == True) & cand.blocknum.between(LO, HI)]
cl = cand[cand.clone_impl.notna() & (cand.c20_core != True) & cand.blocknum.between(LO, HI)]
ok = a2.reindex(cl.clone_impl.to_numpy()).c20_core.fillna(False).to_numpy().astype(bool)
Z = set(d.address) | set(cl[ok].address)
con = duckdb.connect()
P = con.execute(f"SELECT lower('0x'||hex(contract_address)) a FROM read_parquet('{C}/paradigm_tokens.parquet') WHERE block_number BETWEEN {LO} AND {HI} AND (is_clone OR kind IS NOT NULL) AND kind <> 'lp_v1'").df().a
P = set(P)
zb = pd.read_parquet(C / "zellic_blocks.parquet"); zb = zb[zb.block.between(LO, HI)]
pb = con.execute(f"SELECT sum(n_create) n, sum(n_with_code) nc FROM read_parquet('{C}/paradigm_slim/blocks_*.parquet') WHERE block_number BETWEEN {LO} AND {HI}").fetchone()
res = dict(range=[LO, HI], zellic_contract_rows=int(zb.n_rows.sum()), paradigm_create_traces=int(pb[0]), paradigm_create_with_code=int(pb[1]),
           zellic_tokens=len(Z), paradigm_tokens=len(P), both=len(Z & P), only_zellic=len(Z - P), only_paradigm=len(P - Z))
print(json.dumps(res, indent=1)); (ROOT / "data/census/evidence/validate_zellic_vs_paradigm.json").write_text(json.dumps(res, indent=1))
