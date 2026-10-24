#!/usr/bin/env python3
"""Assemble the publishable round-2 subset and simulator pairs.

Huynh/trapdoor rows are deliberately not read.  Inputs are the committed W6
new/top frames plus the locally generated 14-day frame.
"""
import csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "validate" / "round2"

def load(path):
    return [json.loads(x) for x in path.open() if x.strip()]

def main():
    parts = [
        ("w6_new", ROOT / "data/validate/subset_new_eth.jsonl"),
        ("w6_top", ROOT / "data/validate/subset_top.jsonl"),
        ("newer14", OUT / "subset_new_eth.jsonl"),
    ]
    seen, rows = set(), []
    for part, path in parts:
        for r in load(path):
            k = (int(r["chain"]), r["token"].lower())
            if k in seen:
                continue
            seen.add(k)
            r = dict(r)
            r["part"] = part
            r["token"] = r["token"].lower()
            rows.append(r)
    with (OUT / "subset.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")

    # Existing W6 pairs cover the W6 new/top frames.  Add only genuinely new
    # 14-day pairs, preserving the exact pool chosen by the frame builder.
    pairs, pseen = [], set()
    valid_keys = {(r["chain"], r["token"].lower()) for r in rows}
    old_pairs = ROOT / "data/validate/sim_pairs_latest.csv"
    with old_pairs.open() as f:
        for r in csv.DictReader(f):
            # W6's pair file also contains Huynh trapdoor rows.  They are
            # intentionally excluded from this publishable round-2 frame.
            if (1, r["token"].lower()) not in valid_keys:
                continue
            k = (r["token"].lower(), r["pool"].lower())
            if k not in pseen:
                pseen.add(k); pairs.append(dict(r))
    for r in load(OUT / "subset_new_eth.jsonl"):
        k = (r["token"].lower(), r["pool"].lower())
        if k in pseen:
            continue
        pseen.add(k)
        pairs.append({"token": r["token"], "pool": r["pool"],
                      "dex": "uniswap_v2" if r["pool_kind"] == "v2" else "uniswap_v3",
                      "created_block": r.get("token_created_block") or r.get("pool_created_block"),
                      "stratum": r.get("stratum"), "source": "newer14"})
    with (OUT / "sim_pairs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for x in pairs for k in x}))
        w.writeheader(); w.writerows(pairs)
    meta = {"n_subset": len(rows), "parts": {p: sum(r["part"] == p for r in rows) for p, _ in parts},
            "n_pairs": len(pairs), "n_w6_pairs": sum(1 for r in pairs if r.get("source") != "newer14"),
            "n_newer14_pairs": sum(1 for r in pairs if r.get("source") == "newer14")}
    (OUT / "prepare_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))

if __name__ == "__main__":
    main()
