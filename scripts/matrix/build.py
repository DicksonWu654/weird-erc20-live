#!/usr/bin/env python3
"""Build the Tier-1 matrix (A1). Idempotent: re-run any time (e.g. when W3's control results change).

    .venv/bin/python scripts/matrix/build.py [--no-json]

Inputs (read-only): data/probe/<chain>/results.final.jsonl, data/control/<chain>/control.jsonl, data/universe/universe.parquet
Outputs: data/matrix/matrix.parquet, matrix.json (site-ready), summary.csv, columns.json, manifest.json, tokens.csv
"""
import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
import re
import sys
import time

import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import features as FT  # noqa: E402

CHAINS = ["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism"]
OUT = ROOT / "data" / "matrix"
SEC_RX = re.compile(r"xstock|tokenized|^backed |\betf\b|t-bills?|money market fund|preferred stock|treasury (bond|fund)|ondo (short|us)|\bstock\b", re.I)
BANDS = [("1-100", 1, 100), ("101-500", 101, 500), ("501-2000", 501, 2000), ("extras(non-mcap)", None, None)]
CATS = ["stablecoin", "lst_lrt", "meme", "tokenized_security", "wrapped_bridged"]


def band_of(rank, source="coingecko_mcap"):
    """rank bands apply to market-cap-ranked rows only (rank = position in the final list, mcap rows first); everything admitted by another
    rule (Uniswap default list, GeckoTerminal volume, CG volume-without-mcap, Blockscout holders) is the separate 'extras(non-mcap)' band."""
    if source != "coingecko_mcap":
        return "extras(non-mcap)"
    for n, lo, hi in BANDS[:3]:
        if lo <= rank <= hi:
            return n
    return "extras(non-mcap)"


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load_probe(chain):
    T = collections.defaultdict(dict)
    for l in open(ROOT / "data" / "probe" / chain / "results.final.jsonl"):
        r = json.loads(l)
        T[r["token"].lower()][r["test"]] = r
    return T


def load_control(chain):
    p = ROOT / "data" / "control" / chain / "control.jsonl"
    out = {}
    if not p.exists():
        return out
    for l in open(p):
        try:
            r = json.loads(l)
        except Exception:  # a row being appended right now
            continue
        out[r["token"].lower()] = r
    overlay = ROOT / "data/control/c19/derived.jsonl"
    if overlay.exists():
        for line in overlay.read_text().splitlines():
            r = json.loads(line)
            if r["chain"] == chain:
                if out[r["token"]]["block"] != r["block"]:
                    raise ValueError("legacy overlay block differs from source; re-derive it")
                out[r["token"]] = r
    return out


def probe_status(T):
    if not T:
        return "no_probe_rows", ""
    if "probe" in T and T["probe"]["status"] in FT.UNP:
        return "unprobeable", str(T["probe"]["value"])[:80]
    f = T.get("funding")
    if f is None or f["status"] != "pass":
        return "views_only", str(f["value"])[:80] if f else "no_funding_row"
    tr = T.get("transfer_return")
    if tr is None or tr["status"] in FT.UNP:
        return "views_only", "transfer_unprobeable"
    if str(tr["value"]).startswith("reverts"):
        return "transfers_blocked", str(tr["value"])[:80]
    return "probed", f["value"]


def cats_of(u):
    s = set(filter(None, (u.get("category_hint") or "").split(";")))
    out = {c for c in CATS if c in s}
    if SEC_RX.search(f"{u.get('name_onchain') or ''} {u.get('symbol_onchain') or ''}"):
        out.add("tokenized_security")
    return sorted(out)


def short_ev(ev, token):
    if not ev:
        return ""
    if ev["src"] == "probe":
        return ",".join(str(c) for c in ev["cids"][:3])
    refs = [r for r in (ev.get("refs") or []) if token in r][:3]
    return ",".join(r.split("#", 1)[-1] for r in refs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-json", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    uni = pd.read_parquet(ROOT / "data" / "universe" / "universe.parquet")
    grouping = pd.read_csv(ROOT / "data/universe/same_asset_groups.csv").fillna("")
    group_map = {(r.chain, r.address.lower()): r for r in grouping.itertuples()}
    uni["a"] = uni.address.str.lower()
    for c_ in ("market_cap_usd", "rank"):
        uni[c_] = pd.to_numeric(uni[c_], errors="coerce")
    ui = uni.set_index(["chain", "a"])
    recs, manifest = [], {"built_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "control": {}, "probe": {}}
    for ch in CHAINS:
        T_all = load_probe(ch)
        C_all = load_control(ch)
        cp = ROOT / "data" / "control" / ch / "control.jsonl"
        manifest["control"][ch] = {"rows": len(C_all), "complete": sum(1 for r in C_all.values() if r.get("completeness") == "complete" and r.get("status") == "ok"),
                                   "mtime": dt.datetime.fromtimestamp(cp.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if cp.exists() else None}
        manifest["probe"][ch] = {"tokens": len(T_all)}
        sub = uni[uni.chain == ch]
        for _, u in sub.iterrows():
            tok = u["a"]
            T = T_all.get(tok, {})
            Cn = C_all.get(tok)
            ps, pr = probe_status(T)
            cells = {}
            for fid in FT.FEATURE_ORDER:
                cells[fid] = FT.FEATURES[fid]["fn"](T, Cn, u)
            rank = int(u["rank"])
            group = group_map.get((ch, tok))
            recs.append({
                "chain": ch, "token": tok, "symbol": u["symbol_onchain"], "name": u["name_onchain"], "rank": rank, "rank_band": band_of(rank, u["source"]),
                "market_cap_usd": None if pd.isna(u["market_cap_usd"]) else float(u["market_cap_usd"]),
                "same_asset_group": group.same_asset_group if group else u["same_asset_group"] or None,
                "asset_family": getattr(group, "asset_family", ""),
                "deployment_relationship": getattr(group, "deployment_relationship", "unresolved"),
                "categories": cats_of(u), "source": u["source"],
                "probe_status": ps, "probe_status_detail": pr, "probe_block": next((r["block"] for r in T.values() if r.get("block")), None),
                "probed_at": next((r["probed_at"] for r in T.values() if r.get("probed_at")), None),
                "control_block": Cn.get("block") if Cn else None, "cells": cells,
            })
        print(ch, len(sub), f"{time.time() - t0:.0f}s", flush=True)

    # ---- parquet (wide) -------------------------------------------------------------------------------------------
    wide = []
    for r in recs:
        d = {k: v for k, v in r.items() if k != "cells"}
        d["categories"] = ";".join(d["categories"])
        for fid, (st, val, ev) in r["cells"].items():
            d[f"{fid}__status"] = st
            d[f"{fid}__value"] = val
            d[f"{fid}__ev"] = json.dumps(ev, separators=(",", ":")) if ev else None
        wide.append(d)
    df = pd.DataFrame(wide)
    df.to_parquet(OUT / "matrix.parquet", index=False)
    df[["chain", "token", "symbol", "name", "rank", "rank_band", "market_cap_usd", "same_asset_group", "categories", "probe_status", "probe_status_detail"]].to_csv(OUT / "tokens.csv", index=False)

    # ---- columns.json ----------------------------------------------------------------------------------------------
    cols = [{"id": f["id"], "family": f["family"], "kind": f["kind"], "label": f["label"], "src": f["src"]} for f in FT.F]
    (OUT / "columns.json").write_text(json.dumps(cols, indent=1))

    # ---- site json --------------------------------------------------------------------------------------------------
    if not a.no_json:
        code = {"yes": "y", "no": "n", "unprobeable": "u", "error": "e"}
        # per column: the most common value among 'no' cells is elided from the cells (stored once in columns[].default_no)
        dflt = {}
        for fid in FT.FEATURE_ORDER:
            cnt = collections.Counter(r["cells"][fid][1] for r in recs if r["cells"][fid][0] == "no")
            dflt[fid] = cnt.most_common(1)[0][0] if cnt else ""
        for c_ in cols:
            c_["default_no"] = dflt[c_["id"]]
        rows = []
        for r in recs:
            cells = []
            for fid in FT.FEATURE_ORDER:
                st, val, ev = r["cells"][fid]
                c = [code.get(st, "u")]
                evs = short_ev(ev, r["token"])
                if st == "no" and val == dflt[fid]:
                    val = ""
                if val or evs:
                    c.append(val)
                if evs:
                    c.append(evs)
                cells.append(c)
            rows.append({"c": r["chain"], "t": r["token"], "s": r["symbol"], "n": r["name"], "r": r["rank"], "m": r["market_cap_usd"],
                         "g": r["same_asset_group"], "asset_family": r["asset_family"], "deployment_relationship": r["deployment_relationship"],
                         "k": r["categories"], "p": r["probe_status"], "x": cells})
        doc = {"generated": manifest["built_at"],
               "evidence": {"probe": "data/probe/{chain}/raw/{token}.json.gz  (cell ev = call ids 'cid' inside calls[])",
                            "control": "data/control/{chain}/evidence/{token}.jsonl  (cell ev = line refs '#eN')"},
               "status_codes": {"y": "present / observed", "n": "measured, absent", "u": "unprobeable (value = reason)", "e": "error"},
               "columns": cols, "rows": rows}
        (OUT / "matrix.json").write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
        with open(OUT / "matrix.json.gz", "wb") as f:
            f.write(gzip.compress((OUT / "matrix.json").read_bytes(), mtime=0))
        print("matrix.json", (OUT / "matrix.json").stat().st_size / 1e6, "MB")

    # ---- summary.csv --------------------------------------------------------------------------------------------------
    srows = []
    fe = [f for f in FT.F if f["kind"] != "meta"]
    groups = []  # (dim, chain, band, category, mask)
    ch_s = df.chain
    groups.append(("all", "all", "all", "all", pd.Series(True, index=df.index)))
    for ch in CHAINS:
        groups.append(("chain", ch, "all", "all", ch_s == ch))
    for b, _, _ in BANDS:
        groups.append(("band", "all", b, "all", df.rank_band == b))
        for ch in CHAINS:
            groups.append(("chain_band", ch, b, "all", (ch_s == ch) & (df.rank_band == b)))
    catmask = {c: df.categories.str.split(";").apply(lambda x, c=c: c in x) for c in CATS}
    for c in CATS:
        groups.append(("category", "all", "all", c, catmask[c]))
        for ch in CHAINS:
            groups.append(("chain_category", ch, "all", c, (ch_s == ch) & catmask[c]))
    for dim, ch, band, cat, m in groups:
        sub = df[m]
        if len(sub) == 0:
            continue
        for f in fe:
            s = sub[f"{f['id']}__status"]
            n_yes = int((s == "yes").sum())
            n_no = int((s == "no").sum())
            n = n_yes + n_no
            lo, hi = wilson(n_yes, n)
            srows.append({"dim": dim, "chain": ch, "rank_band": band, "category": cat, "feature": f["id"], "family": f["family"], "kind": f["kind"],
                          "n_tokens": len(sub), "n_measurable": n, "n_yes": n_yes, "n_unmeasurable": len(sub) - n,
                          "rate": (n_yes / n) if n else None, "ci95_lo": lo, "ci95_hi": hi,
                          "scope": "inventory_including_extras_use_prevalence_tables_for_rates",
                          "ci_interpretation": "descriptive Wilson; ranked snapshot; excludes classification error"})
    pd.DataFrame(srows).to_csv(OUT / "summary.csv", index=False, float_format="%.5f")

    # ---- value distributions of categorical columns ----------------------------------------------------------------
    dist = []
    for fid in ("decimals", "permit_any", "upgrade_authority_class", "owner_path_class", "is_proxy", "funding"):
        for ch in ["all"] + CHAINS:
            sub = df if ch == "all" else df[df.chain == ch]
            vc = sub[f"{fid}__value"].where(sub[f"{fid}__status"].isin(["yes", "no"]), "unmeasurable:" + sub[f"{fid}__status"].astype(str)).value_counts()
            for v, n in vc.items():
                dist.append({"feature": fid, "chain": ch, "value": v, "n": int(n)})
    pd.DataFrame(dist).to_csv(OUT / "value_distributions.csv", index=False)

    # ---- manifest ----------------------------------------------------------------------------------------------------
    manifest["n_rows"] = len(recs)
    manifest["probe_status_counts"] = dict(collections.Counter(r["probe_status"] for r in recs))
    manifest["features"] = len(FT.F)
    manifest["prevalence_policy"] = "main=market-cap rows; extras separate; ranks 501-2000 Ethereum only; CIs descriptive"
    manifest["cross_chain_policy"] = "market-cap ranks 1-500 only; all feature/comparison tests Holm adjusted within analysis set"
    manifest["securities_exclusion_policy"] = "original tokenized_security category supplemented by saved Base B20 stock policy 5; see tokenized_securities.csv exclusion_basis"
    inputs = [ROOT / "data/universe/universe.parquet", ROOT / "data/universe/same_asset_groups.csv",
              ROOT / "data/control/c19/derived.jsonl"]
    inputs += [ROOT / "data/probe" / ch / "results.final.jsonl" for ch in CHAINS]
    inputs += [ROOT / "data/control" / ch / "control.jsonl" for ch in CHAINS]
    inputs += [ROOT / "scripts/matrix" / name for name in ("build.py", "features.py", "prevalence.py", "subsets.py", "crosschain.py", "group_assets.py")]
    manifest["input_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs if p.exists()}
    from prevalence import build_tables
    build_tables(df)
    from subsets import build_subsets
    build_subsets(df)
    manifest["elapsed_s"] = round(time.time() - t0, 1)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest["probe_status_counts"]), f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
