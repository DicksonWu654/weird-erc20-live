#!/usr/bin/env python3
"""Compare two results.jsonl runs of the same pairs/blocks (e.g. simulateV1 vs anvil): taxes, sell outcomes, gas.
  backend_compare.py data/sim/<A>/results.jsonl data/sim/<B>/results.jsonl"""
import json, sys
def load(p):
    d = {}
    for l in open(p):
        r = json.loads(l); d[(r["token"], r["pool"], r["block"])] = r
    return d
a, b = load(sys.argv[1]), load(sys.argv[2])
def f(x): return "-" if x is None else (f"{x:.4f}" if isinstance(x, float) else str(x))
print(f"{'sym':10} {'blk':>9} | {'label A':12} {'label B':12} | buyA buyB | xferA xferB | sellA sellB | sells ok A/B | gas(buy) A/B")
agree = 0
for k, ra in a.items():
    rb = b.get(k)
    if not rb: continue
    ta, tb = ra["taxes"], rb["taxes"]
    sa = "".join("1" if s["ok"] else "0" for s in ra["sells"]); sb = "".join("1" if s["ok"] else "0" for s in rb["sells"])
    ga = (ra["buy"] or {}).get("gas"); gb = (rb["buy"] or {}).get("gas")
    agree += ra["primary_label"] == rb["primary_label"]
    print(f"{(ra['meta'].get('symbol') or '?')[:10]:10} {k[2]:>9} | {ra['primary_label']:12} {rb['primary_label']:12} | {f(ta.get('buy'))} {f(tb.get('buy'))} | {f(ta.get('transfer'))} {f(tb.get('transfer'))} | {f(ta.get('sell'))} {f(tb.get('sell'))} | {sa}/{sb} | {ga}/{gb}")
print("primary label agreement:", agree, "/", len([k for k in a if k in b]))
