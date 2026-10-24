"""Diagnose why ~1.2k traded tokens are not statically ERC-20: sample residual tokens, fetch code at latest, report flag combos."""
import json, pathlib, random, sys, duckdb, collections
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from rpc import RPC, ROOT
from classify import flags
C = ROOT / "data/census/cache"
con = duckdb.connect()
con.execute(f"""CREATE TABLE pt AS SELECT DISTINCT lower('0x'||hex(contract_address)) a FROM read_parquet('{C}/paradigm_tokens.parquet')""")
con.execute(f"""CREATE TABLE cand AS SELECT DISTINCT lower('0x'||hex(contract_address)) a FROM read_parquet('{C}/paradigm_slim/cand_*.parquet')""")
con.execute(f"CREATE TABLE pools AS SELECT * FROM read_parquet('{ROOT}/data/census/dex_pools.parquet') WHERE block < 16800000 AND dex IN ('uniswap_v2','sushiswap_v2','uniswap_v3')")
rows = con.execute("""SELECT DISTINCT tok FROM (SELECT coalesce(base_token, token0) tok FROM pools) x WHERE tok IS NOT NULL AND tok <> '0x0000000000000000000000000000000000000000'
  AND tok NOT IN (SELECT a FROM pt) AND tok NOT IN (SELECT a FROM cand)""").fetchall()
res = [r[0] for r in rows]
print("residual (not ERC-20-flagged, not proxy/clone candidate):", len(res))
random.seed(1); samp = random.sample(res, min(80, len(res)))
rpc = RPC(stream="diag_residual", rps=4)
cnt = collections.Counter(); ex = {}
for a in samp:
    code = bytes.fromhex(rpc.call("eth_getCode", [a, "latest"], store="hash")[2:])
    if not code:
        k = "no_code_now(selfdestructed/EOA)"
    else:
        f = flags(code)
        k = "|".join(n for n in ["s_totalSupply", "s_balanceOf", "s_transfer", "t_transfer"] if not f[n]) or "all_present"
        k = "missing:" + k + (" proxy_slot" if f["proxy_slot"] else "") + (" len<400" if len(code) < 400 else "")
    cnt[k] += 1; ex.setdefault(k, a)
print(json.dumps(dict(cnt), indent=1)); print(ex)
