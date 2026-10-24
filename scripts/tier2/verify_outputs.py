#!/usr/bin/env python3
"""Check revised Tier-2 identities, support and saved HTTP body hashes."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from reaggregate_revision import OUT, ROOT, BASE, MEASURABLE, is_eligible


def check_intervals(x, path, period_col):
    """Every reported point needs finite limits that contain it."""
    reported = x.share.notna()
    bad = reported & (x.share > 0) & (x.ci_high <= 0)
    assert not bad.any(), (str(path), "positive share with nonpositive upper CI",
                           x.loc[bad, [period_col, "denominator", "label"]].to_dict("records"))
    bad = reported & (~np.isfinite(x.ci_low) | ~np.isfinite(x.ci_high) |
                      (x.ci_low > x.share + 1e-12) | (x.ci_high < x.share - 1e-12))
    assert not bad.any(), (str(path), "CI does not contain share",
                           x.loc[bad, [period_col, "denominator", "label"]].to_dict("records"))


def check_table(path, period_col):
    x = pd.read_csv(path)
    assert not x.duplicated([period_col, "denominator", "label"]).any()
    assert "scam_any" not in set(x.label) and "rugged" not in set(x.label)
    assert x.share.dropna().between(0, 1).all()
    assert x.ci_low.dropna().between(0, 1).all()
    assert x.ci_high.dropna().between(0, 1).all()
    check_intervals(x, path, period_col)
    excluded = (x.denominator.isin(["measurable_pooled", "tradable_ge_0_1_eth"]) &
                x.label.isin(set(BASE) - MEASURABLE)) | (
                    x.denominator.str.startswith("pooled_tokens") & x.label.eq("no_pool"))
    assert x.loc[excluded, ["share", "ci_low", "ci_high", "est_count",
                            "identification_low", "identification_high"]].isna().all().all()
    assert not x.loc[excluded, "identified"].any()
    assert (x.low_n == (x.effective_n < 10)).all()
    assert x.loc[~x.identified, "share"].isna().all()
    for label in ("headline_trade_harm", "owner_control_capability",
                  "liquidity_pulled", "insider_rug"):
        assert label in set(x.label)
    head = x[x.label.eq("headline_trade_harm")].set_index([period_col, "denominator"])
    hp = x[x.label.eq("honeypot")].set_index([period_col, "denominator"])
    tax = x[x.label.eq("high_tax")].set_index([period_col, "denominator"])
    assert np.allclose(head.share.fillna(-1), (hp.share + tax.share).fillna(-1), atol=1e-12)
    for (period, den), g in x.groupby([period_col, "denominator"]):
        if not g[g.label.eq("headline_trade_harm")].identified.iloc[0]:
            continue
        if den == "measurable_pooled":
            labels = ["honeypot", "high_tax", "owner_controlled", "normal"]
        else:
            labels = BASE
        total = g[g.label.isin(labels)].share.sum()
        assert abs(total-1) < 1e-10, (period, den, total)
    return x


def main():
    m = check_table(OUT / "monthly_shares.csv", "month")
    q = check_table(OUT / "quarterly_shares.csv", "quarter")
    y = check_table(OUT / "annual_shares.csv", "year")
    assert m.month.nunique() == 117
    assert all(m[m.denominator.eq(den)].month.nunique() == 117 for den in ("headline_excl_lp", "measurable_pooled"))
    assert m[m.denominator.eq("measurable_pooled") &
             m.label.eq("owner_control_capability")].identified.all()
    feb = m[m.month.eq("2025-02") & m.denominator.eq("headline_excl_lp")]
    assert feb.share.isna().all() and feb.period_coverage.eq("undefined_missing_2025-02_prefix").all()
    assert y[y.year.eq(2025) & y.denominator.eq("headline_excl_lp")].period_coverage.eq("restricted_excludes_2025-02").all()
    assert y[y.year.eq(2026)].period_coverage.eq("year_to_date_Jan_Sep_2026").all()
    hq = q[q.label.eq("headline_trade_harm")]
    assert (hq.quarterly_publishable.fillna(False) ==
            ((hq.effective_n >= 30) & hq.period_coverage.ne("restricted_excludes_2025-02"))).all()
    d = pd.read_parquet(OUT / "token_results.parquet")
    assert len(d) == 47963 and d.sample_id.nunique() == len(d)
    assert d.category.eq(d.base_category).all()
    assert not d.category.eq("rugged").any()
    assert d.base_category.notna().all()
    assert d.owner_control_capability.notna().eq(d.sim_primary_label.notna()).all()
    assert d.loc[d.insider_rug_observed.eq(True), "liquidity_pulled_observed"].eq(True).all()
    assert d[d.rug_second_pass_sampled & is_eligible(d)].rug_phase2_pi.notna().all()
    elig = d[is_eligible(d)]
    for month, g in elig.groupby("month"):
        N = len(g.drop_duplicates(["address", "selected_pool", "sim_block"]))
        n = len(g[g.rug_second_pass_sampled].drop_duplicates(["address", "selected_pool", "sim_block"]))
        assert n == min(40, N), (month, n, N)
        assert np.allclose(g.rug_phase2_pi, n/N)
    manifest = [json.loads(line) for line in (OUT / "rug-second-pass/sample.jsonl").read_text().splitlines()]
    assert len(manifest) >= 2598
    for r in manifest:
        assert (OUT / "rug-second-pass" / f"{r['token'].lower()[2:]}_{r['block']}.json").exists()
    s = pd.read_csv(OUT / "spotchecks.csv")
    assert len(s) >= 20 and s.raw_sha256_match.all()
    p = pd.read_csv(OUT / "precision_vs_blind_rows.csv")
    assert len(p[p.set.eq("rug_C11")]) == 180
    assert len(p[p.set.isin(["v23", "v4"])]) == 115
    rug_raw_checked = 0
    for path in (OUT / "rug-second-pass").glob("*.json"):
        x = json.loads(path.read_text())
        for env in x.get("raw", []):
            assert hashlib.sha256(env["body"].encode()).hexdigest() == env["sha256"], path
            rug_raw_checked += 1
    raw_files = list((OUT / "rug-second-pass/sync-peaks").glob("*.json"))
    raw_checked = 0
    for path in raw_files:
        x = json.loads(path.read_text())
        for env in x.get("raw", []):
            assert hashlib.sha256(env["body"].encode()).hexdigest() == env["sha256"], path
            raw_checked += 1
    block_raw_checked = 0
    for path in (OUT / "rug-second-pass/creation-blocks").glob("*.json"):
        x = json.loads(path.read_text())
        for env in x.get("attempts", []):
            if "body" in env:
                assert hashlib.sha256(env["body"].encode()).hexdigest() == env["sha256"], path
                block_raw_checked += 1
    exact_files = list((OUT / "rug-second-pass/exact-peaks").glob("*.json"))
    for path in exact_files:
        x = json.loads(path.read_text())
        for stem in ("sync", "creation_block", "head_block"):
            if x.get(f"{stem}_evidence"):
                raw = ROOT / x[f"{stem}_evidence"]
                assert hashlib.sha256(raw.read_bytes()).hexdigest() == x[f"{stem}_sha256"], path
    cross = pd.read_csv(OUT / "peak_crosscheck.csv")
    assert len(cross) >= 20 and cross.matches_displayed_precision.all()
    summary = {"months": int(m.month.nunique()), "monthly_rows": len(m), "quarterly_rows": len(q),
               "annual_rows": len(y), "token_rows": len(d), "spotchecks": len(s),
               "phase2_checked_eligible": int((d.rug_second_pass_sampled & is_eligible(d)).sum()),
               "phase2_positive_liquidity": int(d.liquidity_pulled_observed.eq(True).sum()),
               "phase2_positive_insider": int(d.insider_rug_observed.eq(True).sum()),
               "rug_raw_hashes_checked": rug_raw_checked,
               "sync_raw_hashes_checked": raw_checked,
               "block_raw_hashes_checked": block_raw_checked,
               "exact_peak_files": len(exact_files), "C11_peak_crosscheck": len(cross),
               "monthly_liquidity_points": int(m[m.denominator.eq("measurable_pooled") &
                                                   m.label.eq("liquidity_pulled")].share.notna().sum())}
    (OUT / "revision_audit.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    import sys
    if "--v2" in sys.argv:
        from verify_v2 import verify
        preview = "--preview" in sys.argv
        verify(OUT / "v2" / "preview" if preview else OUT / "v2", preview=preview)
    else:
        main()
