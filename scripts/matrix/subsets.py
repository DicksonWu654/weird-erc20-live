#!/usr/bin/env python3
"""Exact Tier-1 subset definitions and address-restriction membership exports."""
import json
import pathlib
import pandas as pd
from prevalence import security_mask

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data/matrix"
RONIN = "0x098b716b8aaf21512996dc57eb0615e2383e2f96"
TORNADO = "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b"


def build_subsets(m):
    yes = lambda f: m[f + "__status"].eq("yes")
    ronin, tornado = yes("sanction_block_ronin"), yes("sanction_block_tornado")
    masks = {
        "inventory": (pd.Series(True, index=m.index), "all six-chain universe deployments, including convenience extras"),
        "market_cap": (m.source.eq("coingecko_mcap") & m.rank_band.ne("extras(non-mcap)"), "CoinGecko market-cap rows; main prevalence denominator before feature measurability"),
        "extras": (m.rank_band.eq("extras(non-mcap)"), "non-market-cap inclusion routes; separate descriptive convenience sample"),
        "top500_market_cap": (m.source.eq("coingecko_mcap") & m["rank"].between(1, 500), "per-chain market-cap ranks 1-500; cross-chain-test domain; OP has 198"),
        "top200_market_cap": (m.source.eq("coingecko_mcap") & m["rank"].between(1, 200), "top-200 candidate EOA table domain; OP has 198"),
        "ronin_rejecting": (ronin, "saved restriction subset = baseline funded clean-recipient transfer succeeds, tested Ronin-exploiter recipient transfer reverts; original W1 amount"),
        "tornado_rejecting": (tornado, "baseline funded clean-recipient transfer succeeds, tested formerly sanctioned Tornado router transfer reverts; original W1 amount"),
        "ronin_and_tornado_rejecting": (ronin & tornado, "intersection of the two original W1 positive differential recipient tests; subset eligible for C10 restriction retest"),
        "tornado_only_rejecting": (tornado & ~ronin, "Tornado-positive outside the Ronin-positive saved restriction subset; not included in C10 restriction retest"),
        "blocklist_capability": (yes("blocklist_capability"), "strict static selector or answering blocklist view; capability, not observed transfer rejection"),
        "blocklist_any": (yes("blocklist_any"), "union of strict blocklist capability OR Ronin recipient revert OR Tornado recipient revert"),
        "original_security_tag": (m.categories.str.split(";").apply(lambda x: "tokenized_security" in x), "original category_hint plus build.SEC_RX name/symbol classification; matrix categories preserved"),
        "tokenized_securities": (security_mask(m), "original security category supplemented with saved Base B20 stock-policy 5 deployments (F1); not a legal classification; explicit basis per token in tokenized_securities.csv"),
        "b20": (m.token.str.startswith("0xb200") & m.chain.eq("base"), "Base 0xB200 deployments in this universe; separate from external B20 factory census and 120-address SDN mirror"),
    }
    for band in ("1-100", "101-500", "501-2000"):
        masks["rank_" + band] = (m.rank_band.eq(band), "market-cap band " + band + ("; Ethereum only" if band == "501-2000" else "; pooled over six chains"))
    for cat in ("stablecoin", "lst_lrt", "meme", "wrapped_bridged"):
        masks["category_" + cat] = (m.categories.str.split(";").apply(lambda x, cat=cat: cat in x), "saved universe category hint; can overlap other categories")
    masks["market_cap_ex_securities"] = (masks["market_cap"][0] & ~masks["tokenized_securities"][0], "main market-cap rows minus the saved tokenized_security category; same per-chain rank positions")
    defs, members = {}, []
    for name, (mask, definition) in masks.items():
        sub = m[mask]
        defs[name] = {"definition": definition, "n_tokens": len(sub), "by_chain": sub.groupby("chain").size().to_dict(),
                      "membership_file": "data/matrix/subset_membership.csv", "membership_key": name}
        members.extend({"subset": name, "chain": r.chain, "token": r.token} for r in sub[["chain", "token"]].itertuples())
    # Each feature has its own denominator, with an explicit membership file.
    for col in (c for c in m if c.endswith("__status")):
        feature = col[:-8]
        sub = m[m[col].isin(["yes", "no"])]
        defs["measurable:" + feature] = {"definition": f"{col} in [yes,no]; error/unprobeable excluded, never treated as negative",
                                          "n_tokens": len(sub), "membership": f"data/matrix/matrix.parquet column {col}"}
    c10_summary = ROOT / "data/verify/claims/summary.json"
    c10_fresh = []
    if c10_summary.exists():
        detail = json.loads(c10_summary.read_text())["main_verdicts"]["N009"]["detail"]
        c10_fresh, _ = json.JSONDecoder().raw_decode(detail.split("Fresh matched list=", 1)[1])
        pd.DataFrame(c10_fresh, columns=["chain", "token"]).to_csv(OUT / "c10_tornado_confirmed.csv", index=False)
        defs["c10_tornado_confirmed"] = {"definition": "C10 independent one-raw-unit transfer rejected with successful credited clean-recipient control, among the 526 original Ronin-positive candidates; later pins than W1",
                                          "n_tokens": len(c10_fresh), "membership_file": "data/matrix/c10_tornado_confirmed.csv",
                                          "evidence": "data/verify/claims/summary.json main_verdicts.N009; data/verify/claims/verdicts.csv N009/N010 and claim_evidence/"}
    targets = ROOT / "data/verify/claims/checks/targets.json"
    if targets.exists():
        eoa = [{"chain": ch, "token": r["token"], "symbol": r["symbol"]}
               for ch, rows in json.loads(targets.read_text()).items() for r in rows if "top200_eoa" in r["groups"]]
        pd.DataFrame(eoa).to_csv(OUT / "c10_eoa_candidates.csv", index=False)
        defs["c10_eoa_candidates"] = {"definition": "frozen pre-C19 top200_eoa candidate group supplied to C10; independent check verified 77 direct upgrades and left wrappers unresolved",
                                      "n_tokens": len(eoa), "membership_file": "data/matrix/c10_eoa_candidates.csv", "evidence": "data/verify/claims/checks/targets.json groups contains top200_eoa"}
    doc = {"unit": "deployment (chain, token), not unique economic asset or issuer", "subsets": defs,
           "tested_addresses": {"ronin_exploiter": RONIN, "tornado_router": TORNADO},
           "tornado_context": "Tornado Cash designations removed by OFAC on 2025-03-21; these tests do not establish current legal sanctions status or issuer intent",
           "c10_reconciliation": {"original_ronin_subset": int(ronin.sum()), "original_tornado_total": int(tornado.sum()),
                                  "original_overlap": int((ronin & tornado).sum()), "outside_c10_restriction_scope": int((tornado & ~ronin).sum()),
                                  "c10_independent_confirmed": len(c10_fresh),
                                  "c10_vs_w1_overlap_membership_equal": set(map(tuple, c10_fresh)) == set(zip(m.loc[ronin & tornado, "chain"], m.loc[ronin & tornado, "token"])),
                                  "note": "Independent verification used one raw unit and clean-recipient controls on the Ronin-positive subset; original matrix positives and independent confirmations are distinct. See data/verify/claims/verdicts.csv N009/N010; retain the 10 unresolved controls."},
           "external_case_study_domains": {"c10_eoa_candidates": "frozen targets.json top200_eoa group; explicit list data/matrix/c10_eoa_candidates.csv; distinct from all classifier-assigned EOA-terminal rows",
                                            "sdn_mirror": "120-address saved third-party mirror, registry check at Base block 52936930; not token deployments and not a fresh legal list",
                                            "b20_scanner_examples": "5 selected cases NVDAc/OUSD/GOOGLc/MSFTc/cbHYPE; router swaps succeed in 2, unresolved in 3; not a probability sample"}}
    (OUT / "subsets.json").write_text(json.dumps(doc, indent=2) + "\n")
    pd.DataFrame(members).to_csv(OUT / "subset_membership.csv", index=False)
    cols = ["chain", "token", "symbol", "name", "rank", "rank_band", "probe_block", "probed_at", "sanction_block_ronin__status", "sanction_block_tornado__status", "sanction_block_tornado__ev"]
    for name, mask in [("tornado_rejecting", tornado), ("tornado_only_rejecting", tornado & ~ronin), ("ronin_and_tornado_rejecting", ronin & tornado)]:
        sub = m.loc[mask, cols].copy()
        sub["c10_restriction_scope_member"] = ronin[mask]
        sub["c10_note"] = ["eligible Ronin-positive subset; see C10 independent outcomes" if x else "outside C10 restriction scope; original W1 evidence only" for x in ronin[mask]]
        sub.to_csv(OUT / (name + ".csv"), index=False)


if __name__ == "__main__":
    build_subsets(pd.read_parquet(OUT / "matrix.parquet"))
