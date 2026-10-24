#!/usr/bin/env python3
"""Join saved simulations to W4's frame and build design-weighted Tier-2 tables."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/tier2"
FRAME = ROOT / "data/census/sample_frame.parquet"
LABELS = ["honeypot", "high_tax", "owner_controlled", "normal", "no_pool", "dust_pool", "unprobeable", "pending_v4"]
SCAM = {"honeypot", "high_tax"}
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
NATIVE = "0x" + "0" * 40


def excluded_from_denominator(denominator, label):
    """Only disjoint trade categories can be structurally excluded.

    Overlapping flags such as owner_control_capability remain measurable even
    though they are not themselves primary trade categories.
    """
    if label not in LABELS:
        return False
    if denominator in ("measurable_pooled", "tradable_ge_0_1_eth"):
        return label in {"no_pool", "dust_pool", "unprobeable", "pending_v4"}
    return denominator.startswith("pooled_tokens") and label == "no_pool"


def key(token, pool, block):
    return str(token).lower(), str(pool).lower(), int(block)


def load_results(path):
    result = {}
    for line in open(path):
        r = json.loads(line)
        result[key(r["token"], r["pool"], r["block"])] = r
    return result


def v1_reserve_map():
    ans = {}
    for p in (OUT / "v1-reserves").glob("*.json"):
        x = json.loads(p.read_text())
        values = []
        for call in x.get("calls", []):
            try:
                values.append(int(call["response"]["result"], 16))
            except (KeyError, TypeError, ValueError):
                values.append(None)
        ans[key(x["token"], x["pool"], x["block"])] = (values + [None, None])[:2] + [str(p.relative_to(ROOT))]
    return ans


def quote_price_map():
    ans = {}
    for p in (OUT / "quote-prices").glob("*.json"):
        x = json.loads(p.read_text())
        try:
            response = x["response"]["result"]
            r0, r1 = int(response[2:66], 16), int(response[66:130], 16)
            if x["reference_token0"].lower() == WETH:
                weth, quote = r0, r1
            else:
                weth, quote = r1, r0
            if weth > 0 and quote > 0:
                ans[(x["quote"].lower(), int(x["block"]))] = weth, quote, str(p.relative_to(ROOT))
        except (KeyError, ValueError, TypeError):
            continue
    return ans


def rug_map():
    ans = {}
    for p in (OUT / "rug-second-pass").glob("*.json"):
        x = json.loads(p.read_text())
        r = x["sample"]
        ans[key(r["token"], r["pool"], r["block"])] = (x.get("verdict") or {}).get("verdict"), str(p.relative_to(ROOT)), x.get("error")
    return ans


def categorized(rec, v1, floor, prices):
    if rec is None:
        return "unprobeable", "missing_sim_result", None, None, None
    pi = rec.get("pool_info") or {}
    quote = str(pi.get("quote") or "").lower()
    raw_reserve = pi.get("reserve_quote")
    reserve = raw_reserve if quote in (NATIVE, WETH) else None
    price_evidence = None
    if reserve is None and raw_reserve is not None and (quote, int(rec["block"])) in prices:
        weth, qreserve, price_evidence = prices[(quote, int(rec["block"]))]
        reserve = int(raw_reserve) * weth // qreserve
    if rec.get("dex") == "uniswap_v1" and v1:
        reserve = v1[0]
        if v1[1] is not None and v1[1] <= 1:
            return "dust_pool", "v1_token_balance_at_most_one_base_unit", reserve, v1[2], price_evidence
    if reserve is not None and int(reserve) < floor:
        return "dust_pool", "quote_reserve_below_floor", int(reserve), v1[2] if v1 else None, price_evidence
    if rec.get("dex") == "uniswap_v1" and str(rec.get("unprobeable") or "").startswith("buy_reverts") and v1 and (v1[0] == 0 or v1[1] == 0):
        return "dust_pool", "empty_v1_exchange", reserve, v1[2], price_evidence
    primary = rec.get("primary_label") or "unprobeable"
    why = rec.get("label_reason") or rec.get("unprobeable") or ""
    if primary == "unprobeable" and why == "dust_pool":
        return "dust_pool", "quote_reserve_below_floor", reserve, v1[2] if v1 else None, price_evidence
    if primary not in LABELS:
        primary = "unprobeable"
    return primary, why, int(reserve) if reserve is not None else None, v1[2] if v1 else None, price_evidence


def assemble(write=True):
    frame = pd.read_parquet(FRAME).reset_index(drop=True)
    frame.insert(0, "sample_id", np.arange(len(frame)))
    blocks = pd.read_csv(ROOT / "data/census/blockmap.csv")
    creation_month = blocks.month.to_numpy()[np.searchsorted(blocks.last_block.to_numpy(), frame.creation_block.to_numpy(), side="left")]
    v23 = load_results(OUT / "sim-run/results.jsonl")
    v4 = load_results(OUT / "v4-results/results.jsonl")
    res = {**v23, **v4}
    v1map, rugs, prices = v1_reserve_map(), rug_map(), quote_price_map()
    manifest = pd.read_csv(OUT / "pair_manifest.csv", keep_default_na=False).set_index("sample_id")
    rows = []
    for sid, f in frame.iterrows():
        m = manifest.loc[sid]
        pool = m.first_quoted_pool_pool_address
        dex = m.first_quoted_pool_dex
        block = int(float(m.first_quoted_pool_block)) + 7200 if m.first_quoted_pool_block else None
        k = key(f.address, pool, block) if pool and block else None
        rec = res.get(k) if k else None
        if not bool(f.pool_ever):
            base, why, reserve, audit, price_evidence = "no_pool", "never_pooled_in_scanned_venues", None, None, None
        elif not pool:
            base, why, reserve, audit, price_evidence = "unprobeable", "no_quoted_pool", None, None, None
        else:
            base, why, reserve, audit, price_evidence = categorized(rec, v1map.get(k), 1_000_000_000, prices)
            if rec is None and dex == "uniswap_v4":
                base, why = "pending_v4", "missing_v4_result"
        rv = rugs.get(k)
        verdict = rv[0] if rv else (rec.get("rug") or {}).get("verdict") if rec else None
        observed = base
        q = str(m.quote_symbol or "")
        record = f.to_dict()
        record.update({"analysis_month": creation_month[sid], "selected_pool": pool or None,
                       "selected_dex": dex or None, "sim_block": block,
                       "category": observed, "base_category": base, "category_reason": why,
                       "unprobeable_reason": why if base == "unprobeable" else None,
                       "quote_symbol": q, "quote_reserve_eth_equiv_wei": reserve,
                       "reserve_audit_evidence": audit, "reserve_conversion_evidence": price_evidence,
                       "sim_evidence": json.dumps((rec or {}).get("evidence")) if rec else None,
                       "sim_primary_label": (rec or {}).get("primary_label"), "sim_flags": json.dumps((rec or {}).get("flags", [])),
                       "sim_taxes": json.dumps((rec or {}).get("taxes", {})),
                       "rug_screen_verdict": (rec.get("rug") or {}).get("verdict") if rec else None,
                       "rug_detail_verdict": rv[0] if rv else None, "rug_detail_evidence": rv[1] if rv else None,
                       "rug_detail_error": rv[2] if rv else None,
                       "rug_second_pass_sampled": bool(rv),
                       "dust_at_0_01_eth": reserve is not None and reserve < 10**16,
                       "tradable_ge_0_1_eth": reserve is not None and reserve >= 10**17,
                       "tradable_reserve_known": reserve is not None,
                       "alternatives_json": m.alternatives_json})
        rows.append(record)
    out = pd.DataFrame(rows)
    if write:
        out.to_parquet(OUT / "token_results.parquet", index=False)
    print("assembled", len(out), Counter(out.category), "joined", out.sim_primary_label.notna().sum(), flush=True)
    return out


def wilson(p, n):
    if not np.isfinite(p) or n <= 0:
        return float("nan"), float("nan")
    z = 1.9599639845
    c = (p + z*z/(2*n))/(1+z*z/n)
    h = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))/(1+z*z/n)
    return max(0.0, c-h), min(1.0, c+h)


def estimate(d, label, rug_rates):
    """Frame-weighted baseline plus a bounded second-phase rug ratio estimate."""
    pop = float(d.weight.sum())
    if not pop:
        return 0., 0., 0., 0.
    w = d.weight.to_numpy(dtype=float)
    labels = {label} if isinstance(label, str) else set(label)
    base = d.base_category.isin(labels).to_numpy()
    count = float(w  @  base)
    rug_uncertainty = 0.
    # The second pass samples all collapse rows, but the rate is computed among
    # non-dust, non-honeypot rows whose primary label a rug can actually change.
    for month, md in d.groupby("month"):
        if month not in rug_rates:
            continue
        rate, rlo, rhi, _ = rug_rates[month]
        eligible = md[(md.rug_screen_verdict == "liquidity_declined_screen_only") &
                      ~md.base_category.isin(["dust_pool", "honeypot", "no_pool", "pending_v4"])]
        if eligible.empty:
            continue
        # Rug precedence moves each eligible base category to rugged. The
        # common within-month rate keeps all estimated counts nonnegative.
        coef = float(eligible.weight.sum()) * int("rugged" in labels)
        coef -= float(eligible.loc[eligible.base_category.isin(labels), "weight"].sum())
        count += coef * rate
        rug_uncertainty += abs(coef) * max(rate-rlo, rhi-rate) / pop
    share = max(0., min(1., count / pop))
    neff = pop*pop / float(w  @  w)
    lo, hi = wilson(share, neff)
    lo, hi = max(0., lo-rug_uncertainty), min(1., hi+rug_uncertainty)
    return share, lo, hi, count


def monthly(df):
    rug_rates = {}
    for month, m in df.groupby("month"):
        eligible = m[(m.rug_screen_verdict == "liquidity_declined_screen_only") &
                     ~m.base_category.isin(["dust_pool", "honeypot", "no_pool", "pending_v4"])]
        sampled = eligible[eligible.rug_second_pass_sampled]
        if sampled.empty:
            rug_rates[month] = (0., 0., 1., 0)
            continue
        w = sampled.weight.to_numpy(dtype=float)
        y = (sampled.category == "rugged").to_numpy(dtype=float)
        rate = float(w  @  y / w.sum())
        neff = float(w.sum()**2 / (w  @  w))
        lo, hi = wilson(rate, neff)
        rug_rates[month] = rate, lo, hi, len(sampled)
    pd.DataFrame([{"frame_month": month, "rug_rate_eligible_collapse": rate,
                   "ci_low": lo, "ci_high": hi, "n_eligible_checked": n}
                  for month, (rate, lo, hi, n) in sorted(rug_rates.items())]).to_csv(OUT / "rug_rates_by_frame_month.csv", index=False)
    rows = []
    df = df[~((df.frame_basis == "first_pool_month") & (df.analysis_month < "2025-02"))].copy()
    census = pd.read_csv(ROOT / "data/census/monthly_counts.csv")
    census = dict(zip(census.loc[census.series == "excl_lp", "month"], census.loc[census.series == "excl_lp", "n_est"]))
    for month, m in df.groupby("analysis_month", sort=True):
        denoms = {"headline_excl_lp": m,
                  "pooled_tokens": m[m.pool_ever],
                  "measurable_pooled": m[m.pool_ever & m.base_category.isin(["honeypot", "high_tax", "owner_controlled", "rugged", "normal"])],
                  "tradable_ge_0_1_eth": m[m.pool_ever & m.tradable_ge_0_1_eth & m.base_category.isin(["honeypot", "high_tax", "owner_controlled", "rugged", "normal"])]}
        if month >= "2025-01":
            for method in ("direct_deploy", "factory"):
                tag = "direct" if method == "direct_deploy" else "factory"
                denoms[f"headline_excl_lp_{tag}"] = m[m.creation_method == method]
                denoms[f"pooled_tokens_{tag}"] = m[m.pool_ever & (m.creation_method == method)]
        for denom, d in denoms.items():
            if d.empty:
                continue
            pop = float(d.weight.sum())
            target_pop = float(census[month]) if denom == "headline_excl_lp" else pop
            count_scale = target_pop / pop
            pending = float(d.loc[d.base_category == "pending_v4", "weight"].sum()) / pop
            raw_estimates = {label: estimate(d, label, rug_rates) for label in LABELS}
            for label in LABELS:
                share, lo, hi, count = raw_estimates[label]
                rows.append({"month": month, "denominator": denom, "label": label, "n_sampled": len(d),
                             "share": share, "ci_low": max(0, min(share, lo)),
                             "ci_high": min(1, max(share, hi)),
                             "est_count": count * count_scale,
                             "denominator_est_count": target_pop, "pending_share": pending,
                             "ci_method": "weighted_wilson_kish_plus_rug_ratio_wilson",
                             "rug_second_pass_n": int(d.rug_second_pass_sampled.sum()),
                             "frame_basis": "+".join(sorted(set(d.frame_basis))),
                             "denominator_source": "census_excl_lp" if denom == "headline_excl_lp" else "frame_weight_sum"})
            for label, cats in (("scam_any", SCAM),):
                share, lo, hi, count = estimate(d, cats, rug_rates)
                rows.append({"month": month, "denominator": denom, "label": label, "n_sampled": len(d),
                             "share": share, "ci_low": max(0, min(share, lo)),
                             "ci_high": min(1, max(share, hi)), "est_count": count * count_scale,
                             "denominator_est_count": target_pop, "pending_share": pending,
                             "ci_method": "weighted_wilson_kish_plus_rug_ratio_wilson", "rug_second_pass_n": int(d.rug_second_pass_sampled.sum()),
                             "frame_basis": "+".join(sorted(set(d.frame_basis))),
                             "denominator_source": "census_excl_lp" if denom == "headline_excl_lp" else "frame_weight_sum"})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "monthly_shares.csv", index=False)
    print("monthly", len(out), out.month.nunique(), sorted(out.denominator.unique()), flush=True)
    return out


def sensitivity(df):
    rows = []
    for month, m in df.groupby("analysis_month", sort=True):
        pooled = m[m.pool_ever]
        pop = float(pooled.weight.sum())
        if not pop:
            continue
        known = pooled.quote_reserve_eth_equiv_wei.notna()
        for floor in (10**9, 10**16, 10**17):
            dust = pooled.base_category.eq("dust_pool") | (known & (pooled.quote_reserve_eth_equiv_wei < floor))
            measurable = ~dust & pooled.base_category.isin(["honeypot", "high_tax", "owner_controlled", "rugged", "normal"])
            weighted_dust = float(pooled.loc[dust, "weight"].sum())
            weighted_meas = float(pooled.loc[measurable, "weight"].sum())
            weighted_scam = float(pooled.loc[measurable & pooled.base_category.isin(SCAM), "weight"].sum())
            rows.append({"month": month, "dust_floor_wei": floor, "n_pooled": len(pooled),
                         "n_reserve_known": int(known.sum()), "reserve_known_weight_share": float(pooled.loc[known, "weight"].sum()) / pop,
                         "dust_share_pooled": weighted_dust / pop,
                         "measurable_share_pooled": weighted_meas / pop,
                         "scam_share_measurable_before_rug_adjustment": weighted_scam / weighted_meas if weighted_meas else float("nan")})
    pd.DataFrame(rows).to_csv(OUT / "dust_sensitivity.csv", index=False)


def unprobeable_eras(df):
    years = df.analysis_month.str[:4].astype(int)
    eras = np.select([years <= 2019, years <= 2022, years <= 2024],
                     ["2017-19", "2020-22", "2023-24"], default="2025-26")
    x = df.copy()
    x["era"] = eras
    x = x[x.pool_ever]
    x["reason_bucket"] = x.unprobeable_reason.fillna("").astype(str).str.split(":").str[0]
    pop = x.groupby("era").weight.sum().to_dict()
    rows = []
    for (era, reason), g in x[x.base_category == "unprobeable"].groupby(["era", "reason_bucket"]):
        rows.append({"era": era, "reason": reason, "n_sampled": len(g),
                     "weight_sum": float(g.weight.sum()), "share_of_pooled": float(g.weight.sum()) / pop[era]})
    pd.DataFrame(rows).sort_values(["era", "weight_sum"], ascending=[True, False]).to_csv(OUT / "unprobeable_by_era.csv", index=False)


def precision(df):
    lookup = {key(r.address, r.selected_pool, r.sim_block): r for r in df.itertuples() if r.selected_pool and pd.notna(r.sim_block)}
    matches = []
    for ver in ("v23", "v4"):
        for r in pd.read_csv(ROOT / f"data/verify/{ver}/verdicts.csv").itertuples():
            k = key(r.token, r.pool, r.block)
            x = lookup.get(k)
            if x is None:
                continue
            # Blind workers tested the trade at the historical state, not the
            # later liquidity trajectory; compare the trade label before rug.
            pred = x.base_category
            if pred == "dust_pool" or (pred == "unprobeable" and x.unprobeable_reason in ("no_active_liquidity", "no_liquidity", "no_positive_sell_quote")):
                pred = "no_liquidity"
            elif pred in ("normal", "owner_controlled", "high_tax"):
                pred = "sellable"
            elif pred == "honeypot":
                pred = "not_sellable"
            actual = r.verdict
            if actual == "sellable" and max(float(r.buy_tax) if pd.notna(r.buy_tax) else 0,
                                              float(r.sell_tax) if pd.notna(r.sell_tax) else 0) >= 10 - 1e-6:
                actual = "high_tax"
            tax_prediction = "high_tax" if x.base_category == "high_tax" else pred
            matches.append({"set": ver, "token": r.token, "pool": r.pool, "block": r.block,
                            "prediction": tax_prediction, "verdict": actual, "category": x.category,
                            "evidence_path": r.evidence_path})
    pd.DataFrame(matches).to_csv(OUT / "precision_vs_blind_rows.csv", index=False)
    rows = []
    for setname in ("v23", "v4", "combined"):
        group = [x for x in matches if setname == "combined" or x["set"] == setname]
        for label in sorted({x["prediction"] for x in group} | {x["verdict"] for x in group}):
            tp = sum(x["prediction"] == label and x["verdict"] == label for x in group)
            pp = sum(x["prediction"] == label for x in group)
            ap = sum(x["verdict"] == label for x in group)
            for metric, n in (("precision", pp), ("recall", ap)):
                p = tp / n if n else float("nan")
                lo, hi = wilson(p, n)
                rows.append({"set": setname, "label": "honeypot" if label == "not_sellable" else label,
                             "metric": metric, "n": n, "tp": tp,
                             "estimate": p, "ci_low": lo, "ci_high": hi,
                             "mapping": "normal/owner_controlled->sellable; honeypot->not_sellable; dust/no_active_liquidity->no_liquidity; tax>=10%->high_tax"})
        for label in ("owner_controlled", "normal"):
            n_pred = sum(x["category"] == label for x in group)
            for metric in ("precision", "recall"):
                rows.append({"set": setname, "label": label, "metric": metric, "n": n_pred if metric == "precision" else 0,
                             "tp": float("nan"), "estimate": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"),
                             "mapping": "not independently adjudicated: sellability does not verify owner control, LP attribution, or absence of both"})
    pd.DataFrame(rows).to_csv(OUT / "precision_vs_blind.csv", index=False)
    print("blind matched", Counter(x["set"] for x in matches), flush=True)


def timeline(ms):
    wide = ms[ms.denominator.isin(["headline_excl_lp", "pooled_tokens", "measurable_pooled"])].copy()
    wide["key"] = wide.denominator + "_" + wide.label
    wide = wide.pivot(index="month", columns="key", values="share").reset_index()
    events = pd.read_csv(ROOT / "research/memecoin-timeline.csv")
    events["month"] = events.date.str[:7]
    agg = events.groupby("month").agg(timeline_event_count=("event", "size"),
                                       timeline_events=("event", lambda s: " | ".join(s)),
                                       timeline_categories=("category", lambda s: " | ".join(sorted(set(s))))).reset_index()
    wide.merge(agg, on="month", how="left").to_csv(OUT / "timeline_join.csv", index=False)


def annual(ms):
    x = ms.copy()
    x["year"] = x.month.str[:4]
    a = x.groupby(["year", "denominator", "label"], as_index=False).agg(
        est_count=("est_count", "sum"), denominator_est_count=("denominator_est_count", "sum"),
        n_sampled=("n_sampled", "sum"), months=("month", "nunique"))
    a["share"] = a.est_count / a.denominator_est_count
    a.to_csv(OUT / "annual_shares.csv", index=False)


def build_share_outputs():
    """Rebuild revised share tables from saved, adjudicated token results."""
    from reaggregate_revision import estimate_period

    df = pd.read_parquet(OUT / "token_results.parquet")
    census = pd.read_csv(ROOT / "data/census/monthly_counts.csv")
    counts = census[census.series.eq("excl_lp")].set_index("month")
    blocks = pd.read_csv(ROOT / "data/census/blockmap.csv").set_index("month")
    for frequency in ("monthly", "quarterly", "annual"):
        table = estimate_period(df, frequency, counts, blocks)
        if frequency == "monthly":
            timeline(table)
        print(f"{frequency} shares: {len(table)} rows", flush=True)


def spotchecks(df):
    # These 21 rows were read in the decoded spotcheck table and compared with
    # their saved category reason, reserve, trade attempts, and raw hashes.
    manually_reviewed = {1, 15990, 29806, 12010, 18140, 29807, 3967, 16464,
                         29835, 0, 15992, 29805, 1555, 15993, 29851, 792,
                         16110, 30840, 3, 16000, 29829}
    candidates = df[df.sim_evidence.notna()].copy()
    candidates["era"] = pd.cut(candidates.creation_block, [0, 10_000_000, 16_800_000, 21_850_000, 30_000_000], labels=["early", "2020-23", "2023-25", "2025-26"])
    sel = []
    for (cat, era), g in candidates.groupby(["category", "era"], observed=True):
        sel.extend(g.sort_values("sample_id").head(1).sample_id.tolist())
    for cat in ("honeypot", "high_tax", "normal", "dust_pool", "unprobeable", "owner_controlled", "rugged"):
        g = candidates[candidates.category == cat]
        sel.extend(g.sort_values("sample_id").head(3).sample_id.tolist())
        sel.extend(g[g.selected_dex == "uniswap_v4"].sort_values("sample_id").head(2).sample_id.tolist())
    sel.extend(sorted(manually_reviewed & set(candidates.sample_id)))
    sel = list(dict.fromkeys(sel))[:60]
    rows = []
    rmap = {**load_results(OUT / "sim-run/results.jsonl"), **load_results(OUT / "v4-results/results.jsonl")}
    for sid in sel:
        x = df.iloc[sid]
        ev = json.loads(x.sim_evidence) if x.sim_evidence else None
        if not ev or not ev.get("raw"):
            continue
        path = ROOT / ev["raw"]
        if not path.exists():
            continue
        data = path.read_bytes()
        raw_json = gzip.decompress(data)
        calls = json.loads(raw_json)
        r = rmap.get(key(x.address, x.selected_pool, x.sim_block))
        if r is None:
            continue
        buy = r.get("buy") or {}
        sells = r.get("sells") or []
        responses = sum("response" in c for c in calls)
        sim_calls = [c for item in calls if item.get("request", {}).get("method") == "eth_simulateV1"
                     for block in (item.get("response", {}).get("result") or []) if isinstance(block, dict)
                     for c in block.get("calls", [])]
        sim_success = sum(c.get("status") == "0x1" for c in sim_calls)
        sim_reverts = sum(c.get("status") == "0x0" for c in sim_calls)
        raw_errors = sorted({str((c.get("error") or {}).get("message", "")) for c in sim_calls if c.get("error")})
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        transfer_events = sum(str((log.get("topics") or [""])[0]).lower() == transfer_topic
                              for c in sim_calls for log in c.get("logs", []))
        rug_calls = 0
        rug_hashes_ok = None
        if isinstance(x.rug_detail_evidence, str) and x.rug_detail_evidence:
            rug_record = json.loads((ROOT / x.rug_detail_evidence).read_text())
            rug_calls = len(rug_record.get("raw", []))
            rug_hashes_ok = all(hashlib.sha256(c["body"].encode()).hexdigest() == c["sha256"]
                                for c in rug_record.get("raw", []) if "body" in c)
        rows.append({"sample_id": sid, "month": x.month, "token": x.address, "pool": x.selected_pool,
                     "category": x.category, "reason": x.category_reason, "raw_path": ev["raw"],
                     "raw_sha256_expected": ev.get("sha256"), "raw_sha256_actual": hashlib.sha256(raw_json).hexdigest(),
                     "raw_sha256_match": hashlib.sha256(raw_json).hexdigest() == ev.get("sha256"),
                     "rpc_calls_decoded": len(calls), "rpc_responses_decoded": responses,
                     "raw_sim_call_success": sim_success, "raw_sim_call_reverts": sim_reverts,
                     "raw_sim_error_reasons": ";".join(raw_errors[:3]), "raw_transfer_events": transfer_events,
                     "rug_raw_calls_decoded": rug_calls, "rug_raw_hashes_match": rug_hashes_ok,
                     "raw_methods": ";".join(sorted(set(c.get("request", {}).get("method", "") for c in calls))),
                     "buy_ok": buy.get("ok"), "buy_expected_out": buy.get("expected_out"),
                     "buy_received": buy.get("received"), "sell_attempts": len(sells),
                     "positive_sell_quotes": sum((s.get("expected_eth") or 0) > 0 for s in sells),
                     "successful_sells": sum(bool(s.get("ok")) for s in sells),
                     "quote_reserve_eth_equiv_wei": x.quote_reserve_eth_equiv_wei,
                     "rug_detail_verdict": x.rug_detail_verdict,
                     "review": "decoded_raw_envelopes_and_compared_summary",
                     "manual_reviewed": sid in manually_reviewed,
                     "manual_review_scope": "category_reason_reserve_buy_sell_hash" if sid in manually_reviewed else None})
    pd.DataFrame(rows).to_csv(OUT / "spotchecks.csv", index=False)
    print("spotchecks", len(rows), "hash pass", sum(x["raw_sha256_match"] for x in rows), flush=True)


def v1_audit_summary():
    rows = []
    reserves = v1_reserve_map()
    for p in sorted((OUT / "v1-audit").glob("*.json.gz")):
        x = json.loads(gzip.decompress(p.read_bytes()))
        k = key(x["token"], x["pool"], x["block"])
        v = reserves.get(k)
        eth = v[0] if v else None
        token = v[1] if v else None
        rows.append({"token": x["token"], "pool": x["pool"], "block": x["block"],
                     "sim_unprobeable": x["sim_unprobeable"], "eth_balance_wei": eth,
                     "token_balance_base_units": token,
                     "audit_raw_path": str(p.relative_to(ROOT)), "audit_raw_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                     "reserve_raw_path": v[2] if v else None,
                     "conclusion": "no_executable_liquidity" if eth == 0 or token is not None and token <= 1 else "unresolved"})
    pd.DataFrame(rows).to_csv(OUT / "v1-audit-summary.csv", index=False)
    print("v1 sample", len(rows), Counter(x["conclusion"] for x in rows), flush=True)


if __name__ == "__main__":
    from reaggregate_revision import run
    run()
