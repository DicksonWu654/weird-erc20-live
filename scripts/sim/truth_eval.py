#!/usr/bin/env python3
"""Agreement of a results.jsonl with a ground-truth column carried in `extra` (e.g. Huynh et al. trapdoor sample:
truth = trapdoor | non_malicious).  Prints a confusion table (primary label x truth), scam-axis agreement among probeable pairs,
and the unprobeable reasons.   truth_eval.py data/sim/<run>/results.jsonl [--truth-col truth]"""
import argparse, collections, json
ap = argparse.ArgumentParser()
ap.add_argument("results")
ap.add_argument("--truth-col", default="truth")
ap.add_argument("--pos", default="trapdoor")
a = ap.parse_args()
last = {}
for l in open(a.results):
    r = json.loads(l)
    last[(r["token"], r["pool"], r["block"])] = r
conf = collections.defaultdict(collections.Counter)
unp = collections.Counter()
SCAM = {"honeypot", "rugged", "high_tax"}
for r in last.values():
    t = (r.get("extra") or {}).get(a.truth_col)
    conf[t][r["primary_label"]] += 1
    if r["primary_label"] == "unprobeable":
        unp[(t, (r["unprobeable"] or "").split(":")[0] if r["unprobeable"] else "sell_pool_math")] += 1
for t, c in conf.items():
    print(t, dict(c))
pos = conf[a.pos]
neg = {k: v for t, c in conf.items() if t != a.pos for k, v in c.items()}
tp_h = pos["honeypot"]
tp = sum(v for k, v in pos.items() if k in SCAM)
pp = sum(v for k, v in pos.items() if k != "unprobeable")
print(f"positives: honeypot-label {tp_h}, any scam label {tp}, probeable {pp}, total {sum(pos.values())}")
print(f"  recall(honeypot label | probeable) = {tp_h}/{pp} = {tp_h / max(pp, 1):.3f};  recall(scam axis | probeable) = {tp}/{pp} = {tp / max(pp, 1):.3f}")
negc = collections.Counter()
for t, c in conf.items():
    if t != a.pos:
        negc.update(c)
npr = sum(v for k, v in negc.items() if k != "unprobeable")
fp_h = negc["honeypot"]
fp = sum(v for k, v in negc.items() if k in SCAM)
print(f"negatives: honeypot-label {fp_h}, scam-axis {fp} (rugged {negc['rugged']}, high_tax {negc['high_tax']}), probeable {npr}")
print(f"  false-honeypot rate = {fp_h}/{npr};  scam-axis FP rate = {fp}/{npr}")
print("unprobeable reasons:", dict(unp))
