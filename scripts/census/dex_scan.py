"""Complete enumeration of DEX pool-creation events via eth_getLogs (adaptive range splitting).

Sources (factory/manager address, event):
  uniswap_v1   0xc0a47dFe034B400B47bDaD5FecDa2621de6c4d95  NewExchange(address indexed token, address indexed exchange)
  uniswap_v2   0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f  PairCreated(address indexed token0, address indexed token1, address pair, uint256)
  sushiswap_v2 0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac  PairCreated (same ABI)
  uniswap_v3   0x1F98431c8aD98523631AE4a59f267346ea31F984  PoolCreated(token0 idx, token1 idx, fee idx, int24 tickSpacing, address pool)
  uniswap_v4   0x000000000004444c5dC75cB358380D2e3dE08A90  Initialize(bytes32 indexed id, currency0 idx, currency1 idx, fee, tickSpacing, hooks, sqrtPriceX96, tick)

Endpoint: Tenderly public gateway (accepts any block range, <=20000 results/query). Raw logs responses are stored
(gz jsonl, full) under data/raw/census/dexlogs_<dex>.jsonl.gz; parsed rows -> data/census/cache/dex_pools_raw.parquet.
Usage: .venv/bin/python scripts/census/dex_scan.py [dex ...]
"""
import json, pathlib, sys, time
import pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from rpc import RPC, ROOT

TENDERLY = "https://gateway.tenderly.co/public/mainnet"
DEX = {
    "uniswap_v1": dict(addr="0xc0a47dFe034B400B47bDaD5FecDa2621de6c4d95", topic="0x9d42cb017eb05bd8944ab536a8b35bc68085931dd5f4356489801453923953f9", start=6627917),
    "uniswap_v2": dict(addr="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f", topic="0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9", start=10000835),
    "sushiswap_v2": dict(addr="0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac", topic="0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9", start=10794229),
    "uniswap_v3": dict(addr="0x1F98431c8aD98523631AE4a59f267346ea31F984", topic="0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118", start=12369621),
    "uniswap_v4": dict(addr="0x000000000004444c5dC75cB358380D2e3dE08A90", topic="0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438", start=21688329),
}


def a(topic):  # indexed address topic -> address
    return "0x" + topic[-40:]


def word(data, i):
    return data[2 + 64 * i: 2 + 64 * (i + 1)]


def sint(hexword, bits):
    v = int(hexword, 16)
    return v - (1 << 256) if v >> 255 else v


def parse(dex, lg):
    base = dict(dex=dex, factory=DEX[dex]["addr"].lower(), block=int(lg["blockNumber"], 16), tx_hash=lg["transactionHash"],
                log_index=int(lg["logIndex"], 16))
    t, d = lg["topics"], lg["data"]
    if dex == "uniswap_v1":
        return {**base, "pool": a(t[2]), "token0": a(t[1]), "token1": "0x" + "0" * 40, "fee": None, "tick_spacing": None, "hooks": None, "sqrt_price_x96": None}
    if dex in ("uniswap_v2", "sushiswap_v2"):
        return {**base, "pool": "0x" + word(d, 0)[-40:], "token0": a(t[1]), "token1": a(t[2]), "fee": 3000, "tick_spacing": None, "hooks": None, "sqrt_price_x96": None}
    if dex == "uniswap_v3":
        return {**base, "pool": "0x" + word(d, 1)[-40:], "token0": a(t[1]), "token1": a(t[2]), "fee": int(t[3], 16),
                "tick_spacing": sint(word(d, 0), 24), "hooks": None, "sqrt_price_x96": None}
    if dex == "uniswap_v4":
        return {**base, "pool": t[1], "token0": a(t[2]), "token1": a(t[3]), "fee": int(word(d, 0), 16),
                "tick_spacing": sint(word(d, 1), 24), "hooks": "0x" + word(d, 2)[-40:], "sqrt_price_x96": str(int(word(d, 3), 16))}


def scan(dex, head, rpc):
    cfg = DEX[dex]
    rows, stack, calls = [], [(cfg["start"], head)], 0
    while stack:
        lo, hi = stack.pop()
        try:
            res = rpc.call("eth_getLogs", [{"fromBlock": hex(lo), "toBlock": hex(hi), "address": cfg["addr"], "topics": [cfg["topic"]]}], store="full")
        except RuntimeError as e:
            msg = str(e)
            if hi > lo and any(k in msg.lower() for k in ("more than", "exceed", "too many", "too large", "limit")):
                mid = (lo + hi) // 2
                stack += [(mid + 1, hi), (lo, mid)]
                continue
            raise
        calls += 1
        if len(res) >= 19990:  # suspicious cap: split to be safe
            mid = (lo + hi) // 2
            stack += [(mid + 1, hi), (lo, mid)]
            continue
        rows += [parse(dex, lg) for lg in res if not lg.get("removed")]
        if calls % 20 == 0:
            print(dex, "calls", calls, "rows", len(rows), "at", lo, flush=True)
    return rows


if __name__ == "__main__":
    names = sys.argv[1:] or list(DEX)
    head_rpc = RPC(stream="dex_head", rps=2)
    head = int(head_rpc.call("eth_blockNumber", []), 16) - 64  # stay clear of reorg depth
    out = ROOT / "data/census/cache"; out.mkdir(parents=True, exist_ok=True)
    for dex in names:
        rpc = RPC(url=TENDERLY, rps=3, stream=f"dexlogs_{dex}", timeout=180)
        t = time.time()
        rows = scan(dex, head, rpc)
        df = pd.DataFrame(rows).drop_duplicates(["tx_hash", "log_index"]).sort_values(["block", "log_index"])
        df.to_parquet(out / f"dex_pools_raw_{dex}.parquet")
        rpc.close()
        print(dex, "DONE", len(df), "pools, head", head, f"{time.time()-t:.0f}s", flush=True)
