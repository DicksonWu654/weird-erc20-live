#!/usr/bin/env python3
"""Sample recently created Uniswap V2 / V3 WETH pools on Ethereum (for the latest-mode smoke test).

  recent_pairs.py --days 30 --n 24 --out data/sim-golden/recent_pairs.csv
Uses Etherscan v2 getLogs on the factories (drpc free plan caps eth_getLogs at ~100 blocks) over 2000-block windows
spread across the last N days (<= 1 req / 0.6 s; the key is shared with other workstreams).
"""
import argparse, csv, random, sys, time, os
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from abi import WETH
from rug import etherscan_key

URL = "https://eth.drpc.org"
V2F, V2T = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f", "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
V3F, V3T = "0x1F98431c8aD98523631AE4a59f267346ea31F984", "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"


def rpc(m, p):
    for i in range(5):
        r = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p}, timeout=60)
        j = r.json()
        if "error" in j and "rate" in str(j["error"]).lower():
            time.sleep(2 ** i)
            continue
        return j
    return j


def es_logs(addr, topic, a, b):
    key = etherscan_key()
    for i in range(6):
        r = requests.get("https://api.etherscan.io/v2/api", params=dict(chainid=1, module="logs", action="getLogs", address=addr, topic0=topic,
                                                                   fromBlock=a, toBlock=b, page=1, offset=1000, apikey=key), timeout=60).json()
        if isinstance(r.get("result"), list):
            return r["result"]
        if "rate" in str(r.get("result")).lower():
            time.sleep(1.5 * (i + 1))
            continue
        return []
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    head = int(rpc("eth_blockNumber", [])["result"], 16) - 10
    rnd = random.Random(a.seed)
    rows = []
    # windows spread over the period, newest first
    wins = [head - int(a.days * 7200 * f) for f in (0.02, 0.2, 0.4, 0.6, 0.8, 0.97)]
    for end in wins:
        end = min(end, head)
        for fac, topic, kind in ((V2F, V2T, "uniswap_v2"), (V3F, V3T, "uniswap_v3")):
            res = es_logs(fac, topic, end - 1999, end)
            time.sleep(0.6)
            for l in res:
                t0, t1 = "0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:]
                d = l["data"][2:]
                pool = "0x" + (d[24:64] if kind == "uniswap_v2" else d[64 + 24:128])
                if t0.lower() == WETH.lower():
                    tok = t1
                elif t1.lower() == WETH.lower():
                    tok = t0
                else:
                    continue
                rows.append({"token": tok, "pool": pool, "dex": kind, "created_block": int(l["blockNumber"], 16), "window_end": end})
    rnd.shuffle(rows)
    per = {}
    out = []
    for r in rows:  # balanced across windows & dex
        k = (r["window_end"], r["dex"])
        if per.get(k, 0) < max(1, a.n // 12):
            per[k] = per.get(k, 0) + 1
            out.append(r)
    out = out[:a.n]
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["token", "pool", "dex", "created_block"])
        w.writeheader()
        for r in sorted(out, key=lambda r: r["created_block"]):
            w.writerow({k: r[k] for k in w.fieldnames})
    print(f"{len(out)} pairs from {len(rows)} candidates -> {a.out}")


main()
