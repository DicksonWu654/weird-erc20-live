#!/usr/bin/env python3
"""Build data/sim-golden/expected.csv (ground truth table) from
  data/sim-golden/candidates_scam.csv  (curated from public datasets, with sources)
  + a hand-listed set of well-known normal tokens (sources: project/Etherscan pages, universally known)
Rows: id, group, token, symbol, pool, dex, mode (historical|latest), created_block, block, expected_label, expect_scam(1/0),
      documented_tax_pct, confidence, source_urls, notes
"""
import csv, json, os, sys
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
G = os.path.join(ROOT, "data", "sim-golden")
cand = pd.read_csv(os.path.join(G, "candidates_scam.csv"))
rows = []


def add(**k):
    base = dict(id="", group="", token="", symbol="", pool="", dex="uniswap_v2", mode="", created_block="", block="", expected_label="",
                expect_scam="", documented_tax_pct="", confidence="", source_urls="", notes="")
    base.update(k)
    rows.append(base)


for _, r in cand.iterrows():
    if pd.isna(r.pool_v2_weth):
        continue
    cb = "" if pd.isna(r.pool_created_block) else int(r.pool_created_block)
    common = dict(token=r.token, symbol=str(r.symbol).strip(), pool=r.pool_v2_weth, confidence=r.confidence, source_urls=r.source_urls,
                  documented_tax_pct="" if pd.isna(r.documented_tax_pct) else r.documented_tax_pct, notes=str(r.mechanism)[:300])
    if r.category == "honeypot" and cb:
        add(id=f"hp-{r.token[:8]}", group="honeypot", mode="historical", created_block=cb, expected_label="honeypot", expect_scam=1, **common)
    elif r.category == "rugged" and cb:
        add(id=f"rug-{r.token[:8]}", group="rugged", mode="historical", created_block=cb, expected_label="rugged", expect_scam=1, **common)
    elif r.category == "died" and cb:
        add(id=f"died-{r.token[:8]}", group="died", mode="historical", created_block=cb, expected_label="normal", expect_scam=0, **common)
    elif r.category == "legit_fee":
        add(id=f"fee-h-{r.token[:8]}", group="legit_fee", mode="historical", created_block=cb, expected_label="not_honeypot", expect_scam=0, **common)
        add(id=f"fee-l-{r.token[:8]}", group="legit_fee", mode="latest", expected_label="not_honeypot", expect_scam=0, **common)

# well-known normal tokens: latest on their main V2 pair + historical at fixed old blocks
normal = json.load(open(os.path.join(G, "normal_pools.json")))
for n in normal:
    add(id=f"norm-l-{n['symbol']}-{n['kind']}", group="normal", token=n["token"], symbol=n["symbol"], pool=n["pool"], dex=n["dex"], mode="latest",
        expected_label="normal", expect_scam=0, confidence="high", source_urls=f"https://etherscan.io/token/{n['token']}",
        notes="widely used token; expected no honeypot/high-tax/rug (owner_controlled acceptable)")
    for blk in n.get("hist_blocks", []):
        add(id=f"norm-h-{n['symbol']}-{blk}", group="normal_hist", token=n["token"], symbol=n["symbol"], pool=n["pool"], dex=n["dex"], mode="historical",
            block=blk, expected_label="normal", expect_scam=0, confidence="high", source_urls=f"https://etherscan.io/token/{n['token']}", notes=f"historical block {blk}")

out = os.path.join(G, "expected.csv")
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
print(len(rows), "rows ->", out)
print(pd.DataFrame(rows).groupby(["group", "mode"]).size())
