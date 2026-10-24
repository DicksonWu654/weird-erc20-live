"""Independent completeness check of V3/V4 (no on-chain counter): re-query random 9,999-block windows on a different endpoint
(rpc.mevblocker.io) and compare event counts with the Tenderly-based scan in data/census/dex_pools.parquet."""
import json, pathlib, random, sys
import pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from rpc import RPC, ROOT
from dex_scan import DEX

d = pd.read_parquet(ROOT / "data/census/dex_pools.parquet")
rpc = RPC(url="https://rpc.mevblocker.io", rps=1.5, stream="dex_crosscheck")
random.seed(7)
res = []
for dex in ["uniswap_v3", "uniswap_v4", "uniswap_v2", "sushiswap_v2"]:
    lo, hi = DEX[dex]["start"], int(d.block.max())
    for _ in range(10 if dex in ("uniswap_v3", "uniswap_v4") else 4):
        a = random.randint(lo, hi - 9999); b = a + 9998
        r = rpc.call("eth_getLogs", [{"fromBlock": hex(a), "toBlock": hex(b), "address": DEX[dex]["addr"], "topics": [DEX[dex]["topic"]]}], store="hash")
        mine = int(((d.dex == dex) & (d.block >= a) & (d.block <= b)).sum())
        res.append(dict(dex=dex, from_block=a, to_block=b, other_endpoint=len(r), scan=mine, match=len(r) == mine))
        print(res[-1], flush=True)
(ROOT / "data/census/evidence/dex_crosscheck.json").write_text(json.dumps(res, indent=1))
print("all match:", all(x["match"] for x in res), "total other/scan:", sum(x["other_endpoint"] for x in res), sum(x["scan"] for x in res))
