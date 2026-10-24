"""Dynamic cross-check of the static ERC-20 definition: eth_call totalSupply()/balanceOf(0xdEaD)/decimals() at the creation block.
'responds like ERC-20' = totalSupply(), balanceOf(address) and allowance(address,address) each return exactly one 32-byte word
(allowance separates ERC-20 from ERC-721/staking contracts that also expose totalSupply/balanceOf).
Classes (each stratified by era, random draws, seeded):
  A_static_resolved : tokens the census counts (strict dispatch rule; EIP-1167 clones resolved to an ERC-20 implementation) -> precision
  B_proxy_slot      : proxy-marked contracts NOT counted (non-1167 proxies, unresolved) -> share that are ERC-20s behind a proxy (recall gap)
  C_unflagged       : any other created contract with code (from the live block samples) -> false-negative rate
  D_loose_only      : selector bytes + Transfer topic present but NOT dispatched (rejected by the strict rule) -> was strictness right?
Writes data/census/evidence/validate_dynamic.json (+ _rows.jsonl).   .venv/bin/python scripts/census/validate_dynamic.py [--n 120]"""
import argparse, glob, json, math, pathlib, random, sys
import duckdb, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from rpc import RPC, ROOT
from classify import flags
C = ROOT / "data/census/cache"
ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=120); ap.add_argument("--rps", type=float, default=1.0); a = ap.parse_args()
random.seed(99)
con = duckdb.connect()
def hexa(col="contract_address"): return f"lower('0x'||hex({col}))"
P = f"{C}/paradigm_slim/cand_*.parquet"
rows = {k: [] for k in ("A_static_resolved", "B_proxy_slot", "C_unflagged", "D_loose_only")}
def add(cls, df, src):
    rows[cls] += [dict(address=r.address, block=int(r.block), src=src) for r in df.itertuples()]
add("A_static_resolved", con.execute(f"SELECT {hexa()} address, block_number block FROM (SELECT * FROM read_parquet('{C}/paradigm_tokens.parquet') WHERE kind IS NOT NULL AND is_clone IN (TRUE,FALSE) AND block_number>=3000000 AND code_len IS NOT NULL) USING SAMPLE {a.n} ROWS").df(), "paradigm")
add("B_proxy_slot", con.execute(f"SELECT address, block FROM (SELECT {hexa()} address, block_number block FROM read_parquet('{P}') WHERE proxy_slot AND NOT c20_core AND clone_impl IS NULL) USING SAMPLE {a.n//2} ROWS").df(), "paradigm")
add("D_loose_only", con.execute(f"SELECT address, block FROM (SELECT {hexa()} address, block_number block FROM read_parquet('{P}') WHERE c20_loose AND NOT c20_core AND clone_impl IS NULL AND block_number>=3000000) USING SAMPLE {a.n//2} ROWS").df(), "paradigm")
zt = C / "zellic_tokens.parquet"
if zt.exists():
    z = pd.read_parquet(zt); z = z[z.kind.notna()].sample(a.n, random_state=1)
    add("A_static_resolved", z.rename(columns={}).assign(block=z.block)[["address", "block"]], "zellic")
zc = C / "zellic_cand.parquet"; zf = C / "zellic_flagged_hashes.parquet"
if zc.exists() and zf.exists():
    c = pd.read_parquet(zc); f = pd.read_parquet(zf).set_index("bytecode_hash")
    c = c[(c.blocknum >= 16_800_000) & (c.blocknum <= 21_849_999)].join(f[["c20_core", "c20_loose", "clone_impl", "proxy_slot"]], on="bytecode_hash")
    b = c[(c.proxy_slot == True) & (c.c20_core != True) & c.clone_impl.isna()]
    add("B_proxy_slot", b.sample(a.n // 2, random_state=2).rename(columns={"blocknum": "block"})[["address", "block"]], "zellic")
    d = c[(c.c20_loose == True) & (c.c20_core != True) & c.clone_impl.isna()]
    add("D_loose_only", d.sample(min(a.n // 2, len(d)), random_state=3).rename(columns={"blocknum": "block"})[["address", "block"]], "zellic")
# live block samples: tokens (resolved) and unflagged creations
st = C / "sampled_tokens.parquet"
if st.exists():
    s = pd.read_parquet(st); s = s[s.block >= 21_850_000]
    if len(s): add("A_static_resolved", s.sample(min(a.n // 2, len(s)), random_state=4)[["address", "block"]], "blocksample")
pc = []
for f_ in glob.glob(str(C / "blocksample/*.jsonl")):
    if f_.endswith(".failed.jsonl"): continue
    for line in open(f_):
        j = json.loads(line)
        for c_ in j["creates"]:
            if not (c_["c20_loose"] or c_["clone_impl"] or c_["proxy_slot"]):
                pc.append(dict(address=c_["address"], block=c_["block"], src="blocksample"))
rows["C_unflagged"] += random.sample(pc, min(3 * a.n, len(pc)))
rpc = RPC(url="https://gateway.tenderly.co/public/mainnet", stream="validate_dynamic", rps=a.rps)
DEAD = "0" * 60 + "dead"
def era(b): return "<16.8M(Paradigm era)" if b < 16_800_000 else ("16.8M-21.85M(Zellic era)" if b < 21_850_000 else ">=21.85M(live sample era)")
def wilson(k, n):
    if n == 0: return (None, None)
    p, z = k / n, 1.96; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 4), round(c + h, 4))
res_all, summ = [], {}
for cls, lst in rows.items():
    stats = {}
    for r in lst:
        res = rpc.batch([("eth_call", [{"to": r["address"], "data": d}, hex(r["block"])]) for d in ("0x18160ddd", "0x70a08231" + DEAD, "0xdd62ed3e" + DEAD + DEAD, "0x313ce567")])
        out = [(x if x is not None else "ERR:" + str(e)[:60]) for x, e in res]
        code = rpc.call("eth_getCode", [r["address"], hex(r["block"])], store="hash")
        fl = flags(bytes.fromhex(code[2:])) if code not in ("0x", None) else None
        ok = all(isinstance(v, str) and v.startswith("0x") and len(v) == 66 for v in out[:3])   # totalSupply, balanceOf, allowance (ERC-721 lacks allowance(address,address))
        e = era(r["block"]); s = stats.setdefault(e, dict(n=0, dyn=0, strict=0, strict_and_dyn=0, code_missing=0))
        s["n"] += 1; s["dyn"] += ok
        if fl is None: s["code_missing"] += 1
        else:
            s["strict"] += bool(fl["c20_core"]); s["strict_and_dyn"] += bool(fl["c20_core"] and ok)
        res_all.append(dict(cls=cls, **r, supply=out[0], balance=out[1], allowance=out[2], decimals=out[3], erc20_like=ok, strict=None if fl is None else bool(fl["c20_core"]), loose=None if fl is None else bool(fl["c20_loose"])))
    summ[cls] = {e: dict(**s, share_dynamic_erc20=round(s["dyn"] / s["n"], 4), wilson95=wilson(s["dyn"], s["n"])) for e, s in stats.items()}
    print(cls, json.dumps(summ[cls]), flush=True)
(ROOT / "data/census/evidence/validate_dynamic.json").write_text(json.dumps(summ, indent=1))
with open(ROOT / "data/census/evidence/validate_dynamic_rows.jsonl", "w") as f:
    for r in res_all: f.write(json.dumps(r) + "\n")
