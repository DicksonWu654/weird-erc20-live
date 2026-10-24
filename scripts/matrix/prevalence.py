#!/usr/bin/env python3
"""Tier-1 descriptive ranked-list rates and exploratory rank-matched Fisher tests.

Extras are a separate convenience sample. Wilson intervals describe this snapshot;
they are not design-based population intervals and omit classification error.
Holm families include every feature x comparison within each analysis set.
"""
import itertools
import functools
import json
import math
import pathlib

import pandas as pd
import features as FT

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data/matrix"
CHAINS = ["ethereum", "base", "arbitrum", "bsc", "polygon", "optimism"]
CI_NOTE = "descriptive Wilson 95%; ranked snapshot, not probability sample; excludes classification error"


def wilson(k, n):
    if not n:
        return None, None
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0, c - h), min(1, c + h)


def stat(df, ids):
    st = df[[i + "__status" for i in ids]]
    yes = st.eq("yes").any(axis=1)
    # A union is negative only if every component was measured and negative.
    measured = yes | st.eq("no").all(axis=1)
    return int(measured.sum()), int(yes.sum())


def measures():
    out = [(f["id"], [f["id"]], f["label"], f["kind"], "feature") for f in FT.F if f["kind"] != "meta"]
    out += [(name, ids, note, "mixed" if len(ids) > 1 else FT.FEATURES[ids[0]]["kind"], "d_xo_section") for name, ids, note in FT.DXO]
    return out


def scopes(m):
    for ch in ["all"] + CHAINS:
        sub = m if ch == "all" else m[m.chain == ch]
        for band in ["all_market_cap", "1-500", "1-100", "101-500", "501-2000"]:
            if band == "all_market_cap":
                g = sub
            elif band == "1-500":
                g = sub[sub["rank"].between(1, 500)]
            else:
                g = sub[sub.rank_band == band]
            yield ch, band, g


def rate_rows(ch, band, sub, analysis_set, scope):
    for feature, ids, definition, kind, measure_type in measures():
        n, k = stat(sub, ids)
        lo, hi = wilson(k, n)
        yield {"analysis_set": analysis_set, "scope": scope, "chain": ch, "rank_band": band,
               "feature": feature, "measure_type": measure_type, "features": ";".join(ids), "kind": kind,
               "definition": definition, "n_tokens": len(sub), "n_measurable": n, "n_yes": k,
               "n_no": n - k, "n_unmeasurable": len(sub) - n, "rate": k / n if n else None,
               "ci95_lo": lo, "ci95_hi": hi, "ci_interpretation": CI_NOTE}


def fisher_two_sided(a, b, c, d):
    """Fixed-margin Fisher exact p, two-sided probability ordering (as in R/scipy)."""
    n1, n2, k = a + b, c + d, a + c
    n = n1 + n2
    def lc(n_, k_):
        return math.lgamma(n_ + 1) - math.lgamma(k_ + 1) - math.lgamma(n_ - k_ + 1)
    def lp(x):
        return lc(n1, x) + lc(n2, k - x) - lc(n, k)
    observed = lp(a)
    return min(1.0, math.fsum(math.exp(lp(x)) for x in range(max(0, k - n2), min(n1, k) + 1)
                              if lp(x) <= observed + 1e-9))


def holm(pvals):
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    out, previous = [0.0] * len(pvals), 0.0
    for rank, i in enumerate(order):
        previous = max(previous, min(1.0, (len(pvals) - rank) * pvals[i]))
        out[i] = previous
    return out


def tests(m):
    rows = []
    comparisons = [(a, b) for a, b in itertools.combinations(CHAINS, 2)] + [("ethereum", "rest")]
    for aset, base in [("market_cap", m), ("market_cap_ex_securities", m[~security_mask(m)])]:
        base = base[base["rank"].between(1, 500)]
        chain_groups = {ch: base[base.chain == ch] for ch in CHAINS}
        chain_groups["rest"] = base[base.chain != "ethereum"]
        family = []
        for f in (f for f in FT.F if f["kind"] != "meta"):
            for ca, cb in comparisons:
                sa, sb = chain_groups[ca], chain_groups[cb]
                na, ka = stat(sa, [f["id"]]); nb, kb = stat(sb, [f["id"]])
                if not na or not nb:
                    continue
                p = fisher_two_sided(ka, na - ka, kb, nb - kb)
                family.append({"analysis_set": aset, "rank_band": "1-500", "source": "coingecko_mcap",
                               "feature": f["id"], "chain_a": ca, "chain_b": cb,
                               "n_tokens_a": len(sa), "n_measurable_a": na, "yes_a": ka, "no_a": na - ka,
                               "n_tokens_b": len(sb), "n_measurable_b": nb, "yes_b": kb, "no_b": nb - kb,
                               "rate_a": ka / na, "rate_b": kb / nb, "p_raw": p,
                               "test": "two-sided Fisher exact; exploratory deployment-level comparison",
                               "holm_family": aset + ":all_features_all_16_comparisons",
                               "limitations": "rank depth matched; unequal realized rank mix/issuers/chains remain; deployments not independent"})
        adjusted = holm([r["p_raw"] for r in family])
        for r, adj in zip(family, adjusted):
            r.update(p_holm=adj, holm_family_n=len(family), reject_holm_0_05=adj < 0.05)
        rows.extend(family)
    return pd.DataFrame(rows)


def security_mask(m):
    tagged = m.categories.fillna("").str.split(";").apply(lambda x: "tokenized_security" in x).astype(bool)
    if "chain" not in m or "token" not in m:
        return tagged
    return tagged | (m.chain.eq("base") & m.token.isin(b20_stock_tokens()))


@functools.lru_cache(maxsize=1)
def b20_stock_tokens():
    """The saved B20 stock policy 5 identifies stocks whose bare names omit 'stock'.

    F1 documents policy 5; derivatives cbZEC/cbHYPE and OUSD use other policies.
    This supplements, rather than silently rewrites, the original name-based tag.
    """
    p = ROOT / "data/control/base/control.jsonl"
    tokens = set()
    for r in map(json.loads, p.read_text().splitlines()):
        policies = (r.get("b20") or {}).get("policies") or {}
        if r["token"].startswith("0xb200") and all(policies.get(k) == 5 for k in ("TRANSFER_SENDER_POLICY", "TRANSFER_RECEIVER_POLICY", "TRANSFER_EXECUTOR_POLICY")):
            tokens.add(r["token"])
    return frozenset(tokens)


def issuer_of(r):
    """Name-derived issuer family, not an independently verified legal attribution."""
    name = r["name"].lower()
    if "ondo" in name:
        return "Ondo"
    if "xstock" in name:
        return "Backed/xStocks"
    if name.startswith("backed "):
        return "Backed/other"
    if "reality tokenized" in name:
        return "Reality (name-derived)"
    if "dinari" in name:
        return "Dinari (name-derived)"
    if r.chain == "base" and r.token.startswith("0xb200"):
        return "Coinbase B20"
    if "spiko" in name:
        return "Spiko"
    return "other_or_unattributed"


def build_tables(m):
    mcap = m[m.source.eq("coingecko_mcap") & m.rank_band.ne("extras(non-mcap)")]
    extras = m[~m.index.isin(mcap.index)]
    rows, ex = [], []
    for ch, band, sub in scopes(mcap):
        rows.extend(rate_rows(ch, band, sub, "market_cap", "main"))
        ex.extend(rate_rows(ch, band, sub[~security_mask(sub)], "market_cap_ex_securities", "sensitivity"))
    pd.DataFrame(rows).to_csv(OUT / "prevalence_by_chain_rank.csv", index=False, float_format="%.10g")
    pd.DataFrame(ex).to_csv(OUT / "prevalence_ex_securities.csv", index=False, float_format="%.10g")
    erows = []
    for ch in ["all"] + CHAINS:
        sub = extras if ch == "all" else extras[extras.chain == ch]
        erows.extend(rate_rows(ch, "extras(non-mcap)", sub, "extras", "convenience_sample_separate"))
    pd.DataFrame(erows).to_csv(OUT / "prevalence_extras.csv", index=False, float_format="%.10g")
    tests(mcap).to_csv(OUT / "cross_chain_tests.csv", index=False, float_format="%.12g")
    securities = m[security_mask(m)].copy()
    securities["issuer_family"] = securities.apply(issuer_of, axis=1)
    securities["exclusion_basis"] = ["saved B20 stock policy 5 (F1)" if r.chain == "base" and r.token in b20_stock_tokens() else "original tokenized_security category" for r in securities.itertuples()]
    securities[["chain", "token", "symbol", "name", "rank", "rank_band", "issuer_family", "exclusion_basis"]].to_csv(OUT / "tokenized_securities.csv", index=False)
    irows = []
    for scope, base in [("all_deployments", securities), ("market_cap", securities[securities.source.eq("coingecko_mcap")])]:
        for issuer, sub in base.groupby("issuer_family"):
            for ch in ["all"] + CHAINS:
                g = sub if ch == "all" else sub[sub.chain == ch]
                irows.append({"scope": scope, "issuer_family": issuer, "chain": ch, "n_tokens": len(g),
                              "upgradeable_yes": int(g.upgradeable__status.eq("yes").sum()),
                              "blocklist_any_yes": int(g.blocklist_any__status.eq("yes").sum()),
                              "share_capability_yes": int(g.share_accounting_capability__status.eq("yes").sum()),
                              "basis": "name-derived families; exclusion = original category plus saved B20 stock policy 5"})
    pd.DataFrame(irows).to_csv(OUT / "security_issuer_composition.csv", index=False)


if __name__ == "__main__":
    build_tables(pd.read_parquet(OUT / "matrix.parquet"))
