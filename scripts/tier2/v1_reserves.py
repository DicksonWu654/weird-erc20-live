#!/usr/bin/env python3
"""Resume a read-only historical reserve audit of V1 buy failures."""
import gzip
import hashlib
import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/tier2/v1-reserves"
RPC = "https://eth.drpc.org"


def main():
    OUT.mkdir(exist_ok=True, parents=True)
    done = {p.stem for p in OUT.glob("*.json")}
    rows = [json.loads(s) for s in open(ROOT / "data/tier2/sim-run/results.jsonl")]
    todo = [r for r in rows if r["dex"] == "uniswap_v1" and
            str(r.get("unprobeable") or "").startswith("buy_reverts")]
    session = requests.Session()
    last = 0.0
    for i, r in enumerate(todo):
        name = r["token"].lower()[2:] + "_" + str(r["block"])
        if name in done:
            continue
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [r["pool"], hex(r["block"])]},
            {"jsonrpc": "2.0", "id": 2, "method": "eth_call", "params":
             [{"to": r["token"], "data": "0x70a08231" + r["pool"].lower()[2:].zfill(64)}, hex(r["block"])]},
        ]
        envelopes = []
        for req in reqs:
            for attempt in range(5):
                wait = last + 0.32 - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                last = time.monotonic()
                try:
                    response = session.post(RPC, json=req, timeout=30)
                    body = response.content
                    item = {"url": RPC, "request": req, "http_status": response.status_code,
                            "response": response.json(), "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "response_sha256": hashlib.sha256(body).hexdigest()}
                    if response.status_code == 429:
                        time.sleep(min(2 ** attempt, 30))
                        continue
                    envelopes.append(item)
                    break
                except (requests.RequestException, ValueError) as exc:
                    if attempt == 4:
                        envelopes.append({"request": req, "transport_error": repr(exc),
                                          "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                    time.sleep(min(2 ** attempt, 30))
        out = {"token": r["token"], "pool": r["pool"], "block": r["block"],
               "sim_evidence": r.get("evidence"), "calls": envelopes}
        (OUT / (name + ".json")).write_text(json.dumps(out, separators=(",", ":")))
        if (i + 1) % 100 == 0:
            print(f"v1 audited {i+1}/{len(todo)}", flush=True)
    print(f"v1 done {len(todo)}", flush=True)


if __name__ == "__main__":
    main()
