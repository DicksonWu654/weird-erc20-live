#!/usr/bin/env python3
"""Re-apply classify.py to saved results (no network): iterate on label rules without re-simulating.
  reclassify.py data/sim/<run>/results.jsonl [...]   (rewrites in place, keeps the old file as results.jsonl.bak once)
Keeps only the newest record per (token, pool, block)."""
import argparse, json, os, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify as CL

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("paths", nargs="+")
ap.add_argument("--dust-floor-wei", type=int, default=CL.RULES["DUST_FLOOR_WEI"])
args = ap.parse_args()
for path in args.paths:
    last = {}
    for l in open(path):
        r = json.loads(l)
        last[(r["token"], r["pool"], r["block"])] = r
    if not os.path.exists(path + ".bak"):
        shutil.copy(path, path + ".bak")
    n = 0
    with open(path, "w") as f:
        for r in last.values():
            res = {"unprobeable": r.get("unprobeable"), "buy": r.get("buy"), "transfer": r.get("transfer"), "approve": r.get("approve"),
                   "sells": r.get("sells") or [], "pool_info": r.get("pool_info") or {}}
            flags, primary, reason, taxes = CL.classify(res, r.get("control"), r.get("rug"), args.dust_floor_wei)
            if primary != r["primary_label"]:
                n += 1
            unprobeable = r.get("unprobeable")
            if primary == "unprobeable" and reason in ("dust_pool", "no_active_liquidity", "zero_output_buy", "no_positive_sell_quote"):
                unprobeable = reason
            r.update(flags=sorted(set(flags)), primary_label=primary, label_reason=reason, taxes=taxes,
                     unprobeable=unprobeable, rules={**CL.RULES, "DUST_FLOOR_WEI": args.dust_floor_wei})
            f.write(json.dumps(r) + "\n")
    print(path, len(last), "records,", n, "primary labels changed")
