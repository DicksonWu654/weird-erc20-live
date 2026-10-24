#!/usr/bin/env python3
"""Compare fixed-classifier historical runs at pool+300 and pool+7,200 blocks."""
import json
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/tier2"


def load(path):
    rows = {}
    for line in open(path):
        r = json.loads(line)
        rows[(r["token"].lower(), r["pool"].lower(), int(r["created_block"]))] = r
    return rows


def main():
    early = load(OUT / "sim-sensitive/results.jsonl")
    late = load(OUT / "sim-run/results.jsonl")
    rows = []
    for k, e in early.items():
        l = late.get(k)
        if not l:
            continue
        rows.append({"token": e["token"], "pool": e["pool"], "dex": e["dex"],
                     "creation_month": (e.get("extra") or {}).get("month"),
                     "block_300": e["block"], "block_7200": l["block"],
                     "label_300": e["primary_label"], "reason_300": e.get("label_reason"),
                     "label_7200": l["primary_label"], "reason_7200": l.get("label_reason"),
                     "same_label": e["primary_label"] == l["primary_label"],
                     "evidence_300": json.dumps(e.get("evidence")),
                     "evidence_7200": json.dumps(l.get("evidence"))})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "offset_sensitivity.csv", index=False)
    df["era"] = pd.cut(df.creation_month.str[:4].astype(int), [2016, 2019, 2022, 2024, 2026],
                       labels=["2017-19", "2020-22", "2023-24", "2025-26"])
    summary = df.groupby("era", observed=True).agg(n=("token", "size"), same=("same_label", "sum"))
    summary["agreement"] = summary.same / summary.n
    summary.to_csv(OUT / "offset_sensitivity_summary.csv")
    print("offset compared", len(df), "agree", df.same_label.sum(), "transitions", Counter(zip(df.label_300, df.label_7200)).most_common(8))


if __name__ == "__main__":
    main()
