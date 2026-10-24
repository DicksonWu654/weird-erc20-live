#!/usr/bin/env python3
"""Read-only historical WETH/quote reserve ratios for non-native V2 pools."""
import hashlib
import json
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/tier2/quote-prices"
RPC = "https://eth.drpc.org"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pools = pd.read_parquet(ROOT / "data/census/dex_pools.parquet")
    pools = pools[pools.dex == "uniswap_v2"].copy()
    pools["t0"] = pools.token0.str.lower()
    pools["t1"] = pools.token1.str.lower()
    refs = pools[(pools.t0 == WETH) | (pools.t1 == WETH)].copy()
    refs["other"] = refs.apply(lambda r: r.t1 if r.t0 == WETH else r.t0, axis=1)
    refs = refs.sort_values("block").drop_duplicates("other").set_index("other")
    tasks = {}
    for line in open(ROOT / "data/tier2/sim-run/results.jsonl"):
        r = json.loads(line)
        pi = r.get("pool_info") or {}
        q = (pi.get("quote") or "").lower()
        if pi.get("reserve_quote") is not None and q not in (WETH, "0x" + "0" * 40):
            tasks[(q, r["block"])] = True
    sess = requests.Session()
    last = 0.0
    for i, (quote, block) in enumerate(sorted(tasks)):
        dest = OUT / (quote[2:] + "_" + str(block) + ".json")
        if dest.exists():
            old = json.loads(dest.read_text())
            if "result" in old.get("response", {}):
                continue
            dest = OUT / (quote[2:] + "_" + str(block) + "_retry.json")
            if dest.exists():
                continue
        if quote not in refs.index or int(refs.loc[quote, "block"]) > block:
            dest.write_text(json.dumps({"quote": quote, "block": block, "status": "no_reference_pool"}))
            continue
        ref = refs.loc[quote]
        req = {"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params":
               [{"to": ref.pool_address, "data": "0x0902f1ac"}, hex(block)]}
        for attempt in range(5):
            # V1 reserve audit uses this endpoint concurrently at <=3.125/s.
            wait = last + 0.85 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            try:
                resp = sess.post(RPC, json=req, timeout=30)
                body = resp.content
                if resp.status_code == 429:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                dest.write_text(json.dumps({"quote": quote, "block": block, "reference_pool": ref.pool_address,
                     "reference_token0": ref.token0, "reference_token1": ref.token1,
                     "request": {"url": RPC, "body": req}, "http_status": resp.status_code,
                     "response": resp.json(), "response_body": body.decode(errors="replace"),
                     "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "sha256": hashlib.sha256(body).hexdigest()}))
                break
            except (requests.RequestException, ValueError) as exc:
                if attempt == 4:
                    dest.write_text(json.dumps({"quote": quote, "block": block, "status": "transport_error", "error": repr(exc)}))
                time.sleep(min(2 ** attempt, 30))
        if (i + 1) % 50 == 0:
            print(f"quote price {i+1}/{len(tasks)}", flush=True)
    print(f"quote prices done {len(tasks)}", flush=True)


if __name__ == "__main__":
    main()
