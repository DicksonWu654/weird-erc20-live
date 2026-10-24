#!/usr/bin/env python3
"""Run the golden set (data/sim-golden/expected.csv) through run.py and compare observed vs expected.

  golden_eval.py [--run-name golden-v1] [--skip-run]
Writes data/sim-golden/observed.csv and data/sim-golden/summary.json; raw sim output in data/sim/<run-name>-{historical,latest}/.
Outcome per row:  TP/FN (expected scam), TN/FP (expected not-scam), UNDET (unprobeable: neither right nor wrong).
Scam axis = primary label in {honeypot, rugged, high_tax} (rugged groups also accept the `rugged` flag).
"""
import argparse, csv, json, os, subprocess, sys
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
G = os.path.join(ROOT, "data", "sim-golden")
PY = os.path.join(ROOT, ".venv", "bin", "python")
SCAM = {"honeypot", "rugged", "high_tax"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", default="golden-v1")
    ap.add_argument("--skip-run", action="store_true")
    ap.add_argument("--offset-blocks", type=int, default=7200)
    a = ap.parse_args()
    ex = pd.read_csv(os.path.join(G, "expected.csv"), dtype=str).fillna("")
    outs = {}
    for mode in ("historical", "latest"):
        sub = ex[ex["mode"] == mode].drop_duplicates(["token", "pool", "block", "created_block"])
        pf = os.path.join(ROOT, "data", "sim", f"{a.run_name}-{mode}-pairs.csv")
        os.makedirs(os.path.dirname(pf), exist_ok=True)
        sub[["token", "pool", "dex", "created_block", "block"]].to_csv(pf, index=False)
        outs[mode] = os.path.join(ROOT, "data", "sim", f"{a.run_name}-{mode}")
        if not a.skip_run:
            subprocess.run([PY, os.path.join(ROOT, "scripts", "sim", "run.py"), "--pairs", pf, "--mode", mode, "--out", outs[mode],
                            "--offset-blocks", str(a.offset_blocks), "--redo-errors"], check=True)
    res = {}
    for mode, d in outs.items():
        for l in open(os.path.join(d, "results.jsonl")):
            r = json.loads(l)
            res.setdefault((mode, r["token"].lower(), r["pool"].lower()), []).insert(0, r)  # newest record first
    rows = []
    for _, e in ex.iterrows():
        cands = res.get((e["mode"], e["token"].lower(), e["pool"].lower()), [])
        if e["block"]:
            cands = [c for c in cands if c["block"] == int(e["block"])] or cands
        r = cands[0] if cands else None
        row = dict(id=e["id"], group=e["group"], mode=e["mode"], symbol=e["symbol"], token=e["token"], expected_label=e["expected_label"],
                   expect_scam=e["expect_scam"], documented_tax_pct=e["documented_tax_pct"], confidence=e["confidence"])
        if not r:
            row.update(outcome="NO_RESULT")
            rows.append(row)
            continue
        t = r["taxes"] or {}
        flags = r["flags"]
        prim = r["primary_label"]
        row.update(block=r["block"], primary=prim, reason=r["label_reason"], unprobeable=r["unprobeable"] or "", buy_tax=t.get("buy"), sell_tax=t.get("sell"),
                   transfer_tax=t.get("transfer"), flags="|".join(flags), evidence=(r["evidence"] or {}).get("raw", ""))
        scam_obs = prim in SCAM or (e["group"] == "rugged" and "rugged" in flags)
        if prim == "unprobeable":
            out = "UNDET"
        elif e["expect_scam"] == "1":
            out = "TP" if (scam_obs and (e["group"] != "honeypot" or prim == "honeypot")) else "FN"
            if e["group"] == "honeypot" and prim in SCAM and prim != "honeypot":
                out = "TP_other_scam_label"
        else:
            out = "FP" if prim in SCAM else "TN"
        if e["group"] == "legit_fee" and e["mode"] == "latest" and e["documented_tax_pct"] and prim != "unprobeable":
            d = float(e["documented_tax_pct"]) / 100
            ok = all(abs((t.get(k) or 0) - d) <= 0.015 for k in ("buy", "sell") if t.get(k) is not None)
            row["tax_matches_doc"] = ok
        rows.append(row)
        row["outcome"] = out
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(G, "observed.csv"), index=False)
    def rate_all(sel):
        s = df[sel]
        return (int((~s.outcome.isin(["UNDET", "NO_RESULT"])).sum()), len(s))
    summ = {}
    for (g, m), x in df.groupby(["group", "mode"]):
        summ[f"{g}/{m}"] = x["outcome"].value_counts().to_dict()
    # headline numbers
    def rate(sel, good):
        s = df[sel & (df.outcome != "UNDET") & (df.outcome != "NO_RESULT")]
        return (s.outcome.isin(good).sum(), len(s))
    summ["honeypot_label_recall_among_probeable"] = rate(df.group == "honeypot", ["TP"])
    summ["honeypot_scam_axis_recall_among_probeable"] = rate(df.group == "honeypot", ["TP", "TP_other_scam_label"])
    summ["honeypot_probeable_fraction"] = rate_all(df.group == "honeypot")
    summ["rugged_recall_among_probeable"] = rate(df.group == "rugged", ["TP"])
    summ["normal_specificity"] = rate(df.group.isin(["normal", "normal_hist"]), ["TN"])
    summ["legit_fee_not_flagged"] = rate(df.group == "legit_fee", ["TN"])
    json.dump(summ, open(os.path.join(G, "summary.json"), "w"), indent=1, default=str)
    print(json.dumps(summ, indent=1, default=str))


main()
