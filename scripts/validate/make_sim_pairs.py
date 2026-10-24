"""Write the pairs file for scripts/sim/run.py from the validation subset (Ethereum tokens that have a pool):
data/validate/sim_pairs_latest.csv  (token,pool,dex,stratum,source,subset_chain).
* new_eth + trapdoor rows carry the pool we selected (best pool at build time / dataset pool).
* top-token rows (chain 1) borrow the pool honeypot.is chose (pair address from its cached answer), if it has one.
Usage: .venv/bin/python scripts/sim/run.py --pairs data/validate/sim_pairs_latest.csv --mode latest --out data/sim/<run>/"""
import csv, json
import normalize as N
from common import VAL

rows = [json.loads(l) for l in open(VAL / "subset.jsonl")]
out = []
for r in rows:
    if r["chain"] != 1:
        continue
    pool, kind, src = r.get("pool"), r.get("pool_kind"), r["source"]
    if not pool:
        h = N.get_honeypot(1, r["token"])
        if not h or h.get("coverage") not in ("ok", "sim_failed") or not h.get("pair"):
            continue
        pool, kind, src = h["pair"], "v3" if h.get("pair_type") == "UniswapV3" else "v2", "top:honeypot.is_pair"
    out.append({"token": r["token"], "pool": pool, "dex": "uniswap_v3" if kind == "v3" else "uniswap_v2",
                "stratum": r["stratum"], "source": src})
with open(VAL / "sim_pairs_latest.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]))
    w.writeheader()
    w.writerows(out)
print(len(out), "pairs")
