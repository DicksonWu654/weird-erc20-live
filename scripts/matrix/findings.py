#!/usr/bin/env python3
"""Tidy CSVs for each Tier-1 finding (A1). Reads data/matrix/matrix.parquet (+ control rows, + raw evidence for revert decoding).

    .venv/bin/python scripts/matrix/findings.py
Outputs data/matrix/findings/*.csv and prints a short log (data/matrix/findings/findings_log.txt)
"""
import collections
import json
import math
import pathlib
import sys

import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import features as FT  # noqa: E402
import rawdec as R  # noqa: E402
from build import wilson, CHAINS, CATS, load_control  # noqa: E402
from prevalence import CI_NOTE, stat as strict_stat  # noqa: E402

OUT = ROOT / "data" / "matrix" / "findings"
OUT.mkdir(parents=True, exist_ok=True)
LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s)


def stat(df, ids):
    """ANY-of semantics; a negative union requires all components measured."""
    return strict_stat(df, ids)


def rec(rows, **kw):
    rows.append(kw)


def load():
    m = pd.read_parquet(ROOT / "data" / "matrix" / "matrix.parquet")
    m["cat_list"] = m.categories.fillna("").str.split(";")
    return m


def dxo_table(m):
    rows = []
    mcap = m[m.rank_band != "extras(non-mcap)"]
    groups = [("all", "all", mcap), ("mcap_ranked_only", "all", mcap)]
    groups += [("chain", ch, mcap[mcap.chain == ch]) for ch in CHAINS]
    groups += [("band", b, mcap[mcap.rank_band == b]) for b in ["1-100", "101-500", "501-2000"]]
    groups += [("category", c, mcap[mcap.cat_list.apply(lambda x, c=c: c in x)]) for c in CATS]
    for name, ids, note in FT.DXO:
        for dim, g, sub in groups:
            n, k = stat(sub, ids)
            lo, hi = wilson(k, n)
            rec(rows, d_xo_section=name, definition=note, features=";".join(ids), dim=dim, group=g, n_tokens=len(sub), n_measurable=n, n_yes=k,
                rate=(k / n) if n else None, ci95_lo=lo, ci95_hi=hi, analysis_set="market_cap", ci_interpretation=CI_NOTE)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "dxo_prevalence.csv", index=False, float_format="%.5f")
    a = df[(df.dim == "all")][["d_xo_section", "n_measurable", "n_yes", "rate", "ci95_lo", "ci95_hi"]]
    t100 = df[(df.dim == "band") & (df.group == "1-100")].set_index("d_xo_section")
    log(f"\n== d-xo prevalence ({len(mcap)} market-cap deployments; extras separate; descriptive CIs) | top-100 band pooled ==")
    for _, r in a.iterrows():
        t = t100.loc[r.d_xo_section]
        log(f"{r.d_xo_section:52s} {int(r.n_yes):5d}/{int(r.n_measurable):5d} = {100 * r.rate:5.1f}% [{100 * r.ci95_lo:4.1f},{100 * r.ci95_hi:5.1f}]   top100: {int(t.n_yes):3d}/{int(t.n_measurable):3d} = {100 * t.rate:5.1f}%")
    return df


def tidy_lists(m):
    """token-level lists for findings"""
    cols = ["chain", "token", "symbol", "name", "rank", "rank_band", "market_cap_usd", "categories"]
    out = {}

    def pick(feat, extra=()):
        x = m[m[f"{feat}__status"] == "yes"][cols + [f"{feat}__value"] + list(extra)].copy()
        x = x.rename(columns={f"{feat}__value": "value"})
        return x.sort_values(["chain", "rank"])

    for feat in ["fresh_address_nonzero_balance", "recipient_not_credited", "false_on_failure", "balance_drift_7d", "paused_now", "permissioned_transfer",
                 "transfer_reverts_market_or_router", "transfer_reverts_paused", "transfer_reverts_fresh_contract", "hook_callbacks_observed", "erc777_static",
                 "callback_into_recipient", "eoa_vs_contract_differs", "same_block_cooldown", "max_uint_transfer_succeeds", "approve_max_capped", "tf_zero_reverts",
                 "forwarding_proxy", "external_balance_storage", "native_currency_repr", "metadata_nonstring", "decimals_high", "transfer_zero_returns_false",
                 "to_zero_burns", "self_transfer_changes_balance", "fee_on_transfer_observed", "uint96_limit", "balance_field_le128", "full_balance_anomaly", "max_tx_limit",
                 "same_block_cooldown", "gas_transfer", "permit_dai_style", "flash_mint_capability", "tf_owner_no_allowance", "ret_missing_transfer", "approve_race_guard"]:
        x = pick(feat)
        x.to_csv(OUT / f"list_{feat}.csv", index=False)
        out[feat] = x
    return out


def single_eoa_table(m):
    """top tokens (rank<=200 per chain, mcap-ranked) that are upgradeable by a single EOA with no timelock; EOA address + nonce from W3 evidence."""
    rows = []
    sub = m[(m.upgrade_single_eoa__status == "yes") & (m["rank"] <= 200) & (m.rank_band != "extras(non-mcap)")]
    ctl = {}
    for ch in CHAINS:
        for r in load_control(ch).values():
            ctl[(ch, r["token"].lower())] = r
    for _, r in sub.iterrows():
        c = ctl.get((r.chain, r.token))
        if not c:
            continue
        for p in c.get("upgrade_paths") or []:
            if p.get("assumed_or_unverified") or p["terminal_kind"] not in ("eoa", "eoa_7702") or "timelock" in p["kinds"] or p.get("access_manager_delay_seconds"):
                continue
            ta = p["terminal_address"]
            nonce = (c["controllers"].get(ta) or {}).get("details", {}).get("nonce")
            rec(rows, chain=r.chain, rank=r["rank"], symbol=r.symbol, token=r.token, market_cap_usd=r.market_cap_usd, categories=r.categories,
                proxy_type=c.get("proxy_type"), upgrade_path=" > ".join(p["addresses"]), terminal_kind=p["terminal_kind"], eoa_address=ta, eoa_nonce=nonce,
                control_block=c.get("block"))
            break
    df = pd.DataFrame(rows).sort_values(["chain", "rank"])
    df.to_csv(OUT / "single_eoa_upgrade_top200.csv", index=False)
    return df


def power_table(m):
    ids = ["pause_capability", "blocklist_any", "upgradeable", "upgrade_single_eoa", "upgrade_no_timelock", "owner_single_eoa", "mint_capability", "controlled",
           "sanction_block_ronin", "is_proxy", "paused_now"]
    rows = []
    for ch in ["all"] + CHAINS:
        for b in ["1-100", "101-500", "501-2000", "extras(non-mcap)"]:
            sub = m[(m.rank_band == b) & ((m.chain == ch) if ch != "all" else True)]
            if not len(sub):
                continue
            for i in ids:
                n, k = stat(sub, [i])
                lo, hi = wilson(k, n)
                rec(rows, chain=ch, band=b, feature=i, n_tokens=len(sub), n_measurable=n, n_yes=k, rate=(k / n) if n else None, ci95_lo=lo, ci95_hi=hi, ci_interpretation=CI_NOTE, scope="inventory_including_extras")
    pd.DataFrame(rows).to_csv(OUT / "power_concentration_by_band.csv", index=False, float_format="%.5f")


def category_vs_rest(m):
    ids = ["pause_capability", "blocklist_any", "sanction_block_ronin", "upgradeable", "upgrade_single_eoa", "upgrade_no_timelock", "owner_single_eoa", "mint_capability",
           "is_proxy", "share_accounting_ratio", "share_accessors", "rebasing_w1_any", "permissioned_transfer", "transfer_reverts_fresh_contract", "fee_on_transfer_observed",
           "allowance_max_decrements", "decimals_low", "ret_missing_transfer", "permit_any", "approve_zero_addr_reverts"]
    rows = []
    for c in CATS:
        isc = m.cat_list.apply(lambda x, c=c: c in x)
        for i in ids:
            for name, sub in (("in_category", m[isc]), ("rest", m[~isc])):
                n, k = stat(sub, [i])
                lo, hi = wilson(k, n)
                rec(rows, category=c, group=name, feature=i, n_tokens=len(sub), n_measurable=n, n_yes=k, rate=(k / n) if n else None, ci95_lo=lo, ci95_hi=hi, ci_interpretation=CI_NOTE, scope="inventory_including_extras")
    pd.DataFrame(rows).to_csv(OUT / "category_vs_rest.csv", index=False, float_format="%.5f")


def sanction_reverts(m):
    """decode (raw evidence) what a transfer to the Ronin-hack address reverts with, for every token where it reverts while a normal transfer works"""
    rows = []
    sub = m[m.sanction_block_ronin__status == "yes"]
    for _, r in sub.iterrows():
        try:
            raw = R.load_raw(r.chain, r.token)
            sc, cid = R.scripts(raw)
            s = R.S(sc)
            a = s["sanctioned_1", "tr"]; t0 = s["sanctioned_0", "tr"]
            rows.append({"chain": r.chain, "token": r.token, "symbol": r.symbol, "name": r["name"], "rank": r["rank"], "categories": r.categories,
                         "revert_ronin": R.revert_text(a), "revert_name": FT.SEL_NAMES.get("0x" + R.revert_text(a), ""), "tornado_router": "reverts:" + R.revert_text(t0) if not t0[0] else "ok", "evidence": f"data/probe/{r.chain}/raw/{r.token}.json.gz cid{cid} script=sanctioned_1"})
        except Exception as e:  # noqa: BLE001
            rows.append({"chain": r.chain, "token": r.token, "symbol": r.symbol, "revert_ronin": f"decode_error:{e}"})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "sanction_reverts.csv", index=False)
    return df


def main():
    m = load()
    log(f"matrix rows {len(m)}; probe_status {dict(m.probe_status.value_counts())}")
    dxo_table(m)
    lists = tidy_lists(m)
    se = single_eoa_table(m)
    power_table(m)
    category_vs_rest(m)
    sr = sanction_reverts(m)
    log("\n== sanction-address reverts by revert text/selector ==")
    sr["fam"] = sr.revert_ronin.str.replace(r"0x[0-9a-fA-F]{40}", "0xADDR", regex=True)
    for k, v in sr.fam.value_counts().head(25).items():
        log(f"  {v:4d}  {k[:110]}")
    log(f"\nsingle-EOA upgradable among rank<=200 mcap-ranked: {len(se)} deployments; by chain {dict(se.chain.value_counts())}")
    for n, x in lists.items():
        log(f"list_{n}: {len(x)}")
    (OUT / "findings_log.txt").write_text("\n".join(LOG))


if __name__ == "__main__":
    main()
