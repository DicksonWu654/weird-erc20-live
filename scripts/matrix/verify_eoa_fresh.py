#!/usr/bin/env python3
"""Fresh, independent re-verification of the 'upgradeable by a single EOA' headline (A1). Does NOT reuse W3 evidence:
for each deployment in data/matrix/findings/single_eoa_upgrade_top200.csv it re-queries the chain *now* (latest block):
  1. eth_getCode(eoa)           -> must be empty (or an EIP-7702 delegation prefix 0xef0100)
  2. eth_getTransactionCount    -> nonce (informational)
  3. the proxy admin pointer    -> EIP-1967 admin slot / zos admin slot at the token; must equal the first hop of W3's path
Polite client: publicnode endpoints, JSON-RPC batches of <=10, <=4 req/s. Raw request/response pairs saved to
data/matrix/findings/raw_fresh_eoa_verify.json; summary -> data/matrix/findings/single_eoa_fresh_verification.csv
"""
import json
import pathlib
import time

import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "matrix" / "findings"
RPC = {"ethereum": "https://ethereum-rpc.publicnode.com", "base": "https://base-rpc.publicnode.com", "arbitrum": "https://arbitrum-one-rpc.publicnode.com",
       "bsc": "https://bsc-rpc.publicnode.com", "polygon": "https://polygon-bor-rpc.publicnode.com", "optimism": "https://optimism-rpc.publicnode.com"}
SLOT_ADMIN_1967 = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
SLOT_BEACON = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
SLOT_ADMIN_ZOS = "0x10d6a54a4754c8869d6886b5f5d7fbfa5b4522237ea5c60d11bc4e7a1ff9390b"
LAST = [0.0]


def post(url, batch):
    dt = time.time() - LAST[0]
    if dt < 0.3:
        time.sleep(0.3 - dt)
    for attempt in range(4):
        try:
            r = requests.post(url, json=batch, timeout=40)
            LAST[0] = time.time()
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            return r.json(), r.status_code
        except Exception as e:  # noqa: BLE001
            time.sleep(2)
    return None, 0


def main():
    df = pd.read_csv(OUT / "single_eoa_upgrade_top200.csv")
    raw, rows = [], []
    for ch, g in df.groupby("chain"):
        g = g.reset_index(drop=True)
        items, by = [], {}
        for i, r in g.iterrows():
            eoa = r.eoa_address
            first_hop = r.upgrade_path.split(" > ")[0]
            slot = SLOT_ADMIN_ZOS if r.proxy_type == "zos_legacy" else SLOT_ADMIN_1967
            items += [("code", i, {"method": "eth_getCode", "params": [eoa, "latest"]}),
                      ("nonce", i, {"method": "eth_getTransactionCount", "params": [eoa, "latest"]}),
                      ("admin", i, {"method": "eth_getStorageAt", "params": [r.token, slot, "latest"]}),
                      ("owner", i, {"method": "eth_call", "params": [{"to": r.token, "data": "0x8da5cb5b"}, "latest"]}),
                      ("beacon", i, {"method": "eth_getStorageAt", "params": [r.token, SLOT_BEACON, "latest"]})]
        for k in range(0, len(items), 10):
            chunk = items[k:k + 10]
            batch = [{"jsonrpc": "2.0", "id": j, **c[2]} for j, c in enumerate(chunk)]
            resp, st = post(RPC[ch], batch)
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            raw.append({"chain": ch, "url": RPC[ch], "retrieved_at": ts, "http_status": st, "request": batch, "response": resp})
            res = {it["id"]: it for it in (resp or []) if isinstance(it, dict)}
            for j, c in enumerate(chunk):
                by[(c[1], c[0])] = (res.get(j) or {}).get("result")
        bq = [(i, "0x" + by[(i, "beacon")][-40:]) for i in g.index if isinstance(by.get((i, "beacon")), str) and int(by[(i, "beacon")], 16) > 0]
        for k in range(0, len(bq), 10):
            chunk = bq[k:k + 10]
            batch = [{"jsonrpc": "2.0", "id": j, "method": "eth_call", "params": [{"to": b, "data": "0x8da5cb5b"}, "latest"]} for j, (i, b) in enumerate(chunk)]
            resp, st = post(RPC[ch], batch)
            raw.append({"chain": ch, "url": RPC[ch], "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "http_status": st, "request": batch, "response": resp})
            res = {it["id"]: it for it in (resp or []) if isinstance(it, dict)}
            for j, (i, b) in enumerate(chunk):
                by[(i, "beacon_owner")] = (res.get(j) or {}).get("result")
        for i, r in g.iterrows():
            code, nonce, adm = by.get((i, "code")), by.get((i, "nonce")), by.get((i, "admin"))
            own, bown = by.get((i, "owner")), by.get((i, "beacon_owner"))
            _a = lambda x: ("0x" + x[-40:]).lower() if isinstance(x, str) and len(x) >= 42 else None
            first_hop = r.upgrade_path.split(" > ")[0].lower()
            adm_addr = ("0x" + adm[-40:]).lower() if isinstance(adm, str) and len(adm) >= 42 else None
            rows.append({"chain": ch, "rank": r["rank"], "symbol": r.symbol, "token": r.token, "proxy_type": r.proxy_type, "w3_eoa": r.eoa_address,
                         "path": r.upgrade_path, "fresh_code": (code[:12] + "…") if isinstance(code, str) and len(code) > 12 else code,
                         "fresh_is_eoa_or_7702": isinstance(code, str) and (code == "0x" or code.startswith("0xef0100")),
                         "fresh_nonce": int(nonce, 16) if isinstance(nonce, str) else None, "w3_nonce": r.eoa_nonce,
                         "admin_slot_value": adm_addr, "admin_slot_matches_first_hop": (adm_addr == first_hop) if adm_addr else None,
                         "token_owner": _a(own), "beacon_owner": _a(bown),
                         "link_reverified": first_hop in (adm_addr, _a(own), _a(bown))})
    out = pd.DataFrame(rows).sort_values(["chain", "rank"])
    out.to_csv(OUT / "single_eoa_fresh_verification.csv", index=False)
    json.dump(raw, open(OUT / "raw_fresh_eoa_verify.json", "w"))
    n = len(out)
    print(f"{n} deployments; terminal has no code (EOA/7702): {int(out.fresh_is_eoa_or_7702.sum())}; "
          f"first hop of W3's path re-read now from admin slot / token.owner() / beacon.owner(): {int(out.link_reverified.sum())} of {n}")


if __name__ == "__main__":
    main()
