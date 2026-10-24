#!/usr/bin/env python3
"""Cross-chain comparison of 'the same asset' (A1 finding 1).

Families: explicit saved asset groups; issuer and third-party bridge/peg deployments
are separate families. Ticker equality alone is not evidence of common issuance.

    .venv/bin/python scripts/matrix/crosschain.py
Outputs: data/matrix/findings/crosschain_deployments.csv, crosschain_families.csv
"""
import json
import pathlib
import re
import sys

import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import features as FT  # noqa: E402

OUT = ROOT / "data" / "matrix" / "findings"
ALIAS = {"USD₮0": "USDT", "USDT0": "USDT", "USDBC": "USDC", "USDT.E": "USDT"}
VARIANT_RX = re.compile(r"\((pos|arb1|bridged)\)|bridged|binance-peg|wormhole|\.e$|\bpeg\b", re.I)
SKIP_GROUPS = {"sai"}
COMPARE = [f["id"] for f in FT.F if f["kind"] in ("observed", "capability", "control") and f["id"] not in ("gas_transfer", "upgrade_authority_class", "owner_path_class")]


def norm_sym(s):
    s = (s if isinstance(s, str) else "").upper().strip()
    s = re.sub(r"\s*\((POS|ARB1)\)$", "", s)
    s = re.sub(r"\.E$", "", s)
    return ALIAS.get(s, s)


def first_word(n):
    n = n if isinstance(n, str) else ""
    w = re.sub(r"[^a-z0-9]", "", (n.lower().split() or [""])[0])
    return w


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    m = pd.read_parquet(ROOT / "data" / "matrix" / "matrix.parquet")
    m["nsym"] = m.symbol.map(norm_sym)
    m["fw"] = m.name.map(first_word)
    ok = m[(m["rank"] <= 700) & ~m.same_asset_group.isin(SKIP_GROUPS) & m.nsym.ne("")].copy()
    def family(r):
        if r.asset_family:
            return r.asset_family
        # Unknown deployments never enter the issuer family by ticker alone.
        return f"{r.nsym}|unresolved|{r.chain}:{r.token}"
    ok["family"] = ok.apply(family, axis=1)
    fam = ok.groupby("family").agg(n_chains=("chain", "nunique"), n_dep=("token", "count"), mc=("market_cap_usd", "max"))
    # Retain explicitly named peg families even when seen on one chain, so they
    # are visible beside the issuer families rather than silently discarded.
    variant_families = set(ok.loc[ok.deployment_relationship.str.startswith("third_party"), "family"])
    fam = fam[(fam.n_chains >= 2) | fam.index.isin(variant_families)].sort_values("mc", ascending=False)
    top = fam.head(40).index.tolist()

    dep_rows, fam_rows = [], []
    for f in top:
        sub = ok[ok.family == f].sort_values(["chain", "rank"])
        # keep the highest-ranked deployment per chain+variant: all deployments are kept, flagged
        for _, r in sub.iterrows():
            d = {"family": f, "chain": r.chain, "token": r.token, "symbol": r.symbol, "name": r["name"], "rank": r["rank"], "market_cap_usd": r.market_cap_usd,
                 "cg_id": r.same_asset_group, "variant": r.deployment_relationship,
                 "deployment_relationship": r.deployment_relationship,
                 "decimals": r["decimals__value"]}
            for fid in COMPARE:
                d[fid] = {"yes": "Y", "no": "n"}.get(r[f"{fid}__status"], "?")
                d[fid + "__value"] = r[f"{fid}__value"]
            dep_rows.append(d)
        dd = pd.DataFrame([x for x in dep_rows if x["family"] == f])
        diffs = {}
        for fid in COMPARE:
            vals = dd[fid]
            known = vals[vals.isin(["Y", "n"])]
            if known.nunique() > 1:
                diffs[fid] = {f"{c}:{t[:8]}": v for c, t, v in zip(dd.chain, dd.token, vals)}
        dec = sorted(set(dd.decimals.astype(str)))
        fam_rows.append({"family": f, "n_deployments": len(dd), "n_chains": dd.chain.nunique(), "chains": ",".join(sorted(set(dd.chain))), "max_market_cap_usd": fam.loc[f, "mc"],
                         "comparison_scope": "multi_chain_family" if dd.chain.nunique() >= 2 else "third_party_single_chain_reference",
                         "decimals_values": "/".join(dec), "n_features_differ": len(diffs), "features_differ": ";".join(diffs), "detail_json": json.dumps(diffs)})
    pd.DataFrame(dep_rows).to_csv(OUT / "crosschain_deployments.csv", index=False)
    fdf = pd.DataFrame(fam_rows)
    fdf.to_csv(OUT / "crosschain_families.csv", index=False)
    print(fdf[["family", "n_deployments", "n_chains", "decimals_values", "n_features_differ", "features_differ"]].to_string())


if __name__ == "__main__":
    main()
