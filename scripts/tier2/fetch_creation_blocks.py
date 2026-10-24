#!/usr/bin/env python3
"""Save archived pool-creation block timestamps for exact 30-day windows."""
import asyncio
import hashlib
import json
import time
from pathlib import Path

import aiohttp

from rug_second_pass import OUT, ROOT

DEST = OUT / "creation-blocks"
RPC = "https://eth.drpc.org"


async def main():
    DEST.mkdir(parents=True, exist_ok=True)
    blocks = set()
    # The selected V2/Sushi sample is fixed before Sync enrichment finishes.
    # Fetch its block headers independently so RPC and Etherscan evidence
    # collection can progress at their separate endpoint limits.
    for line in (OUT / "sample.jsonl").read_text().splitlines():
        sample = json.loads(line)
        if sample.get("dex") not in ("uniswap_v2", "sushiswap_v2", "sushiswap"):
            continue
        blocks.add(int(sample["created_block"]))
        head = int(sample["screen"]["head"])
        blocks.add(head - head % 2000)
    next_at = 0.
    n = 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
        for block in sorted(blocks):
            dest = DEST / f"{block}.json"
            if dest.exists() and json.loads(dest.read_text()).get("timestamp") is not None:
                continue
            payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber", "params": [hex(block), False]}
            attempts = []
            result = None
            for trial in range(5):
                wait = next_at - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                next_at = max(next_at, time.monotonic()) + .5
                try:
                    async with session.post(RPC, json=payload) as response:
                        body = await response.read()
                        parsed = json.loads(body)
                        item = {"request": {"url": RPC, "json": payload},
                                "http_status": response.status, "body": body.decode(errors="replace"),
                                "response": parsed, "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "sha256": hashlib.sha256(body).hexdigest()}
                        attempts.append(item)
                        result = parsed.get("result") or {}
                        if response.status == 200 and result.get("timestamp"):
                            break
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    attempts.append({"request": {"url": RPC, "json": payload},
                                     "error": repr(exc), "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                await asyncio.sleep(min(2**trial, 20))
            dest.write_text(json.dumps({"block": block, "timestamp": int(result["timestamp"], 16)
                                        if isinstance(result, dict) and result.get("timestamp") else None,
                                        "attempts": attempts}, separators=(",", ":")))
            n += 1
            if n % 50 == 0:
                print(f"creation blocks {n}/{len(blocks)}", flush=True)
    print(f"creation blocks done {n}/{len(blocks)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
