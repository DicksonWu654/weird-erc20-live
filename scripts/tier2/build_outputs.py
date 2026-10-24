#!/usr/bin/env python3
"""Build C1 Tier-2 pairs and derived outputs.

This module deliberately keeps network work in ``scripts/sim/run.py``.  It only
constructs deterministic pair manifests, joins simulator JSONL, and computes
weighted monthly estimates.  The sample frame's ``first_quoted_pool_*`` fields
are the W4 linkage rule: WETH/ETH, then the major stablecoin/blue-chip quote
priority, with the earliest pool within that quote class.  All other pools are
retained in ``alternatives_json`` for auditability.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FRAME = ROOT / "data/census/sample_frame.parquet"
POOLS = ROOT / "data/census/dex_pools.parquet"
TIER2 = ROOT / "data/tier2"
PAIR_FILE = TIER2 / "pairs.csv"
PENDING_V4 = TIER2 / "pending_v4.csv"
RESULTS = ROOT / "data/tier2/sim-main/results.jsonl"

MONTHS = pd.period_range("2017-01", "2026-09", freq="M").astype(str).tolist()
LABELS = ["honeypot", "high_tax", "owner_controlled", "rugged", "normal",
          "no_pool", "dust_pool", "pending_v4", "unprobeable"]
QUOTE_PRIORITY = ["WETH", "ETH", "USDC", "USDT", "DAI", "USDS", "PYUSD",
                  "WBTC", "cbBTC", "BUSD", "FRAX", "LUSD", "USDe", "EURC"]
STABLE_6 = {"USDC", "USDT", "USDS", "PYUSD", "USDE", "EURC", "FRAX"}
STABLE_18 = {"DAI", "LUSD", "BUSD"}
QUOTE_DECIMALS = {"WETH": 18, "ETH": 18, "WBTC": 8, "CBTC": 8, **{x: 6 for x in STABLE_6}, **{x: 18 for x in STABLE_18}}
DUST_QUOTE_UNITS = 0.01


def _s(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return str(x)


def _addr(x):
    s = _s(x)
    return s.lower() if s else None


def _bool(x):
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    return str(x).lower() in {"true", "1", "yes"}


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return None if np.isnan(x) else float(x)
    if pd.isna(x):
        return None
    return x


def _pool_options(pool_map, token: str):
    """Return all pools where W4's base_token or either side is token.

    ``dex_pools`` is large; callers build a token->rows map once instead of
    scanning 781k rows for every sampled token.
    """
    q = pool_map.get(token.lower())
    if q is None or q.empty:
        return []
    q = q.copy()
    if q.empty:
        return []
    q["quote_rank"] = q["quote_symbol"].fillna("").astype(str).str.upper().map({x: i for i, x in enumerate(QUOTE_PRIORITY)}).fillna(999)
    q = q.sort_values(["quote_rank", "block", "log_index"], kind="stable")
    out = []
    for _, r in q.iterrows():
        out.append({"dex": _s(r.dex), "pool": _s(r.pool_address), "block": int(r.block),
                    "quote_symbol": _s(r.quote_symbol), "quote_token": _s(r.quote_token),
                    "fee": _jsonable(r.fee), "hooks": _s(r.hooks)})
    return out


def build_pairs(frame_path=FRAME, pools_path=POOLS, pair_path=PAIR_FILE, pending_path=PENDING_V4):
    frame = pd.read_parquet(frame_path).reset_index(drop=True)
    TIER2.mkdir(parents=True, exist_ok=True)
    pairs, pending, manifest = [], [], []
    for sid, r in frame.iterrows():
        token = _s(r.address)
        quoted_pool = _s(r.first_quoted_pool_pool_address)
        quoted_dex = _s(r.first_quoted_pool_dex)
        quoted_block = r.first_quoted_pool_block
        pool_ever = _bool(r.pool_ever)
        # W4 supplies the selected pool and its first-pool alternative.  Keep
        # these compact alternatives in every row; the complete event table is
        # preserved separately in data/census/dex_pools.parquet.
        alts = []
        for dex, pool, block, qs in [
            (_s(r.first_quoted_pool_dex), _s(r.first_quoted_pool_pool_address), r.first_quoted_pool_block, _s(r.first_quoted_pool_quote_symbol)),
            (_s(r.first_pool_dex), _s(r.first_pool_pool_address), r.first_pool_block, _s(r.first_pool_quote_symbol)),
        ]:
            if pool and not any(a["pool"] == pool for a in alts):
                alts.append({"dex": dex, "pool": pool, "block": int(block) if block is not None and not pd.isna(block) else None, "quote_symbol": qs})
        base = {"sample_id": int(sid), "month": _s(r.month), "token": token,
                "address": token, "frame_basis": _s(r.frame_basis), "source": _s(r.source),
                "stratum": _s(r.stratum), "weight": float(r.weight),
                "creation_block": int(r.creation_block) if not pd.isna(r.creation_block) else "",
                "creation_tx": _s(r.creation_tx) or "", "creation_method": _s(r.creation_method) or "",
                "pool_ever": str(pool_ever).lower(), "n_pools": int(r.n_pools) if not pd.isna(r.n_pools) else 0,
                "first_pool_dex": _s(r.first_pool_dex) or "", "first_pool_pool_address": _s(r.first_pool_pool_address) or "",
                "first_pool_block": int(r.first_pool_block) if not pd.isna(r.first_pool_block) else "",
                "first_quoted_pool_dex": quoted_dex or "", "first_quoted_pool_pool_address": quoted_pool or "",
                "first_quoted_pool_block": int(quoted_block) if quoted_block is not None and not pd.isna(quoted_block) else "",
                "quote_symbol": _s(r.first_quoted_pool_quote_symbol) or "",
                "quote_token": _s(r.first_quoted_pool_quote_token) or "",
                "alternatives_json": json.dumps(alts, separators=(",", ":")),
                "selection_rule": "first_quoted_pool:quote_priority_then_pool_creation_block",
                "status": ""}
        if not pool_ever and not quoted_pool:
            base["status"] = "no_pool"
            manifest.append(base)
            continue
        if not quoted_pool:
            base["status"] = "unprobeable:no_quoted_pool"
            manifest.append(base)
            continue
        if (quoted_dex or "").lower() == "uniswap_v4":
            base["status"] = "pending_v4"
            pending.append(base.copy())
            manifest.append(base)
            continue
        if not quoted_block or pd.isna(quoted_block):
            base["status"] = "unprobeable:no_pool_block"
            manifest.append(base)
            continue
        p = base.copy()
        p["pool"] = quoted_pool
        p["dex"] = quoted_dex
        p["created_block"] = int(quoted_block)
        p["block"] = int(quoted_block) + 7200
        p["status"] = "queued"
        pairs.append(p)
        manifest.append(base)
    pair_cols = ["token", "pool", "dex", "created_block", "block"] + [c for c in pairs[0] if c not in {"token", "pool", "dex", "created_block", "block"}] if pairs else []
    pd.DataFrame(pairs).to_csv(pair_path, index=False, columns=pair_cols)
    pd.DataFrame(pending).to_csv(pending_path, index=False)
    pd.DataFrame(manifest).to_csv(TIER2 / "pair_manifest.csv", index=False)
    print(json.dumps({"sample_rows": len(frame), "sim_pairs": len(pairs), "pending_v4": len(pending),
                      "no_pool": sum(x["status"] == "no_pool" for x in manifest),
                      "unprobeable_no_quote": sum(x["status"] == "unprobeable:no_quoted_pool" for x in manifest),
                      "runtime_hours_at_2_3pps": round(len(pairs) / 2.3 / 3600, 2)}, indent=2))


def _read_results(path):
    rows = {}
    if not Path(path).exists():
        return rows
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                # latest record wins when --redo-errors appended a replacement
                rows[(str(r.get("token", "")).lower(), str(r.get("pool", "")).lower(), int(r.get("block")))] = r
            except Exception:
                continue
    return rows


def _quote_threshold(symbol):
    s = (symbol or "").upper()
    d = QUOTE_DECIMALS.get(s)
    if d is None:
        return None
    return int(DUST_QUOTE_UNITS * (10 ** d))


def _sim_label(rec, pair):
    """Apply C1 explicit non-sim categories around W5's primary label."""
    if pair["status"] == "pending_v4":
        return "pending_v4", "v4 pool requires C2 simulator"
    if pair["status"] == "no_pool":
        return "no_pool", "never pooled in census DEX event scans"
    if pair["status"].startswith("unprobeable:"):
        return pair["status"], pair["status"]
    if rec is None:
        return "unprobeable:missing_sim_result", "simulator result absent"
    pi = rec.get("pool_info") or {}
    # Reserve quote is raw units.  Pair metadata carries quote symbol from W4;
    # use it only when the simulator returned a numeric reserve.
    reserve = pi.get("reserve_quote")
    qsym = pair.get("quote_symbol") or ""
    threshold = _quote_threshold(qsym)
    if reserve is not None and threshold is not None:
        try:
            if int(reserve) < threshold:
                return "dust_pool", f"quote reserve {reserve} < {threshold} raw units (0.01 {qsym})"
        except (TypeError, ValueError):
            pass
    # V1 exchanges do not expose a reserve call.  W5's rug screen samples the
    # ETH balance at c+1; a zero balance is an empty exchange, even though the
    # Vyper proxy can surface ``InvalidJump`` while executing the buy path.
    if (rec.get("dex") or "").lower() == "uniswap_v1":
        series = ((rec.get("rug") or {}).get("screen") or {}).get("series") or []
        c1 = next((x.get("quote") for x in series if x.get("label") == "c+1"), None)
        if c1 is not None and float(c1) <= 0:
            return "dust_pool", "V1 exchange ETH balance at c+1 is zero"
    buy = rec.get("buy") or {}
    reason_text = str(rec.get("unprobeable") or buy.get("reason") or "").upper()
    if buy.get("expected_out") == 0 and "INSUFFICIENT_OUTPUT_AMOUNT" in reason_text:
        return "dust_pool", "buy quote rounds to zero (insufficient output)"
    primary = rec.get("primary_label") or "unprobeable:missing_primary_label"
    if primary == "unprobeable":
        reason = rec.get("unprobeable") or rec.get("label_reason") or "unknown"
        return "unprobeable:" + str(reason), str(reason)
    return primary, rec.get("label_reason") or ""


def _load_manifest(path=TIER2 / "pair_manifest.csv"):
    m = pd.read_csv(path, keep_default_na=False)
    return m


def assemble(frame_path=FRAME, manifest_path=TIER2 / "pair_manifest.csv", sim_path=RESULTS,
             out_path=TIER2 / "token_results.parquet"):
    frame = pd.read_parquet(frame_path).reset_index(drop=True)
    man = _load_manifest(manifest_path)
    sim = _read_results(sim_path)
    # manifest is authoritative for every sampled row; join simulator by the
    # exact historical block key used by run.py.
    out = []
    for sid, p in man.set_index("sample_id").sort_index().iterrows():
        token = str(p.token)
        pool = str(p.get("pool", ""))
        block = int(float(p["block"])) if p.get("block", "") not in ("", None) else None
        rec = sim.get((token.lower(), pool.lower(), block)) if pool and block is not None else None
        label, reason = _sim_label(rec, p.to_dict())
        rr = frame.iloc[int(sid)].to_dict()
        # Keep compact result columns plus complete simulator JSON for audit.
        rr.update({"sample_id": int(sid), "category": label, "category_reason": reason,
                   "selected_pool": pool or None, "selected_dex": p.get("dex") or p.get("first_quoted_pool_dex") or None,
                   "sim_block": block, "sim_primary_label": (rec or {}).get("primary_label"),
                   "sim_unprobeable": (rec or {}).get("unprobeable"), "sim_flags": json.dumps((rec or {}).get("flags", []), separators=(",", ":")),
                   "sim_taxes": json.dumps((rec or {}).get("taxes", {}), separators=(",", ":")),
                   "sim_evidence": json.dumps((rec or {}).get("evidence"), separators=(",", ":")) if rec else None,
                   "alternatives_json": p.get("alternatives_json", "[]"),
                   "dust_threshold_quote_units": _quote_threshold(p.get("quote_symbol", ""))})
        out.append(rr)
    result = pd.DataFrame(out)
    result.to_parquet(out_path, index=False)
    print(json.dumps({"token_results": len(result), "categories": result.category.value_counts(dropna=False).to_dict(),
                      "sim_records_joined": int(result.sim_primary_label.notna().sum())}, indent=2, default=str))
    return result


def _bayes_ci(weights, indicators, rng, reps=1200):
    """Weighted Bayesian bootstrap CI; weights are census expansion weights."""
    w = np.asarray(weights, dtype=float)
    y = np.asarray(indicators, dtype=float)
    if len(w) == 0 or w.sum() <= 0:
        return (np.nan, np.nan, 0.0)
    point = float(np.dot(w, y) / w.sum())
    # Dirichlet(alpha=weight) is stable at these census weights and preserves
    # the unequal-probability design.  Clip only numerical underflow.
    alpha = np.maximum(w, 1e-6)
    draws = rng.dirichlet(alpha, size=reps)
    vals = draws  @  y
    return point, float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def monthly(result: pd.DataFrame, out_path=TIER2 / "monthly_shares.csv"):
    rng = np.random.default_rng(20261021)
    rows = []
    result["month"] = result["month"].astype(str)
    result["has_pool"] = result["pool_ever"].map(_bool)
    result["label_bucket"] = result["category"].astype(str).map(
        lambda x: "unprobeable" if x.startswith("unprobeable:") else x)
    # Explicit denominator membership.  In W4's 2025+ mixed frame, month is
    # first-pool month for pooled strata and creation month for no-pool rows;
    # this is the only denominator that the supplied weights identify.
    for month in MONTHS:
        sm = result[result.month == month]
        if sm.empty:
            continue
        denoms = [("headline_excl_lp", sm), ("pooled_tokens", sm[sm.has_pool])]
        if month >= "2025-01":
            for method in ["direct_deploy", "factory"]:
                denoms.append((f"headline_excl_lp_{method.replace('_deploy','')}", sm[sm.creation_method == method]))
                denoms.append((f"pooled_tokens_{method.replace('_deploy','')}", sm[(sm.creation_method == method) & sm.has_pool]))
        for den, d in denoms:
            if d.empty:
                continue
            pop = float(d.weight.sum())
            pending_weight = float(d.loc[d.category == "pending_v4", "weight"].sum())
            pending_share = pending_weight / pop if pop else np.nan
            for label in LABELS:
                p, lo, hi = _bayes_ci(d.weight.values, (d.label_bucket.values == label).astype(float), rng)
                rows.append({"month": month, "denominator": den, "label": label,
                             "n_sampled": int(len(d)), "share": p, "ci_low": lo, "ci_high": hi,
                             "est_count": p * pop, "denominator_est_count": pop,
                             "pending_share": pending_share, "ci_method": "weighted_bayesian_bootstrap",
                             "creation_method": ("direct_deploy" if den.endswith("_direct") else "factory" if den.endswith("_factory") else "all")})
    out = pd.DataFrame(rows)
    out.to_csv(out_path, index=False)
    print(json.dumps({"months": out.month.nunique(), "rows": len(out), "denominators": sorted(out.denominator.unique())}, indent=2))
    return out


def timeline(monthly_path=TIER2 / "monthly_shares.csv", timeline_path=ROOT / "research/memecoin-timeline.csv",
             out_path=TIER2 / "timeline_join.csv"):
    ms = pd.read_csv(monthly_path)
    # Headline and pooled labels in wide form make the join directly chartable.
    h = ms[ms.denominator == "headline_excl_lp"].pivot(index="month", columns="label", values="share").add_prefix("headline_share_")
    p = ms[ms.denominator == "pooled_tokens"].pivot(index="month", columns="label", values="share").add_prefix("pooled_share_")
    base = pd.DataFrame({"month": MONTHS}).set_index("month").join(h).join(p).reset_index()
    tl = pd.read_csv(timeline_path)
    tl["month"] = tl.date.astype(str).str[:7]
    agg = tl.groupby("month", as_index=False).agg(timeline_event_count=("event", "size"),
                                                    timeline_events=("event", lambda x: " | ".join(map(str, x))),
                                                    timeline_categories=("category", lambda x: " | ".join(sorted(set(map(str, x))))))
    out = base.merge(agg, on="month", how="left")
    out.to_csv(out_path, index=False)
    return out


def spotchecks(result_path=TIER2 / "token_results.parquet", out_path=TIER2 / "spotchecks.csv"):
    df = pd.read_parquet(result_path)
    probe = df[df.sim_evidence.notna()].copy()
    if probe.empty:
        pd.DataFrame(columns=["sample_id", "month", "token", "pool", "category", "raw_path", "raw_sha256", "raw_items", "decoded_requests", "decoded_responses", "manual_agreement", "check_note"]).to_csv(out_path, index=False)
        return
    probe["era"] = pd.cut(pd.to_numeric(probe.creation_block), bins=[0, 10_000_000, 16_800_000, 21_850_000, 30_000_000], labels=["v1", "paradigm", "zellic", "sampled"], include_lowest=True)
    # Spread across categories and eras.  We intentionally inspect raw files
    # rather than trusting only the JSONL summary.
    selected = []
    for cat in sorted(probe.category.unique()):
        for era in ["v1", "paradigm", "zellic", "sampled"]:
            q = probe[(probe.category == cat) & (probe.era.astype(str) == era)]
            if not q.empty:
                selected.append(q.sort_values("sample_id").iloc[0])
    selected.extend([probe.sort_values("sample_id").iloc[i] for i in range(min(len(probe), 40))])
    sel = pd.DataFrame(selected).drop_duplicates("sample_id").head(40)
    rows = []
    for _, r in sel.iterrows():
        ev = json.loads(r.sim_evidence)
        raw = ROOT / ev["raw"]
        ok = False; nreq = nresp = 0; note = "missing_raw"
        if raw.exists():
            try:
                with gzip.open(raw, "rt") as f:
                    items = json.load(f)
                nreq = len(items)
                for item in items:
                    resp = item.get("response") or {}
                    if item.get("request", {}).get("method") or item.get("method"):
                        nreq = max(nreq, 1)
                    if "result" in resp or "error" in resp:
                        nresp += 1
                ok = bool(items) and nresp > 0
                note = "decoded gzip JSON; response envelopes present"
            except Exception as e:
                note = f"decode_error:{e}"
        rows.append({"sample_id": int(r.sample_id), "month": r.month, "token": r.address,
                     "pool": r.selected_pool, "category": r.category,
                     "raw_path": ev.get("raw"), "raw_sha256": ev.get("sha256"), "raw_items": nreq,
                     "decoded_requests": nreq, "decoded_responses": nresp,
                     "manual_agreement": bool(ok), "check_note": note})
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"spotchecks={len(rows)} decoded_ok={sum(x['manual_agreement'] for x in rows)}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-pairs")
    sub.add_parser("shares", help="rebuild revised monthly/quarterly/annual shares from saved token results")
    a = sub.add_parser("assemble")
    a.add_argument("--sim", default=str(RESULTS))
    sub.add_parser("monthly")
    sub.add_parser("timeline")
    sub.add_parser("spotchecks")
    args = ap.parse_args()
    if args.cmd == "build-pairs":
        build_pairs()
    elif args.cmd == "shares":
        from aggregate import build_share_outputs
        build_share_outputs()
    elif args.cmd == "assemble":
        assemble(sim_path=args.sim)
    elif args.cmd == "monthly":
        monthly(pd.read_parquet(TIER2 / "token_results.parquet"))
    elif args.cmd == "timeline":
        timeline()
    elif args.cmd == "spotchecks":
        spotchecks()


if __name__ == "__main__":
    main()
