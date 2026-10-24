#!/usr/bin/env python3
"""Save complete V2 Sync histories for plausible 30-day pull candidates.

The earlier screen samples only a few reserve blocks.  A Sync-event peak is
needed to confirm that a withdrawal reached 80% of the actual 30-day peak.
"""
import asyncio
import fcntl
import json
import os
import time
from pathlib import Path

from eth_utils import keccak

from rug_second_pass import Client, ROOT, OUT
import sys
sys.path.insert(0, str(ROOT / "scripts/sim"))
import rug as RG

TOPIC = "0x" + keccak(text="Sync(uint112,uint112)").hex()
DEST = OUT / "sync-peaks"


def first_mint_time(record):
    times = []
    for env in record.get("raw", []):
        if not str(env.get("label", "")).startswith("es_mint:"):
            continue
        for event in (env.get("response") or {}).get("result", []):
            if isinstance(event, dict) and event.get("timeStamp"):
                times.append(int(event["timeStamp"], 16))
    return min(times) if times else None


async def main():
    DEST.mkdir(parents=True, exist_ok=True)
    lock = (OUT / "sync-peaks.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    key = RG.etherscan_key()
    if not key:
        raise RuntimeError("ETHERSCAN_API_KEY unavailable")
    concurrent = os.environ.get("TIER2_CONCURRENT_SYNC") == "1"
    manifest_n = sum(1 for _ in (OUT / "sample.jsonl").open()) if concurrent else 0
    client = Client(key, interval=1.0 if concurrent else 0.5)
    count = 0
    processed = 0
    try:
        for src in sorted(OUT.glob("*.json")):
            processed += 1
            if concurrent and processed % 25 == 0 and sum(1 for _ in OUT.glob("*.json")) >= manifest_n:
                print("rug pass complete; handing remaining Sync work to final runner", flush=True)
                break
            if concurrent and time.time() - src.stat().st_mtime < 5:
                continue
            r = json.loads(src.read_text())
            sample = r.get("sample") or {}
            if sample.get("dex") not in ("uniswap_v2", "sushiswap_v2", "sushiswap") or r.get("error"):
                continue
            detail = r.get("detail") or {}
            screen = sample.get("screen") or {}
            if float(detail.get("quote_burned_total") or 0) <= 0:
                continue
            out = DEST / src.name
            if out.exists():
                continue
            start = first_mint_time(r)
            if start is None:
                out.write_text(json.dumps({"source": str(src.relative_to(ROOT)), "error": "no_mint_timestamp"}))
                continue
            raw = []
            events = []
            truncated = True
            for page in range(1, 11):
                env = await client.get(module="logs", action="getLogs", address=sample["pool"],
                                       fromBlock=int(sample["created_block"]),
                                       toBlock=min(int(sample["created_block"]) + 270000, int(screen["head"])),
                                       topic0=TOPIC, page=page, offset=1000)
                raw.append(env)
                result = (env.get("response") or {}).get("result")
                if not isinstance(result, list):
                    truncated = False if result == "No records found" else True
                    break
                events.extend(result)
                if len(result) < 1000:
                    truncated = False
                    break
            quote0 = bool((sample.get("pool_info") or {}).get("quote_is_token0"))
            peak = 0
            peak_block = None
            peak_timestamp = None
            eligible_events = 0
            for ev in events:
                t = int(ev.get("timeStamp") or "0x0", 16)
                if t < start or t > start + 30*86400:
                    continue
                data = ev.get("data", "")
                if len(data) < 130:
                    continue
                q = int(data[2:66], 16) if quote0 else int(data[66:130], 16)
                if q > peak:
                    peak, peak_block, peak_timestamp = q, int(ev["blockNumber"], 16), t
                eligible_events += 1
            out.write_text(json.dumps({"source": str(src.relative_to(ROOT)), "first_mint_timestamp": start,
                                       "window_end_timestamp": start + 30*86400, "peak_quote_30d": peak,
                                       "peak_block": peak_block, "peak_timestamp": peak_timestamp,
                                       "n_sync_window": eligible_events, "truncated": truncated,
                                       "raw": raw}, separators=(",", ":")))
            count += 1
            if count % 25 == 0:
                print(f"sync peaks {count}", flush=True)
    finally:
        await client.close()
        lock.close()
    print(f"sync peaks done {count}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
