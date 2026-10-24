#!/usr/bin/env python3
"""Revised Tier-2 trade and 30-day LP-withdrawal series.

Trade categories are observed at the historical simulation block.  LP outcomes
are separate, second-phase estimates.  Missing adjudications stay missing.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

import aggregate as A

ROOT, OUT = A.ROOT, A.OUT
BASE = A.LABELS
OUTCOMES = BASE + ["headline_trade_harm", "owner_control_capability",
                   "liquidity_pulled", "insider_rug"]
MEASURABLE = {"honeypot", "high_tax", "owner_controlled", "normal"}
RUG_LABELS = {"liquidity_pulled", "insider_rug"}
_BLOCKMAP = pd.read_csv(ROOT / "data/census/blockmap.csv")


def kish(w):
    w = np.asarray(w, dtype=float)
    return float(w.sum()**2 / (w  @  w)) if w.size and w  @  w > 0 else 0.0


def wilson(p, n, alpha=.05):
    if n <= 0 or not np.isfinite(p):
        return (0., 1.)
    z = NormalDist().inv_cdf(1-alpha/2)
    den = 1+z*z/n
    c = (p+z*z/(2*n))/den
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/den
    return max(0., c-h), min(1., c+h)


def is_eligible(d):
    return d.rug_screen_verdict.eq("liquidity_declined_screen_only") & ~d.base_category.isin(
        ["dust_pool", "honeypot", "no_pool", "pending_v4"])


def raw_timestamps(record):
    by_tx, first_mint = {}, []
    for env in record.get("raw", []):
        result = (env.get("response") or {}).get("result", [])
        if not isinstance(result, list):
            continue
        for ev in result:
            if not isinstance(ev, dict) or not ev.get("timeStamp"):
                continue
            ts = int(ev["timeStamp"], 16)
            by_tx[str(ev.get("transactionHash") or "").lower()] = ts
            if "mint" in str(env.get("label", "")).lower() or "add" in str(env.get("label", "")).lower():
                first_mint.append(ts)
    return by_tx, min(first_mint) if first_mint else None


def classify_pull(record, peak_record, creator, sender):
    """Conservative three-valued rule; positives need the complete V2 Sync peak.

    A single burn of >=80% of the 30-day peak establishes a pull.  Multiple
    burns are unresolved unless the blind reconstruction adjudicated them.
    Total burns below 80% of a known lower bound on peak establish a negative.
    """
    if record.get("error") or not record.get("detail"):
        return None, None, "second_pass_error", None
    detail = record["detail"]
    if detail.get("truncated"):
        return None, None, "truncated_event_history", None
    v2 = (record.get("sample") or {}).get("dex") in ("uniswap_v2", "sushiswap_v2", "sushiswap")
    screen = (record.get("sample") or {}).get("screen") or {}
    peak_lower = max((int(s.get("quote") or 0) for s in screen.get("series", [])
                      if s.get("label") in ("c+1", "c+7d", "sim_block")), default=0.)
    peak = None
    if peak_record and not peak_record.get("error") and not peak_record.get("truncated"):
        peak = int(peak_record.get("peak_quote_30d") or 0)
    if peak_lower <= 0 and not peak:
        return None, None, "zero_peak", None
    pool_block = int((record.get("sample") or {}).get("created_block") or 0)
    block_pos = np.searchsorted(_BLOCKMAP.last_block.to_numpy(), pool_block, side="left")
    pool_month = str(_BLOCKMAP.month.iloc[block_pos]) if block_pos < len(_BLOCKMAP) else "9999-99"
    # A complete history of burns through the pinned head is an upper bound
    # on withdrawals within 30 days.  This negative conclusion needs neither
    # a Mint timestamp nor an exact peak (the sparse peak is a lower bound).
    benchmark = peak if peak else peak_lower
    if pool_month <= "2026-08" and 5*int(detail.get("quote_burned_total") or 0) < 4*benchmark:
        return False, False, "complete_burn_upper_bound", None
    by_tx, mint_start = raw_timestamps(record)
    start = (peak_record or {}).get("creation_timestamp") if v2 else mint_start
    if start is None:
        return None, None, "no_creation_timestamp" if v2 else "no_initial_liquidity_timestamp", None
    # A request's retrieval time can be later than its pinned log head.  Use
    # the saved head block timestamp; for older pools the month bound alone
    # proves that the 30-day window has elapsed.
    head_time = (peak_record or {}).get("head_timestamp")
    mature = bool(head_time >= start + 30*86400) if head_time is not None else pool_month <= "2026-08"
    burns = detail.get("burns_all") or []
    within = []
    for b in burns:
        ts = by_tx.get(str(b.get("tx") or "").lower())
        if ts is None:
            return None, None, "burn_timestamp_missing", None
        if start <= ts <= start + 30*86400:
            within.append(b)
    linked = {str(creator).lower(), str(sender).lower()} - {"none", "nan", ""}
    linked_burns = [b for b in within if len(b.get("lp_senders") or []) == 1 and
                    str((b.get("lp_senders") or [None])[0]).lower() in linked]
    total = sum(int(b.get("quote_out") or 0) for b in within)
    cumulative_pull = cumulative_insider = False
    if peak and peak_record and peak_record.get("raw"):
        quote0 = bool(((record.get("sample") or {}).get("pool_info") or {}).get("quote_is_token0"))
        peak_point = None
        for env in peak_record["raw"]:
            result = (env.get("response") or {}).get("result", [])
            if not isinstance(result, list):
                continue
            for ev in result:
                data = ev.get("data", "")
                if len(data) < 130 or not ev.get("timeStamp"):
                    continue
                ts = int(ev["timeStamp"], 16)
                if not start <= ts <= start + 30*86400:
                    continue
                q = int(data[2:66], 16) if quote0 else int(data[66:130], 16)
                if q == peak:
                    point = (int(ev["blockNumber"], 16), int(ev.get("logIndex") or "0x0", 16))
                    if peak_point is None or point < peak_point:
                        peak_point = point
        if peak_point:
            # Count only burns in later blocks, but subtract every Mint at or
            # after the peak event.  This conservative treatment of same-block
            # ordering cannot create a false cumulative positive.
            after_burns = [b for b in within if int(b.get("block") or 0) > peak_point[0]]
            deposits = 0
            for env in record.get("raw", []):
                if not str(env.get("label", "")).startswith("es_mint:"):
                    continue
                result = (env.get("response") or {}).get("result", [])
                if not isinstance(result, list):
                    continue
                for ev in result:
                    data = ev.get("data", "")
                    if len(data) < 130 or not ev.get("timeStamp"):
                        continue
                    ts = int(ev["timeStamp"], 16)
                    point = (int(ev["blockNumber"], 16), int(ev.get("logIndex") or "0x0", 16))
                    if point >= peak_point and ts <= start + 30*86400:
                        deposits += int(data[2:66], 16) if quote0 else int(data[66:130], 16)
            net_all = sum(int(b.get("quote_out") or 0) for b in after_burns) - deposits
            net_linked = sum(int(b.get("quote_out") or 0) for b in after_burns if b in linked_burns) - deposits
            cumulative_pull = 5*net_all >= 4*peak
            cumulative_insider = 5*net_linked >= 4*peak
    if not mature and not within:
        return None, None, "right_censored_before_30d", None
    # Full quote-balance reconstruction is unavailable for V1/V3; keep positive
    # claims unidentified there.  V3 Burn is owed principal, not a transfer.
    single_pull = bool(peak and any(5*int(b.get("quote_out") or 0) >= 4*peak for b in within))
    single_insider = bool(peak and any(5*int(b.get("quote_out") or 0) >= 4*peak for b in linked_burns))
    if mature and v2 and peak and (single_pull or cumulative_pull):
        pull = True
    elif mature and 5*total < 4*benchmark:
        pull = False
    else:
        pull = None
    if pull is False:
        insider = False
    elif pull is True and mature and v2 and peak and (single_insider or cumulative_insider):
        insider = True
    else:
        insider = None
    method = ("v2_sync_single_burn" if single_pull else "v2_sync_net_post_peak_burns") if pull is True else "conservative_event_bounds"
    return pull, insider, method, peak


def adjudicate(df):
    blind = pd.read_csv(ROOT / "data/verify/rug/verdicts.csv")
    blind_map = {(r.address.lower(), r.pool.lower()): r.verdict for r in blind.itertuples()}
    values = {}
    for p in (OUT / "rug-second-pass").glob("*.json"):
        x = json.loads(p.read_text())
        s = x.get("sample") or {}
        k = A.key(s["token"], s["pool"], s["block"])
        sync_path = OUT / "rug-second-pass/sync-peaks" / p.name
        peak_path = OUT / "rug-second-pass/exact-peaks" / p.name
        peak = json.loads(sync_path.read_text()) if sync_path.exists() else None
        exact = json.loads(peak_path.read_text()) if peak_path.exists() else None
        if peak is not None and exact is not None:
            peak.update(exact)
        values[k] = (x, peak if exact is not None else None, str(p.relative_to(ROOT)),
                     str(peak_path.relative_to(ROOT)) if exact else None)
    cols = {name: [] for name in ("liquidity_pulled_observed", "insider_rug_observed", "rug_adjudication_method",
                                "rug_peak_quote_30d", "rug_peak_evidence")}
    for r in df.itertuples():
        k = A.key(r.address, r.selected_pool, r.sim_block) if r.selected_pool and pd.notna(r.sim_block) else None
        item = values.get(k)
        if not item:
            outcome = (None, None, "not_second_phase_checked", None)
            peak = None
            peakpath = None
        else:
            x, peak, _, peakpath = item
            outcome = classify_pull(x, peak, r.creator_contract_or_eoa, r.tx_sender)
        for name, value in zip(("liquidity_pulled_observed", "insider_rug_observed", "rug_adjudication_method",
                                "rug_peak_quote_30d"), outcome):
            if name == "rug_peak_quote_30d" and peak and not peak.get("error"):
                value = peak.get("peak_quote_30d")
            if name == "rug_peak_quote_30d" and value is not None:
                value = str(value)
            cols[name].append(value)
        cols["rug_peak_evidence"].append(peakpath)
    for name, values in cols.items():
        df[name] = values
    df["rug_blind_verdict"] = [blind_map.get((str(r.address).lower(), str(r.selected_pool).lower()))
                                for r in df.itertuples()]
    eligible = is_eligible(df)
    df["rug_phase2_pi"] = np.nan
    rates = []
    for month, g in df[eligible].groupby("month"):
        # Duplicate simulator evidence keys are one second-stage selection unit.
        N = len(g.drop_duplicates(["address", "selected_pool", "sim_block"]))
        checked = g[g.rug_second_pass_sampled]
        n = len(checked.drop_duplicates(["address", "selected_pool", "sim_block"]))
        if N:
            df.loc[g.index, "rug_phase2_pi"] = n/N
        rates.append({"frame_month": month, "eligible_N": N, "eligible_checked": n,
                      "pi2_eligible": n/N if N else np.nan,
                      "liquidity_positive": int(checked.liquidity_pulled_observed.eq(True).sum()),
                      "liquidity_unknown": int(checked.liquidity_pulled_observed.isna().sum()),
                      "insider_positive": int(checked.insider_rug_observed.eq(True).sum()),
                      "insider_unknown": int(checked.insider_rug_observed.isna().sum()),
                      "eligible_checked_kish": kish(checked.weight)})
    pd.DataFrame(rates).to_csv(OUT / "rug_rates_by_frame_month.csv", index=False)
    df["rug_two_phase_weight"] = np.where(eligible & df.rug_second_pass_sampled,
                                            df.weight/df.rug_phase2_pi, np.nan)
    df["owner_control_capability"] = pd.array(
        [pd.NA if pd.isna(primary) else "owner_controlled" in json.loads(flags or "[]")
         for primary, flags in zip(df.sim_primary_label, df.sim_flags)], dtype="boolean")
    df.to_parquet(OUT / "token_results.parquet", index=False)
    return df


def phase1_variance(d, z, blocks, counts):
    """C12's stratified score variance, with creation-block PSUs and FPC."""
    total, unsampled = 0., False
    # N_h^2/n_h below supplies the expansion.  Multiplying each observation
    # by its first-stage weight here would count that expansion twice.
    zseries = pd.Series(np.asarray(z, dtype=float), index=d.index)
    for (month, source, stratum), g in d.groupby(["month", "source", "stratum"]):
        n = int(g.n_stratum_sampled.iloc[0])
        N = float(g.N_stratum.iloc[0])
        x = zseries.loc[g.index].to_numpy(dtype=float)
        if source == "blocksample":
            n = int(counts.loc[month, "n_blocks_sampled"])
            N = int(blocks.loc[month, "last_block"]) - max(int(blocks.loc[month, "first_block"]), 21_850_000) + 1
            x = g.assign(z=x).groupby("creation_block").z.sum().to_numpy(dtype=float)
        if n >= N or n <= 1:
            continue
        unsampled = True
        s2 = max(0., float(x  @  x - x.sum()**2/n))/(n-1)
        total += N*N*(1-n/N)*s2/n
    return total, unsampled


def base_interval(d, a, p, blocks, counts, alpha=.05, frame=None):
    D = float(d.weight.sum())
    if not D:
        return (np.nan, np.nan, np.nan)
    # A domain ratio has score a - p*b on the entire sampled frame.  Rows
    # outside the denominator contribute zero; dropping them understates the
    # stratum variance, especially for measurable and deployment-method cuts.
    if frame is None:
        frame = d
    z = np.zeros(len(frame), dtype=float)
    loc = frame.index.get_indexer(d.index)
    if (loc < 0).any():
        raise ValueError("domain rows missing from variance frame")
    z[loc] = np.asarray(a, dtype=float)-p
    v, unsampled = phase1_variance(frame, z, blocks, counts)
    v /= D*D
    ne = p*(1-p)/v if 0 < p < 1 and v > 1e-20 else kish(d.weight)
    lo, hi = wilson(p, ne, alpha) if unsampled else (p, p)
    return lo, hi, ne


def denominators(m, period):
    ds = {"headline_excl_lp": m, "pooled_tokens": m[m.pool_ever],
          "measurable_pooled": m[m.pool_ever & m.base_category.isin(MEASURABLE)],
          "tradable_ge_0_1_eth": m[m.pool_ever & m.tradable_ge_0_1_eth & m.base_category.isin(MEASURABLE)]}
    if period >= "2025":
        for method, suffix in (("direct_deploy", "direct"), ("factory", "factory")):
            ds[f"headline_excl_lp_{suffix}"] = m[m.creation_method.eq(method)]
            ds[f"pooled_tokens_{suffix}"] = m[m.pool_ever & m.creation_method.eq(method)]
    return ds


def estimate_period(df, frequency, counts, blocks):
    df = df[~((df.frame_basis == "first_pool_month") & (df.analysis_month < "2025-02"))].copy()
    df["period"] = (df.analysis_month if frequency == "monthly" else
                    df.analysis_month.str[:4] + "Q" + (((df.analysis_month.str[5:7].astype(int)-1)//3)+1).astype(str)
                    if frequency == "quarterly" else df.analysis_month.str[:4])
    rows = []
    for period, pframe in df.groupby("period", sort=True):
        for denominator, d in denominators(pframe, period).items():
            if d.empty:
                continue
            all_new = denominator.startswith("headline_excl_lp")
            # February's no-pool prefix has zero inclusion probability.  A
            # monthly full-population point is undefined; longer periods
            # explicitly exclude it and are marked as restricted periods.
            restricted = all_new and ("2025-02" in set(d.analysis_month))
            if restricted and frequency != "monthly":
                d = d[d.analysis_month.ne("2025-02")]
            variance_frame = (pframe[pframe.analysis_month.ne("2025-02")]
                              if restricted and frequency != "monthly" else pframe)
            if d.empty:
                continue
            D = float(d.weight.sum())
            month_counts = counts.loc[sorted(set(d.analysis_month)), "n_est"]
            target = float(month_counts.sum()) if denominator == "headline_excl_lp" else D
            dk = kish(d.weight)
            eligible = is_eligible(d)
            v4_unassessed = float(d.loc[d.selected_dex.eq("uniswap_v4"), "weight"].sum())/D
            for label in OUTCOMES:
                capability_unassessed = 0
                if label in RUG_LABELS:
                    s = d[eligible & d.rug_second_pass_sampled]
                    outcome = s[label+"_observed"]
                    valid = s[outcome.notna()]
                    unknown = s[outcome.isna()]
                    pos = valid[valid[label+"_observed"].eq(True)]
                    mass = float(pos.rug_two_phase_weight.sum())
                    upper_mass = mass + float(unknown.rug_two_phase_weight.sum())
                    # Unassessed V4 and zero-support frame months have an
                    # identification range, never a manufactured zero.
                    missing_frame = any((d.loc[eligible & d.month.eq(fm), "rug_second_pass_sampled"].sum() == 0)
                                        for fm in d.loc[eligible, "month"].unique())
                    if missing_frame:
                        missing_mass = float(d.loc[eligible & ~d.month.isin(s.month), "weight"].sum())
                        upper_mass += missing_mass
                    upper_mass += float(d.loc[d.selected_dex.eq("uniswap_v4"), "weight"].sum())
                    identified = (not restricted and unknown.empty and not missing_frame and
                                  v4_unassessed == 0 and 0 <= mass/D <= 1)
                    point_raw = mass/D
                    if restricted and frequency == "monthly":
                        point_raw = np.nan
                    point = point_raw if identified and 0 <= point_raw <= 1 else np.nan
                    lo_id, hi_id = max(0., min(1., mass/D)), max(0., min(1., upper_mass/D))
                    if not 0 <= mass/D <= 1:
                        lo_id = hi_id = np.nan
                    if restricted and frequency == "monthly":
                        lo_id = hi_id = np.nan
                    if np.isfinite(point):
                        a = np.zeros(len(d))
                        loc = d.index.get_indexer(pos.index)
                        a[loc] = pos.rug_two_phase_weight.to_numpy(dtype=float)/d.loc[pos.index, "weight"].to_numpy(dtype=float)
                        p1lo, p1hi, _ = base_interval(d, a, point, blocks, counts, .025,
                                                     variance_frame)
                        # Bonferroni envelope for the eligible second-stage
                        # rates; this is approximate for the direct HT point.
                        K = max(1, len(s.month.unique()))
                        down = up = 0.
                        for fm, sg in s.groupby("month"):
                            known = sg[sg[label+"_observed"].notna()]
                            if known.empty:
                                continue
                            w = known.weight.to_numpy(dtype=float)
                            rate = float(w  @  known[label+"_observed"].astype(float).to_numpy()/w.sum())
                            pi2 = float(sg.rug_phase2_pi.iloc[0])
                            L, H = (rate, rate) if pi2 >= 1 else wilson(rate, kish(w), .025/K)
                            em = float(d.loc[eligible & d.month.eq(fm), "weight"].sum())/D
                            down += em*(rate-L); up += em*(H-rate)
                        ci_low, ci_high = max(0., p1lo-down), min(1., p1hi+up)
                    else:
                        ci_low = ci_high = np.nan
                    pos_eff = kish(pos.rug_two_phase_weight)
                    phase2_eff = min((kish(g.weight) for _, g in s.groupby("month")), default=0.)
                    eff = min(pos_eff, phase2_eff)
                    n2, npos, nunknown = len(s), len(pos), len(unknown)
                    reason = "screen_detected_30d;V2_Sync_peak_or_C11;unassessed_V4_bounds"
                    method = "direct_HT_plus_Bonferroni_phase1_score_phase2_Wilson_approx95"
                else:
                    if label == "owner_control_capability":
                        y = d.owner_control_capability.eq(True).fillna(False).to_numpy(dtype=float)
                        capability_unknown = d.owner_control_capability.isna()
                    else:
                        cats = {"honeypot", "high_tax"} if label == "headline_trade_harm" else {label}
                        y = d.base_category.isin(cats).to_numpy(dtype=float)
                        capability_unknown = pd.Series(False, index=d.index)
                    mass = float(d.weight.to_numpy(dtype=float)  @  y)
                    p = mass/D
                    identified = not (restricted and frequency == "monthly") and not capability_unknown.any()
                    point = p if identified else np.nan
                    if identified:
                        ci_low, ci_high, _ = base_interval(d, y, p, blocks, counts,
                                                           frame=variance_frame)
                    else:
                        ci_low = ci_high = np.nan
                    lo_id = p
                    hi_id = min(1., p + float(d.loc[capability_unknown, "weight"].sum())/D)
                    if restricted and frequency == "monthly":
                        lo_id = hi_id = np.nan
                    pos_eff = kish(d.loc[y.astype(bool), "weight"])
                    phase2_eff = np.nan
                    eff = min(dk, pos_eff)
                    n2 = npos = nunknown = 0
                    capability_unassessed = int(capability_unknown.sum())
                    reason = ("trade_at_sim_block;owner_controlled_separate" if label == "headline_trade_harm" else
                              "sim_owner_control_flag;unprobed_unknown" if label == "owner_control_capability" else
                              "base_trade_category")
                    method = "stratified_phase1_score_Kish_boundary_FPC_approx95"
                    if A.excluded_from_denominator(denominator, label):
                        point = ci_low = ci_high = lo_id = hi_id = np.nan
                        identified = False
                        reason = "structurally_excluded_by_denominator"
                        method = "not_applicable_outside_denominator"
                rows.append({"period": period, "denominator": denominator, "label": label,
                             "n_sampled": len(d), "share": point, "ci_low": ci_low, "ci_high": ci_high,
                             "est_count": point*target if np.isfinite(point) else np.nan,
                             "denominator_est_count": target, "frame_denominator_weight": D,
                             "denominator_effective_n": dk, "positive_effective_n": pos_eff,
                             "phase2_effective_n": phase2_eff, "effective_n": eff,
                             "low_n": bool(eff < 10),
                             "quarterly_publishable": bool(eff >= 30 and not restricted)
                             if frequency == "quarterly" and label == "headline_trade_harm" else np.nan,
                             "identified": bool(identified), "identification_low": lo_id, "identification_high": hi_id,
                             "phase2_checked": n2, "phase2_positive": npos, "phase2_unknown": nunknown,
                             "capability_unassessed": capability_unassessed,
                             "v4_unassessed_weight_share": v4_unassessed if label in RUG_LABELS else 0.,
                             "point_raw_ht": point_raw if label in RUG_LABELS else np.nan,
                             "period_coverage": "restricted_excludes_2025-02" if restricted and frequency != "monthly" else
                                                "undefined_missing_2025-02_prefix" if restricted else
                                                "year_to_date_Jan_Sep_2026" if frequency == "annual" and period == "2026" else
                                                "full_frame_scope",
                             "measurement_scope": reason, "ci_method": method,
                             "denominator_source": "census_excl_lp" if denominator == "headline_excl_lp" else "frame_weight_sum"})
    out = pd.DataFrame(rows)
    col = {"monthly": "month", "quarterly": "quarter", "annual": "year"}[frequency]
    out = out.rename(columns={"period": col})
    out.to_csv(OUT / f"{frequency}_shares.csv", index=False)
    return out


def precision(df):
    A.precision(df)
    rows = pd.read_csv(OUT / "precision_vs_blind_rows.csv")
    blind = pd.read_csv(ROOT / "data/verify/rug/verdicts.csv")
    lookup = {(str(r.address).lower(), str(r.selected_pool).lower()): r for r in df.itertuples()
              if isinstance(r.selected_pool, str)}
    add = []
    for r in blind.itertuples():
        x = lookup.get((r.address.lower(), r.pool.lower()))
        if x is None:
            continue
        insider_actual = True if r.verdict == "rugged_insider" else False if r.verdict in (
            "died_by_selling", "stable_or_growing", "migrated_or_legit_removal") else None
        # The C11 headline verdict adjudicates creator linkage.  Its saved
        # exact peak and withdrawal records can separately resolve an LP pull
        # by anyone, including an unlinked remover.
        reconstructed = ROOT / "data/verify/rug/reconstructed" / f"{r.address.lower()}.json"
        assessment = ROOT / "data/verify/rug/assessments" / f"{r.address.lower()}.json"
        liquidity_actual = None
        if reconstructed.exists() and assessment.exists():
            rec = json.loads(reconstructed.read_text())
            ass = json.loads(assessment.read_text())
            peak = Decimal(str(rec.get("peak_quote_raw") or 0))
            if peak and not ass.get("censored"):
                removals = ass.get("enriched_removals") or []
                if any(5*Decimal(str(z.get("quote_raw") or 0)) >= 4*peak for z in removals):
                    liquidity_actual = True
                elif 5*Decimal(str(ass.get("all_LP_withdrawals_raw") or 0)) < 4*peak:
                    liquidity_actual = False
        if insider_actual is True:
            liquidity_actual = True
        for label in ("liquidity_pulled", "insider_rug", "legacy_rugged"):
            actual = liquidity_actual if label == "liquidity_pulled" else insider_actual
            pred = (x.rug_detail_verdict in ("rug_lp_pull", "rug_lp_pull_unattributed")) if label == "legacy_rugged" else getattr(x, label+"_observed")
            if pd.isna(pred):
                # The old LP heuristic is shown separately and does not stand
                # in for a strict creator-linked adjudication.
                pred_label = "unresolved"
            else:
                pred_label = "positive" if bool(pred) else "negative"
            add.append({"set": "rug_C11", "token": r.address, "pool": r.pool, "block": x.sim_block,
                        "prediction": pred_label, "verdict": "positive" if actual else "negative" if actual is False else "undetermined",
                        "category": label, "evidence_path": r.evidence})
    pd.concat([rows, pd.DataFrame(add)], ignore_index=True).to_csv(OUT / "precision_vs_blind_rows.csv", index=False)
    summary = pd.read_csv(OUT / "precision_vs_blind.csv")
    extra = []
    for label in ("liquidity_pulled", "insider_rug", "legacy_rugged"):
        g = [x for x in add if x["category"] == label and x["verdict"] != "undetermined"]
        resolved = [x for x in g if x["prediction"] != "unresolved"]
        for metric in ("precision", "recall"):
            den = [x for x in resolved if x["prediction"] == "positive"] if metric == "precision" else [x for x in resolved if x["verdict"] == "positive"]
            tp = sum(x["prediction"] == "positive" and x["verdict"] == "positive" for x in den)
            p = tp/len(den) if den else np.nan
            lo, hi = wilson(p, len(den)) if den else (np.nan, np.nan)
            extra.append({"set": "rug_C11", "label": label, "metric": metric, "n": len(den), "tp": tp,
                          "estimate": p, "ci_low": lo, "ci_high": hi,
                          "n_blind_total": 60, "n_decidable": len(g),
                          "n_resolved_both": len(resolved),
                          "n_pred_unresolved": sum(x["prediction"] == "unresolved" for x in g),
                          "mapping": "C11 exact peak/single withdrawal or complete total bound for liquidity; creator-linked verdict for insider/legacy; both unresolved truths and predictions excluded"})
    pd.concat([summary, pd.DataFrame(extra)], ignore_index=True).to_csv(OUT / "precision_vs_blind.csv", index=False)


def run():
    d = adjudicate(A.assemble())
    census = pd.read_csv(ROOT / "data/census/monthly_counts.csv")
    counts = census[census.series.eq("excl_lp")].set_index("month")
    blocks = pd.read_csv(ROOT / "data/census/blockmap.csv").set_index("month")
    monthly = estimate_period(d, "monthly", counts, blocks)
    estimate_period(d, "quarterly", counts, blocks)
    estimate_period(d, "annual", counts, blocks)
    A.sensitivity(d); A.unprobeable_eras(d); A.timeline(monthly)
    A.spotchecks(d); A.v1_audit_summary(); precision(d)
    print("revised", len(d), "months", monthly.month.nunique(),
          "rug outcomes", Counter(d.liquidity_pulled_observed.dropna()), flush=True)


if __name__ == "__main__":
    run()
