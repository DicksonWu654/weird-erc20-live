#!/usr/bin/env python3
"""Acceptance audit and old/new column diff, using independent raw decoders."""
import gzip
import hashlib
import json
import math
import pathlib

import pandas as pd
import rawdec as R

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data/matrix"


def main():
    before = pd.read_parquet(OUT / "c19_before/matrix.parquet").set_index(["chain", "token"]).sort_index()
    m = pd.read_parquet(OUT / "matrix.parquet").set_index(["chain", "token"]).sort_index()
    assert before.index.equals(m.index) and len(m) == 4740
    diffs = [{"column": c, "rows_changed": 0, "change": "existing_column"} for c in ("chain", "token")]
    counts = []
    for col in m:
        if col not in before:
            diffs.append({"column": col, "rows_changed": len(m), "change": "new_column"})
        else:
            changed = ~m[col].fillna("<NA>").eq(before[col].fillna("<NA>"))
            diffs.append({"column": col, "rows_changed": int(changed.sum()), "change": "existing_column"})
        if col.endswith("__status"):
            b = before[col] if col in before else pd.Series(dtype=str)
            counts.append({"feature": col[:-8], "before_yes": int(b.eq("yes").sum()) if len(b) else None,
                           "after_yes": int(m[col].eq("yes").sum()),
                           "before_measurable": int(b.isin(["yes", "no"]).sum()) if len(b) else None,
                           "after_measurable": int(m[col].isin(["yes", "no"]).sum())})
    pd.DataFrame(diffs).to_csv(OUT / "c19_column_changes.csv", index=False)
    pd.DataFrame(counts).to_csv(OUT / "c19_feature_counts.csv", index=False)
    evidence = []
    phantom = m[m.fresh_address_nonzero_balance__status.eq("yes")]
    for (chain, token), r in phantom.iterrows():
        raw = R.load_raw(chain, token)
        scripts, cid = R.scripts(raw)
        steps = R.S(scripts)
        rb, ra = R.u(steps["transfer", "bal_r0"]), R.u(steps["transfer", "bal_r1"])
        assert rb > 0
        assert all(r[f + "__status"] == "unprobeable" for f in ["fee_on_transfer_observed", "recipient_not_credited", "full_balance_anomaly"])
        evidence.append({"chain": chain, "token": token, "symbol": r.symbol, "check": "phantom_prebalance",
                         "recipient_before": str(rb), "recipient_after": str(ra), "raw": f"data/probe/{chain}/raw/{token}.json.gz", "cid": cid})
    fees = m[m.fee_on_transfer_observed__status.eq("yes")]
    for (chain, token), r in fees.iterrows():
        raw = R.load_raw(chain, token)
        scripts, cid = R.scripts(raw)
        steps = R.S(scripts)
        rb, ra = R.u(steps["transfer", "bal_r0"]), R.u(steps["transfer", "bal_r1"])
        assert rb == 0 and ra >= 0
        # The independent payload read establishes that no remaining fee uses
        # a pre-existing recipient balance as the amount received.
        evidence.append({"chain": chain, "token": token, "symbol": r.symbol, "check": "fee_clean_recipient",
                         "recipient_before": str(rb), "recipient_after": str(ra), "raw": f"data/probe/{chain}/raw/{token}.json.gz", "cid": cid})
    removed = m[before.share_accounting_ratio__status.eq("yes") & m.share_accounting_ratio__status.eq("no")]
    for (chain, token), r in removed.iterrows():
        raw = R.load_raw(chain, token)
        ratios = []
        for c in raw["calls"]:
            if c["tag"] not in ("fund_probe", "fund_verify") or "result" not in c["response"]:
                continue
            params = c["request"].get("params", [])
            if len(params) < 3 or not isinstance(params[2], dict):
                continue
            for override in params[2].values():
                for value in override.get("stateDiff", {}).values():
                    read = R.hexint(c["response"]["result"])
                    slot = int(value, 16)
                    if read and slot:
                        ratios.append(read / slot)
        assert any(abs(math.log2(ratio) - round(math.log2(ratio))) < 2e-8 for ratio in ratios)
        evidence.append({"chain": chain, "token": token, "symbol": r.symbol, "check": "binary_shift_ratio_excluded",
                         "raw": f"data/probe/{chain}/raw/{token}.json.gz", "ratios": json.dumps(ratios)})
    pd.DataFrame(evidence).to_csv(OUT / "c19_raw_spotchecks.csv", index=False)
    assert len(phantom) == 10 and len(fees) == 29 and len(removed) == 9
    checks = json.loads((ROOT / "data/control/c19/legacy_forward_checks.json").read_text())
    nraw = 0
    for p in (ROOT / "data/control/c19/evidence").glob("*.jsonl"):
        for e in map(json.loads, p.read_text().splitlines()):
            canonical = json.dumps(e["response"], sort_keys=True, separators=(",", ":")).encode()
            assert hashlib.sha256(canonical).hexdigest() == e["sha256"]
            if "body" in e:
                assert hashlib.sha256(e["body"].encode()).hexdigest() == e["body_sha256"]
            assert e["request"]["method"] in ("eth_call", "eth_getCode", "eth_getBlockByNumber")
            nraw += 1
    for c in checks:
        r = m.loc[(c["chain"], c["token"])]
        assert c["status"] == "confirmed" and r.upgradeable__status == "yes"
        assert r.upgrade_single_eoa__status == ("no" if c["symbol"] == "USDT" else "yes")
        assert r.upgrade_no_timelock__status == "yes"
    up = m[m.upgradeable__status.eq("yes")]
    assert all(r.upgradeable__value.startswith("authority: " + r.upgrade_authority_class__value) for r in up.itertuples())
    main = pd.read_csv(OUT / "prevalence_by_chain_rank.csv")
    ex = pd.read_csv(OUT / "prevalence_ex_securities.csv")
    extras = pd.read_csv(OUT / "prevalence_extras.csv")
    for rates in (main, ex, extras):
        assert rates.ci_interpretation.str.contains("descriptive").all()
        assert (rates.n_yes <= rates.n_measurable).all()
        assert ((rates.n_measurable + rates.n_unmeasurable) == rates.n_tokens).all()
    assert "extras(non-mcap)" not in set(main.rank_band)
    pooled = main[(main.chain == "all") & (main.rank_band == "all_market_cap")]
    assert set(pooled.n_tokens) == {4198}
    assert set(ex[(ex.chain == "all") & (ex.rank_band == "all_market_cap")].n_tokens) == {3680}
    assert len(pd.read_csv(OUT / "tokenized_securities.csv")) == 523
    assert set(extras[extras.chain == "all"].n_tokens) == {542}
    tests = pd.read_csv(OUT / "cross_chain_tests.csv")
    assert set(tests.rank_band) == {"1-500"} and set(tests.source) == {"coingecko_mcap"}
    assert (tests.p_holm + 1e-10 >= tests.p_raw).all()
    assert (tests.n_tokens_a <= 500).all() and (tests.loc[tests.chain_b != "rest", "n_tokens_b"] <= 500).all()
    for _, family in tests.groupby("holm_family"):
        assert set(family.holm_family_n) == {len(family)}
    # Reconcile the exact list files, rather than merely comparing aggregate totals.
    tornado = pd.read_csv(OUT / "tornado_rejecting.csv")
    overlap = pd.read_csv(OUT / "ronin_and_tornado_rejecting.csv")
    extra = pd.read_csv(OUT / "tornado_only_rejecting.csv")
    assert (len(tornado), len(overlap), len(extra)) == (33, 32, 1)
    assert extra.iloc[0].symbol == "DEP" and not extra.iloc[0].c10_restriction_scope_member
    groups = pd.read_csv(ROOT / "data/universe/same_asset_groups.csv")
    peg = groups[groups.address.str.lower().eq("0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d")].iloc[0]
    issuer = groups[groups.cg_same_asset_group.eq("usd-coin")]
    assert peg.deployment_relationship == "third_party_binance_peg"
    assert set(issuer.deployment_relationship) == {"issuer_deployment"}
    assert peg.asset_family not in set(issuer.asset_family)
    gold = pd.read_csv(OUT / "golden/report.csv")
    assert len(gold) == 114 and int(gold["match"].sum()) >= 107
    manifest = json.loads((OUT / "manifest.json").read_text())
    for path, sha in manifest["input_sha256"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha
    assert gzip.decompress((OUT / "matrix.json.gz").read_bytes()) == (OUT / "matrix.json").read_bytes()
    report = {"status": "pass", "n_deployments": len(m), "golden": f"{int(gold['match'].sum())}/114",
              "phantom_raw_checks": len(phantom), "fee_raw_checks": len(fees), "packing_raw_checks": len(removed),
              "new_raw_response_hashes_verified": nraw, "upgrade_text_class_checks": len(up),
              "main_prevalence_rows": len(main), "ex_securities_rows": len(ex), "cross_chain_tests": len(tests),
              "tornado_total_overlap_outside": [len(tornado), len(overlap), len(extra)]}
    (OUT / "c19_verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
