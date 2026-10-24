"""Merge per-DEX raw pool-creation scans into data/census/dex_pools.parquet (+ per-month pool counts) and run completeness checks.

  .venv/bin/python scripts/census/dex_build.py
Checks: uniswap_v2/sushiswap_v2/uniswap_v1 event counts vs factory counters (allPairsLength()/tokenCount()) at the scan head.
"""
import hashlib, json, pathlib, sys
import pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from rpc import RPC, ROOT

C, OUT = ROOT / "data/census/cache", ROOT / "data/census"
Z = "0x" + "0" * 40
QUOTES = [  # priority order
    ("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH"), (Z, "ETH"),
    ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC"), ("0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT"),
    ("0x6b175474e89094c44da98b954eedeac495271d0f", "DAI"), ("0xdc035d45d973e3ec169d2276ddab16f1e407384f", "USDS"),
    ("0x6c3ea9036406852006290770bedfcaba0e23a0e8", "PYUSD"), ("0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "WBTC"),
    ("0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf", "cBBTC"), ("0x4fabb145d64652a948d72533023f6e7a623c7c53", "BUSD"),
    ("0x853d955acef822db058eb8505911ed77f175b99e", "FRAX"), ("0x5f98805a4e8be255a32880fdec7f6728c6568ba0", "LUSD"),
    ("0x4c9edd5852cd905f086c759e8383e09bff1e68b3", "USDe"), ("0x1abaea1f7c830bd89acc67ec4af516284b1bc33c", "EURC"),
]
QRANK = {a: i for i, (a, _) in enumerate(QUOTES)}
QSYM = dict(QUOTES)


def quote_info(t0, t1):
    c = [t for t in (t0, t1) if t in QRANK]
    if not c:
        return None, None, None
    q = min(c, key=QRANK.get)
    return q, QSYM[q], (t1 if q == t0 else t0)


def main():
    parts, ev = [], {}
    for dex in ["uniswap_v1", "uniswap_v2", "sushiswap_v2", "uniswap_v3", "uniswap_v4"]:
        df = pd.read_parquet(C / f"dex_pools_raw_{dex}.parquet")
        raw = ROOT / f"data/raw/census/dexlogs_{dex}.jsonl.gz"
        ev[dex] = dict(raw_file=str(raw.relative_to(ROOT)), sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), bytes=raw.stat().st_size, n_events=len(df))
        parts.append(df)
    d = pd.concat(parts, ignore_index=True)
    d = d.rename(columns={"pool": "pool_address", "factory": "factory_or_manager"})
    qi = [quote_info(a, b) for a, b in zip(d.token0, d.token1)]
    d["quote_token"], d["quote_symbol"], d["base_token"] = zip(*qi)
    d["block"] = d["block"].astype("int64")  # = pool creation block
    bm = pd.read_csv(OUT / "blockmap.csv")
    d["month"] = pd.cut(d.block, bins=[bm.first_block.iloc[0] - 1] + list(bm.last_block), labels=bm.month, right=True).astype(str)
    d = d.sort_values(["block", "log_index"]).reset_index(drop=True)
    d.to_parquet(OUT / "dex_pools.parquet", index=False, compression="zstd", compression_level=19)
    (OUT / "evidence").mkdir(exist_ok=True)
    (OUT / "evidence/dex_raw_files.json").write_text(json.dumps(ev, indent=1))
    pm = d.pivot_table(index="month", columns="dex", values="block", aggfunc="count").fillna(0).astype(int)
    pm.to_csv(OUT / "dex_pool_counts_monthly.csv")
    print(d.groupby("dex").size(), "\nquote known share:\n", d.groupby("dex").quote_symbol.apply(lambda s: s.notna().mean()))
    # completeness vs on-chain counters (state at head; scan stopped 64 blocks earlier)
    rpc = RPC(stream="dex_counters", rps=2)
    chk = {}
    for dex, addr, sig in [("uniswap_v2", "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f", "0x574f2ba3"),
                           ("sushiswap_v2", "0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac", "0x574f2ba3"),
                           ("uniswap_v1", "0xc0a47dFe034B400B47bDaD5FecDa2621de6c4d95", "0x9f181b5e")]:
        r = rpc.call("eth_call", [{"to": addr, "data": sig}, "latest"])
        chk[dex] = dict(counter=int(r, 16), events=int((d.dex == dex).sum()))
    print("completeness vs counters:", chk)
    (OUT / "evidence").mkdir(exist_ok=True)
    (OUT / "evidence/dex_completeness_counters.json").write_text(json.dumps(chk, indent=1))
    rpc.close()


if __name__ == "__main__":
    main()
