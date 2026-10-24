"""Merge candidates + on-chain verification into the deliverables (no network):
  data/universe/<chain>.csv, data/universe/universe.parquet, data/universe/exclusions.csv,
  data/universe/same_asset_groups.csv, data/universe/stats.json
Usage: finalize.py [chain ...]   (default: all chains that have candidates + verify files)
"""
import csv, json, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from common import *
import pandas as pd

HINTS = [  # (hint, CoinGecko category ids that imply it)
    ("stablecoin", {"stablecoins", "usd-stablecoin", "eur-stablecoin", "fiat-backed-stablecoins", "crypto-backed-stablecoins", "algorithmic-stablecoins", "yield-bearing-stablecoins", "gold-stablecoin"}),
    ("wrapped_bridged", {"wrapped-tokens", "bridged-tokens", "bridged-usdc", "bridged-usdt", "bridged-dai", "bridged-wbtc", "bridged-weth", "wormhole-bridged-tokens", "layerzero-bridged"}),
    ("lst_lrt", {"liquid-staking-tokens", "liquid-staking", "liquid-restaking-tokens", "restaking", "eth-liquid-staking"}),
    ("meme", {"meme-token", "meme"}),
    ("governance", {"governance"}),
    ("rwa", {"real-world-assets-rwa", "tokenized-gold"}),
]


def hint(cats, name, symbol):
    cs = set(filter(None, (cats or "").split(";")))
    out = [h for h, ids in HINTS if cs & ids]
    return ";".join(out)


def load_verify(chain):
    d = {}
    p = UNI / f"{chain}.verify.jsonl"
    if p.exists():
        for line in open(p):
            j = json.loads(line)
            d[j["address"].lower()] = j  # last record wins
    return d


def main(chains):
    lst = json.load(open(RAW / "coingecko" / "coins_list_include_platform.json"))
    cg_plat = {c["id"]: c.get("platforms") or {} for c in lst}
    plat2chain = {v["cg"]: k for k, v in CHAINS.items()}
    frames, excl_rows, stats = [], [], {}
    for chain in chains:
        cand = pd.read_csv(UNI / f"{chain}.candidates.csv", dtype=str, keep_default_na=False)
        ver = load_verify(chain)
        rows = []
        for r in cand.to_dict("records"):
            v = ver.get(r["address"].lower())
            r["candidate_rank"] = r.pop("rank")
            if v is None:
                r["verify_status"] = "not_verified"
            else:
                for k in ("verify_block", "verify_status", "has_code", "code_size", "code_keccak", "total_supply", "decimals", "supply_status",
                          "decimals_status", "symbol_onchain", "name_onchain", "symbol_encoding", "supports_721", "supports_1155", "raw", "raw_id_prefix", "msg_supply", "msg_decimals"):
                    r[k] = v.get(k)
            if r["verify_status"] in ("erc721", "erc1155"):
                excl_rows.append(dict(chain=chain, address=r["address"], cg_id=r["cg_id"], symbol=r["symbol"], name=r["name"],
                                      reason=f"not an ERC-20: supportsInterface says {r['verify_status'].upper()}", source="onchain:" + r.get("raw", "")))
                continue
            rows.append(r)
        df = pd.DataFrame(rows)
        df.insert(2, "rank", range(1, len(df) + 1))
        NATIVE_PSEUDO = {"0x0000000000000000000000000000000000001010", "0xdeaddeaddeaddeaddeaddeaddeaddeaddead0000"}
        df["flags"] = [";".join(filter(None, [f] + (["native_gas_token_pseudo_erc20"] if a.lower() in NATIVE_PSEUDO else [])))
                       for f, a in zip(df["flags"], df["address"])]
        df["same_asset_group"] = df["cg_id"].replace("", pd.NA)
        df["category_hint"] = [hint(c, n, s) for c, n, s in zip(df["categories"], df["name"], df["symbol"])]
        chains_with = []
        for cid in df["cg_id"]:
            pl = cg_plat.get(cid, {}) if cid else {}
            chains_with.append(";".join(sorted(plat2chain[p] for p, a in pl.items() if a and p in plat2chain)))
        df["chains_with_contract"] = chains_with
        sym_c = df["symbol"].fillna("").str.lower(); sym_o = df.get("symbol_onchain", pd.Series([""] * len(df))).fillna("").astype(str).str.lower()
        df["symbol_matches_onchain"] = [(a == b) if b else None for a, b in zip(sym_c, sym_o)]
        df.to_csv(UNI / f"{chain}.csv", index=False)
        frames.append(df)
        stats[chain] = dict(rows=len(df), by_source=df["source"].value_counts().to_dict(),
                            by_verify=df["verify_status"].value_counts().to_dict(),
                            candidates=len(cand), onchain_excluded=sum(1 for e in excl_rows if e["chain"] == chain))
    allf = pd.concat(frames, ignore_index=True)
    # same-asset groups across all six chains, whether or not the other chain's contract is inside that chain's universe
    inuni = {(r.chain, r.address.lower()) for r in allf.itertuples()}
    gids = sorted(set(allf["cg_id"]) - {""})
    ga = []
    for g in gids:
        for p, a in cg_plat.get(g, {}).items():
            if p in plat2chain and a:
                ga.append(dict(same_asset_group=g, chain=plat2chain[p], address=a, in_universe=(plat2chain[p], a.lower()) in inuni))
    pd.DataFrame(ga).to_csv(UNI / "same_asset_groups.csv", index=False)
    stats["same_asset_groups_multichain"] = int(pd.DataFrame(ga).groupby("same_asset_group")["chain"].nunique().ge(2).sum()) if ga else 0
    # exclusions: source-level + on-chain
    ex = []
    for chain in chains:
        p = UNI / f"{chain}.exclusions_sources.csv"
        if p.exists():
            ex.append(pd.read_csv(p, dtype=str, keep_default_na=False))
    ex.append(pd.DataFrame(excl_rows))
    exdf = pd.concat(ex, ignore_index=True)
    exdf.to_csv(UNI / "exclusions.csv", index=False)
    stats["exclusions_by_reason"] = exdf.groupby(["chain", "reason"]).size().reset_index(name="n").to_dict("records")
    for c in allf.columns:
        if allf[c].dtype == object: allf[c] = allf[c].astype("string")
    allf.to_parquet(UNI / "universe.parquet", index=False)
    json.dump(stats, open(UNI / "stats.json", "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in stats.items() if k != "exclusions_by_reason"}, indent=1, default=str))


if __name__ == "__main__":
    chs = sys.argv[1:] or [c for c in CHAINS if (UNI / f"{c}.candidates.csv").exists()]
    main(chs)
