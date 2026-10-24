#!/usr/bin/env python3
"""Stratified, resumable Etherscan second pass for liquidity-collapse screens."""
import asyncio
import hashlib
import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/sim"))
import rug as RG

OUT = ROOT / "data/tier2/rug-second-pass"
API = "https://api.etherscan.io/v2/api"
PER_MONTH = 8
TARGET_ELIGIBLE = 40


class Client:
    def __init__(self, key, interval=0.5):
        self.key = key
        self.interval = interval
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90))
        self.next_at = 0.0

    async def get(self, **params):
        q = {"chainid": 1, **params}
        for attempt in range(6):
            wait = self.next_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            # Two requests per second leaves headroom under the shared
            # Etherscan ceiling of four, including retries.
            self.next_at = max(self.next_at, time.monotonic()) + self.interval
            try:
                async with self.session.get(API, params={**q, "apikey": self.key}) as resp:
                    body = await resp.read()
                    parsed = json.loads(body)
                    raw = {"request": {"url": API, "params": q}, "http_status": resp.status,
                           "body": body.decode(errors="replace"), "response": parsed,
                           "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "sha256": hashlib.sha256(body).hexdigest()}
                result = parsed.get("result")
                if resp.status == 429 or (isinstance(result, str) and
                   any(s in result.lower() for s in ("rate limit", "max calls", "busy"))):
                    await asyncio.sleep(min(2 ** attempt, 30))
                    continue
                return raw
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                if attempt == 5:
                    raise RuntimeError(f"Etherscan transport: {exc!r}")
                await asyncio.sleep(min(2 ** attempt, 30))
        raise RuntimeError("Etherscan rate-limited")

    async def close(self):
        await self.session.close()


class Raw:
    def __init__(self):
        self.items = []

    def add(self, label, raw):
        self.items.append({"label": label, **raw})


def sample():
    by_month = defaultdict(list)
    for line in open(ROOT / "data/tier2/sim-run/results.jsonl"):
        r = json.loads(line)
        if (r.get("rug") or {}).get("verdict") == "liquidity_declined_screen_only":
            by_month[(r.get("extra") or {}).get("month")].append(r)
    rng = random.Random(20261022)
    picks = []
    for month, rows in sorted(by_month.items()):
        rows = sorted(rows, key=lambda r: (r["token"], r["pool"], r["block"]))
        for r in rng.sample(rows, min(PER_MONTH, len(rows))):
            picks.append({"month": month, "N_month": len(rows), "n_month": min(PER_MONTH, len(rows)),
                          "token": r["token"], "pool": r["pool"], "block": r["block"],
                          "created_block": r["created_block"], "dex": r["dex"],
                          "weight": (r.get("extra") or {}).get("weight"), "screen": r["rug"]["screen"],
                          "pool_info": r.get("pool_info"), "base_primary": r["primary_label"],
                          "base_reason": r.get("label_reason")})
    return picks


def extend_eligible(picks):
    """Complete a uniform eligible-domain sample of up to 40 per frame month.

    The original up-to-eight screen draw was uniform.  Conditional on that
    draw, the added eligible rows are uniform without replacement.  The staged
    20-then-40 extension also samples uniformly from the remaining eligible
    keys, so the final inclusion probability is min(40,E_h)/E_h.
    """
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("tier2_aggregate", ROOT / "scripts/tier2/aggregate.py")
    mod = module_from_spec(spec); spec.loader.exec_module(mod)
    frame = mod.assemble(write=False)
    eligible = frame[(frame.rug_screen_verdict == "liquidity_declined_screen_only") &
                     ~frame.base_category.isin(["dust_pool", "honeypot", "no_pool", "pending_v4"])]
    saved = {(r["token"].lower(), r["pool"].lower(), int(r["block"])) for r in picks}
    sim = mod.load_results(ROOT / "data/tier2/sim-run/results.jsonl")
    # A distinct seed for the 40-check increment keeps its random draw
    # separate from the earlier 20-check extension.
    rng = random.Random(20261022 + TARGET_ELIGIBLE // 20)
    for month, g in eligible.groupby("month"):
        keys = sorted({(str(r.address).lower(), str(r.selected_pool).lower(), int(r.sim_block)) for r in g.itertuples()})
        already = sum(k in saved for k in keys)
        add = rng.sample([k for k in keys if k not in saved], max(0, min(TARGET_ELIGIBLE, len(keys)) - already))
        for k in add:
            r = sim[k]
            picks.append({"month": month, "N_month": None, "n_month": None,
                          "eligible_N_month": len(keys), "eligible_n_month": min(TARGET_ELIGIBLE, len(keys)),
                          "token": r["token"], "pool": r["pool"], "block": r["block"],
                          "created_block": r["created_block"], "dex": r["dex"],
                          "weight": (r.get("extra") or {}).get("weight"), "screen": r["rug"]["screen"],
                          "pool_info": r.get("pool_info"), "base_primary": r["primary_label"],
                          "base_reason": r.get("label_reason"), "sample_phase": "eligible_extension"})
        print(f"extend {month}: eligible {already}->{already+len(add)}/{len(keys)}", flush=True)
    return picks


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = OUT / "sample.jsonl"
    picks = [json.loads(s) for s in open(manifest)] if manifest.exists() else sample()
    if not manifest.exists():
        manifest.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in picks))
    if os.environ.get("TIER2_EXTEND_ELIGIBLE") == "1":
        picks = extend_eligible(picks)
        manifest.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in picks))
    key = RG.etherscan_key()
    if not key:
        raise RuntimeError("ETHERSCAN_API_KEY unavailable")
    client = Client(key)
    try:
        for i, r in enumerate(picks):
            name = r["token"].lower()[2:] + "_" + str(r["block"])
            dest = OUT / (name + ".json")
            if dest.exists():
                continue
            rl = Raw()
            kind = (r.get("pool_info") or {}).get("kind")
            try:
                detail = await RG.rug_detail(client, rl, r["token"], r["pool"], kind,
                     bool((r.get("pool_info") or {}).get("quote_is_token0")),
                     r["created_block"], head=r["screen"]["head"] - r["screen"]["head"] % 2000)
                verdict = RG.rug_verdict(r["screen"], detail)
                error = None
            except Exception as exc:
                detail, verdict, error = None, {"verdict": "unknown"}, repr(exc)
            dest.write_text(json.dumps({"sample": r, "detail": detail, "verdict": verdict,
                                        "error": error, "raw": rl.items}, separators=(",", ":")))
            if (i + 1) % 25 == 0:
                print(f"rug audited {i+1}/{len(picks)}", flush=True)
        print(f"rug done {len(picks)}", flush=True)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
