"""Build the 'top tokens across our chains' half of the validation subset (data/validate/subset_top.jsonl).

Source (no new network traffic): W2's cached CoinGecko pages (data/raw/coingecko/markets_p*.json = market-cap
ranked coins; coins_list_include_platform.json = contract address per platform). If W2 has published
data/universe/*.csv (columns chain + address/token [+ rank]) it is used instead.
For each chain we take the top --pool coins (by global CoinGecko market-cap rank) that have a contract on that
chain and draw a seeded random sample of the per-chain quota.
"""
import argparse, csv, glob, json, random
from common import ROOT, VAL

PLAT = {"ethereum": 1, "binance-smart-chain": 56, "base": 8453, "arbitrum-one": 42161, "polygon-pos": 137,
        "optimistic-ethereum": 10}
QUOTA = {1: 70, 56: 35, 8453: 30, 42161: 25, 137: 20, 10: 20}


def from_universe():
    rows = {}
    for f in glob.glob(str(ROOT / "data" / "universe" / "*.csv")):
        with open(f) as fh:
            rd = csv.DictReader(fh)
            cols = rd.fieldnames or []
            ccol = next((c for c in cols if c.lower() in ("chain", "chain_id", "chainid")), None)
            acol = next((c for c in cols if c.lower() in ("address", "token", "token_address")), None)
            rcol = next((c for c in cols if "rank" in c.lower()), None)
            if not (ccol and acol):
                continue
            for r in rd:
                try:
                    cid = int(r[ccol]) if r[ccol].isdigit() else {"ethereum": 1, "bsc": 56, "base": 8453, "arbitrum": 42161,
                                                                  "polygon": 137, "optimism": 10}[r[ccol].lower()]
                except KeyError:
                    continue
                rk = float(r[rcol]) if rcol and r.get(rcol) else 1e9
                rows.setdefault(cid, []).append((rk, r[acol].lower(), r.get("symbol", ""), f"universe:{f.split('/')[-1]}"))
    return rows


def from_coingecko():
    cg = ROOT / "data" / "raw" / "coingecko"
    plats = {c["id"]: c["platforms"] for c in json.load(open(cg / "coins_list_include_platform.json"))}
    rows = {}
    for f in sorted(glob.glob(str(cg / "markets_p*.json"))):
        for m in json.load(open(f)):
            rank = m.get("market_cap_rank")
            if not rank:
                continue
            for plat, cid in PLAT.items():
                a = (plats.get(m["id"]) or {}).get(plat)
                if a and a.startswith("0x") and len(a) == 42:
                    rows.setdefault(cid, []).append((rank, a.lower(), m["symbol"], f"coingecko:{m['id']}"))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261021)
    a = ap.parse_args()
    rows = from_universe() or from_coingecko()
    rng = random.Random(a.seed)
    out = []
    for cid, q in QUOTA.items():
        r = sorted(set(rows.get(cid, [])))[:a.pool]
        pick = rng.sample(r, min(q, len(r)))
        print(cid, "pool", len(r), "picked", len(pick))
        for rank, addr, sym, src in sorted(pick):
            out.append({"chain": cid, "token": addr, "source": "top", "stratum": "top", "symbol": sym,
                        "rank_global": rank, "rank_source": src, "seed": a.seed})
    VAL.mkdir(parents=True, exist_ok=True)
    with open(VAL / "subset_top.jsonl", "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    print("wrote", len(out))


if __name__ == "__main__":
    main()
