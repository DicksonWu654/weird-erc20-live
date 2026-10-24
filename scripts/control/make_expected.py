#!/usr/bin/env python3
"""Convert the independently sourced facts (data/control-golden/sourcing/golden_sourcing.jsonl, collected by a research
helper that never saw this tool's output) into data/control-golden/expected.csv. Mapping rules (documented, deterministic):
  * kind 'unknown' / null -> blank (not asserted);  owner kind 'none' stays 'none' (asserts: no owner path found)
  * roles.minter whose holder is the token owner (ARB, OP, CAKE: mint is onlyOwner) is asserted as OWNER, not as minter
  * DAI: the documented 'minter' (DaiJoin) is a `wards` entry, which the classifier reports in owner_chain
  * proxy_type 'custom' is checked loosely by golden.py (observed proxy of any non-standard type)
"""
import csv
import json
import pathlib

G = pathlib.Path(__file__).resolve().parents[2] / "data" / "control-golden"
COLS = ["chain", "token", "symbol", "confidence", "exp_is_proxy", "exp_proxy_type", "exp_upgrade_kind", "exp_upgrade_addr",
        "exp_owner_kind", "exp_owner_addr", "exp_minter_addr", "exp_minter_kind", "exp_pauser_addr", "exp_pauser_kind",
        "exp_blacklister_addr", "exp_blacklister_kind", "exp_has_blacklist", "exp_has_mint", "exp_immutable", "sources",
        "notes"]


def b(x):
    return "" if x is None else str(x).lower()


def kind(x):
    return "" if not x or x in ("unknown",) else x


rows = []
for line in (G / "sourcing" / "golden_sourcing.jsonl").read_text().splitlines():
    j = json.loads(line)
    e = j["expect"]
    ua, ow = e.get("upgrade_admin") or {}, e.get("owner") or {}
    roles = e.get("roles") or {}
    r = {c: "" for c in COLS}
    r.update(chain=j["chain"], token=j["token"], symbol=j["symbol"], confidence=j["confidence"],
             exp_is_proxy=b(e.get("is_proxy")), exp_proxy_type=e.get("proxy_type") or "",
             exp_upgrade_kind=kind(ua.get("kind")), exp_upgrade_addr=(ua.get("address") or ""),
             exp_owner_kind=kind(ow.get("kind")), exp_owner_addr=(ow.get("address") or ""),
             exp_has_blacklist=b(e.get("has_blacklist")), exp_has_mint=b(e.get("has_mint")),
             exp_immutable=b(e.get("immutable_no_admin")))
    notes = []
    for role in ("minter", "pauser", "blacklister"):
        ro = roles.get(role) or {}
        r[f"exp_{role}_addr"] = ro.get("address") or ""
        r[f"exp_{role}_kind"] = kind(ro.get("kind"))
    sym = j["symbol"]
    if sym == "DAI":
        r["exp_owner_addr"], r["exp_owner_kind"] = r["exp_minter_addr"], ""
        r["exp_minter_addr"] = r["exp_minter_kind"] = ""
        notes.append("mapping: documented ward (DaiJoin) asserted as owner_chain member")
    if sym in ("ARB", "OP", "CAKE"):
        if r["exp_minter_addr"] and not r["exp_owner_addr"]:
            r["exp_owner_addr"] = r["exp_minter_addr"]
        if r["exp_minter_kind"] and not r["exp_owner_kind"]:
            r["exp_owner_kind"] = r["exp_minter_kind"]
        r["exp_minter_addr"] = r["exp_minter_kind"] = ""
        notes.append("mapping: mint is onlyOwner, asserted as owner")
    r["sources"] = " | ".join(dict.fromkeys(s["url"].split("  [")[0] for s in j["sources"]))[:600]
    r["notes"] = (" ".join(notes) + " " + (j.get("notes") or "")).strip()[:500]
    rows.append(r)
with open(G / "expected.csv", "w", newline="") as f:
    w = csv.DictWriter(f, COLS)
    w.writeheader()
    w.writerows(rows)
print(len(rows), "rows")
