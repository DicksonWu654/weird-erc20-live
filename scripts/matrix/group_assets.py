#!/usr/bin/env python3
"""Annotate CG contract groups using saved names/ids, with separate bridge families.

No symbol-only identity claims. Generic CG groups have unresolved issuance status.
Issuer deployments here mean the explicit CG platform group for USDC/USDT;
bridged/pegged versions have separate ids and remain separate comparison families.
"""
import pathlib
import re
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = ROOT / "data/universe/same_asset_groups.csv"
BRIDGE = re.compile(r"bridg|binance[- ]peg|wormhole|axelar|anyswap|multichain|\.e(?:\b|$)|\((?:pos|arb1)\)", re.I)


def classify(group, name="", symbol=""):
    text = f"{group} {name} {symbol}"
    if "binance" in text.lower() and ("bridg" in text.lower() or "peg" in text.lower()):
        return "third_party_binance_peg", "Binance-Peg", "saved CoinGecko id/name explicitly identifies Binance peg"
    if BRIDGE.search(text):
        provider = next((n for n in ["wormhole", "axelar", "polygon", "optimism", "arbitrum", "base"] if n in text.lower()), "unspecified_bridge")
        return "third_party_bridge_or_peg", provider, "saved CoinGecko id/name explicitly identifies bridge/peg; issuer status not inferred from ticker"
    if group in ("usd-coin", "tether"):
        return "issuer_deployment", "Circle" if group == "usd-coin" else "Tether", "saved CoinGecko platform group; separate from bridge ids"
    return "unresolved", "", "CoinGecko identity only; issuance/bridge relationship not established"


def main():
    g = pd.read_csv(PATH).fillna("")
    if "cg_same_asset_group" not in g:
        g["cg_same_asset_group"] = g.same_asset_group
    u = pd.read_parquet(ROOT / "data/universe/universe.parquet").fillna("")
    by = {(r.chain, r.address.lower()): r for r in u.itertuples()}
    rows = []
    for r in g.to_dict("records"):
        cg = r["cg_same_asset_group"]
        token = by.get((r["chain"], r["address"].lower()))
        name = getattr(token, "name_onchain", "")
        sym = getattr(token, "symbol_onchain", "")
        relation, provider, basis = classify(cg, name, sym)
        r.update(same_asset_group=cg, asset_family=cg, deployment_relationship=relation, deployment_provider=provider,
                 grouping_basis=basis, grouping_evidence=getattr(token, "raw", "") or "data/raw: saved CoinGecko coins/list platform metadata")
        # USDC/USDT are economic exposures. Each third-party deployment's own CG
        # group remains distinct, even where the ticker matches issuer tokens.
        if cg == "usd-coin":
            r["asset_family"] = "USDC|Circle issuer"
        elif cg == "tether":
            r["asset_family"] = "USDT|Tether issuer"
        elif relation.startswith("third_party"):
            asset = "USDC" if re.search(r"usdc|usd-coin", cg, re.I) else "USDT" if re.search(r"usdt|tether", cg, re.I) else cg
            r["asset_family"] = f"{asset}|{provider}|{cg}"
        rows.append(r)
    pd.DataFrame(rows).to_csv(PATH, index=False)


if __name__ == "__main__":
    main()
