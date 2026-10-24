#!/usr/bin/env python3
"""Weird-ERC20-Live probe runner.

    .venv/bin/python scripts/probe/run.py --chain ethereum --tokens tokens.csv --block latest --out data/probe/ethereum/

Writes <out>/results.jsonl (one row per token x test, appended), <out>/raw/<token>.json.gz (every RPC request+response of
that token, with url / retrieved_at / sha256), <out>/done.jsonl (completed tokens: resume skips them) and
<out>/runs.jsonl (one line per invocation). Re-running resumes; tokens whose probe hit infrastructure errors are retried.
See scripts/probe/README.md.
"""
import argparse
import asyncio
import csv
import hashlib
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import aiohttp  # noqa: E402

from chains import CHAINS  # noqa: E402
from common import BYTECODE, ROOT, H, SINK, REJ, R, E  # noqa: E402
from rpc import Endpoint, Pool, Recorder, utcnow  # noqa: E402
import probe as P  # noqa: E402
import classify as C  # noqa: E402

PROBE_VERSION = "1"
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")


def load_tokens(spec):
    """csv / jsonl / txt file, comma separated addresses, or '-' for stdin -> ordered unique list of lowercase addresses"""
    out, seen = [], set()

    def add(a):
        a = a.lower()
        if a not in seen and ADDR_RE.fullmatch(a):
            seen.add(a)
            out.append(a)

    p = pathlib.Path(spec)
    if spec == "-":
        text = sys.stdin.read()
        for m in ADDR_RE.findall(text):
            add(m)
        return out
    try:
        is_file = p.exists()
    except OSError:
        is_file = False
    if not is_file:
        for a in spec.split(","):
            add(a.strip())
        return out
    if p.suffix == ".jsonl":
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                m = ADDR_RE.search(line)
                if m:
                    add(m.group(0))
                continue
            if isinstance(j, str):
                add(j)
            elif isinstance(j, dict):
                for k in ("address", "token", "token_address", "contract", "contract_address", "addr"):
                    if k in j and isinstance(j[k], str):
                        add(j[k])
                        break
        return out
    if p.suffix == ".csv":
        with open(p, newline="") as f:
            rd = csv.reader(f)
            rows = list(rd)
        if not rows:
            return out
        hdr = [h.strip().lower() for h in rows[0]]
        col = None
        for k in ("address", "token", "token_address", "contract", "contract_address", "addr"):
            if k in hdr:
                col = hdr.index(k)
                break
        body = rows[1:] if col is not None else rows
        col = col or 0
        for r in body:
            if len(r) > col:
                add(r[col].strip())
        return out
    for m in ADDR_RE.findall(p.read_text()):
        add(m)
    return out


STALE_PAT = re.compile(r"Unknown state|missing trie node|state .{0,40}not available|Archive requests|header not found", re.I)


class Pin:
    """Block pinning. `latest`: re-resolved every ttl seconds (latest - lag), each row records the block it used.
    Fixed N: used as is. The lag (default 6 s, at least 3 blocks) keeps the pin inside what freshly-synced nodes can serve
    state for (fast L2s such as Arbitrum answer 'Unknown state' for the newest blocks). Also estimates the drift block."""

    def __init__(self, pool, mode, ttl, drift_days, lag_s=6.0):
        self.pool, self.mode, self.ttl, self.drift_days, self.lag_s = pool, mode, ttl, drift_days, lag_s
        self.cur = None
        self.t = 0
        self.lock = asyncio.Lock()
        self.rate = None     # seconds per block

    async def _estimate_rate(self, n):
        a = await self.pool.one("eth_getBlockByNumber", [hex(n), False], "pin_rate_new")
        o = await self.pool.one("eth_getBlockByNumber", [hex(max(n - 100000, 1)), False], "pin_rate_old")
        ab, ob = a.get("result"), o.get("result")
        if ab and ob:
            dt = int(ab["timestamp"], 16) - int(ob["timestamp"], 16)
            nn = int(ab["number"], 16) - int(ob["number"], 16)
            if nn and dt > 0:
                self.rate = dt / nn
        return self.rate

    async def get(self, fresh_after=None):
        """fresh_after: a pin tuple the caller found expired; re-resolve unless someone already did."""
        async with self.lock:
            if fresh_after is not None and self.cur is fresh_after and self.mode == "latest":
                self.t = -1e9
            if self.cur is None or (self.mode == "latest" and time.monotonic() - self.t > self.ttl):
                r = await self.pool.one("eth_blockNumber", [], "pin_latest")
                if "result" not in r:
                    raise RuntimeError("cannot resolve latest block: " + str(r.get("error")))
                head = int(r["result"], 16)
                if self.rate is None:
                    await self._estimate_rate(head - 5)
                lag = 3
                if self.rate:
                    lag = max(3, int(-(-self.lag_s // self.rate)))
                n = head - lag if self.mode == "latest" else int(self.mode)
                b = await self.pool.one("eth_getBlockByNumber", [hex(n), False], "pin_block")
                blk = b.get("result") or {}
                info = {"number": n, "hash": blk.get("hash"), "timestamp": int(blk["timestamp"], 16) if blk.get("timestamp") else None,
                        "head_at_pin": head, "lag_blocks": head - n, "seconds_per_block_est": self.rate}
                drift = None
                if self.drift_days and self.rate:
                    drift = n - int(self.drift_days * 86400 / self.rate)
                self.cur = (info, drift)
                self.t = time.monotonic()
            return self.cur


async def main_async(args):
    chain = args.chain
    cfg = dict(CHAINS[chain])
    out = pathlib.Path(args.out) if args.out else ROOT / "data" / "probe" / chain
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw").mkdir(exist_ok=True)
    tokens = load_tokens(args.tokens)
    if args.limit:
        tokens = tokens[: args.limit]
    done = set()
    dpath = out / "done.jsonl"
    if dpath.exists():
        for line in dpath.read_text().splitlines():
            try:
                j = json.loads(line)
                if j.get("complete") and not args.redo:
                    done.add(j["token"])
            except Exception:
                pass
    todo = [t for t in tokens if t not in done]
    print(f"[{chain}] {len(tokens)} tokens, {len(done & set(tokens))} already done, {len(todo)} to probe -> {out}", flush=True)
    if not todo:
        return
    rpcs = cfg["rpcs"]
    if args.rpc:
        rpcs = [(u, None, 3 if "drpc" in u else 20) for u in args.rpc.split(",")]
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=64)) as session:
        eps = [Endpoint(u, rps=args.rps, max_batch=mb, archive=ar) for (u, ar, mb) in rpcs]
        pool = Pool(chain, eps, session, timeout=args.timeout)
        pin_ttl = args.pin_ttl if args.pin_ttl is not None else cfg.get("pin_ttl", 60.0)
        pin_lag = args.pin_lag if args.pin_lag is not None else cfg.get("pin_lag", 6.0)
        pin = Pin(pool, args.block, pin_ttl, 0 if args.no_drift else args.drift_days, pin_lag)
        info0, drift0 = await pin.get()
        run_meta = {
            "started_at": utcnow(), "chain": chain, "argv": sys.argv[1:], "probe_version": PROBE_VERSION,
            "block_mode": args.block, "first_block": info0, "drift_block": drift0, "endpoints": [e.url for e in eps],
            "rps_per_endpoint": args.rps, "concurrency": args.concurrency,
            "harness": {"addresses": {"H": H, "SINK": SINK, "REJ": REJ, "R": R, "E": E},
                        "runtime_sha256": {k: BYTECODE[k]["sha256"] for k in ("Probe", "Sink", "Rejecter")},
                        "source_sha256": BYTECODE["_meta"]["source_sha256"], "solc": BYTECODE["_meta"]["solc"]},
        }
        with open(out / "runs.jsonl", "a") as f:
            f.write(json.dumps(run_meta) + "\n")
        q = asyncio.Queue()
        for t in todo:
            q.put_nowait(t)
        wl = asyncio.Lock()
        stats = {"n": 0, "complete": 0, "errors": 0, "t0": time.time(), "status": {}}
        rf = open(out / "results.jsonl", "a")
        df = open(dpath, "a")

        async def worker():
            while True:
                try:
                    tok = q.get_nowait()
                except asyncio.QueueEmpty:
                    return
                pinned = None
                for attempt in range(3):
                    rec = Recorder(chain, tok)
                    rows = []
                    try:
                        pinned = await pin.get(fresh_after=pinned if attempt else None)
                        info, drift = pinned
                        ctx = await asyncio.wait_for(
                            P.probe_token(pool, cfg, chain, tok, info["number"], hex(info["number"]), rec,
                                          drift_block=drift, run_extras=True), timeout=args.token_timeout)
                        rel = f"raw/{tok}.json.gz"
                        rows = C.classify(ctx, rec, rel)
                        extra = {"block": info, "drift_block": drift, "retrieved_by": "scripts/probe/run.py",
                                 "probe_version": PROBE_VERSION, "attempt": attempt + 1,
                                 "harness_runtime_sha256": {k: BYTECODE[k]["sha256"] for k in ("Probe", "Sink", "Rejecter")}}
                        rec.save(out / rel, extra)
                    except asyncio.TimeoutError:
                        rows = [{"chain": chain, "token": tok, "test": "probe", "status": "unprobeable", "value": "token_time_budget_exceeded",
                                 "detail": {"seconds": args.token_timeout, "note": "the whole token probe (funding search + battery) did not finish "
                                            "inside the per-token budget, typically a slow brute-force funding search or a node that "
                                            "stalls on this token; partial evidence is saved in raw/<token>.json.gz; rerun with --redo "
                                            "and a larger --token-timeout to retry"}, "block": None, "script": None, "evidence": None,
                                 "evidence_calls": [], "evidence_sha256": []}]
                        rec.save(out / "raw" / f"{tok}.json.gz", {"token_timeout_s": args.token_timeout, "partial": True})
                    except Exception as e:  # harness/classifier bug or total RPC failure: record, keep going
                        import traceback
                        rows = [{"chain": chain, "token": tok, "test": "probe", "status": "error", "value": f"exception:{type(e).__name__}:{e}",
                                 "detail": {"trace": traceback.format_exc()[-1500:]},
                                 "block": None, "script": None, "evidence": None, "evidence_calls": [], "evidence_sha256": []}]
                        rec.save(out / "raw" / f"{tok}.json.gz", {"exception": str(e)})
                    # state pruned under a slow token (fast L2s keep only seconds of state): retry once more on a fresh pin
                    stale = args.block == "latest" and any(
                        r["status"] == "error" and STALE_PAT.search(json.dumps(r.get("value")) + json.dumps(r.get("detail")))
                        for r in rows)
                    if not stale:
                        break
                now = utcnow()
                for r in rows:
                    r["probed_at"] = now
                n_err = sum(1 for r in rows if r["status"] == "error")
                complete = n_err == 0
                async with wl:
                    for r in rows:
                        rf.write(json.dumps(r, separators=(",", ":")) + "\n")
                    rf.flush()
                    df.write(json.dumps({"token": tok, "complete": complete, "n_rows": len(rows), "n_errors": n_err,
                                         "block": rows[0].get("block") if rows else None, "at": now}) + "\n")
                    df.flush()
                    stats["n"] += 1
                    stats["complete"] += complete
                    stats["errors"] += (not complete)
                    for r in rows:
                        if r["test"] in ("funding", "probe"):
                            stats["status"][r["status"]] = stats["status"].get(r["status"], 0) + 1
                    if stats["n"] % args.progress == 0 or stats["n"] == len(todo):
                        el = time.time() - stats["t0"]
                        print(f"[{chain}] {stats['n']}/{len(todo)} done, {stats['errors']} with errors, "
                              f"{stats['n'] / el * 60:.1f} tokens/min, funding/probe status {stats['status']}, "
                              f"http {[e.n_http for e in eps]} 429s {[e.n_429 for e in eps]}", flush=True)

        await asyncio.gather(*[worker() for _ in range(args.concurrency)])
        rf.close()
        df.close()
        run_meta["finished_at"] = utcnow()
        run_meta["tokens_probed"] = stats["n"]
        run_meta["tokens_with_errors"] = stats["errors"]
        run_meta["seconds"] = round(time.time() - stats["t0"], 1)
        run_meta["http_requests"] = {e.url: e.n_http for e in eps}
        with open(out / "runs.jsonl", "a") as f:
            f.write(json.dumps({"finished": run_meta}) + "\n")


def compact(out):
    """results.jsonl may contain several row sets per token (retries/re-runs): keep the newest set. -> results.final.jsonl"""
    out = pathlib.Path(out)
    # every probe of a token appends a complete row set sharing one probed_at: keep only the newest set per token
    newest = {}
    rows = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]
    for r in rows:
        newest[r["token"]] = max(newest.get(r["token"], ""), r.get("probed_at", ""))
    last = {}
    for r in rows:
        if r.get("probed_at", "") == newest[r["token"]]:
            last[(r["token"], r["test"])] = r
    with open(out / "results.final.jsonl", "w") as f:
        for r in last.values():
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"{len(last)} rows -> {out / 'results.final.jsonl'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", required=True, choices=sorted(CHAINS))
    ap.add_argument("--tokens", help="csv/jsonl/txt file, comma-separated addresses, or - for stdin")
    ap.add_argument("--block", default="latest", help="'latest' (re-pinned every --pin-ttl s, per-row block recorded) or a block number")
    ap.add_argument("--out", help="default data/probe/<chain>/")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--rps", type=float, default=5.0, help="max HTTP requests/s per endpoint")
    ap.add_argument("--rpc", help="override endpoints (comma-separated URLs)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--redo", action="store_true", help="re-probe tokens already marked complete")
    ap.add_argument("--no-drift", action="store_true", help="skip the 7-day balance-drift (rebasing) archive test")
    ap.add_argument("--drift-days", type=float, default=7.0)
    ap.add_argument("--pin-ttl", type=float, default=None, help="seconds a pinned block is reused (default 60; per-chain override in chains.py, arbitrum 2)")
    ap.add_argument("--pin-lag", type=float, default=None, help="seconds behind head for the pinned block (>= 3 blocks; default 6, arbitrum 1)")
    ap.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout per request")
    ap.add_argument("--token-timeout", type=float, default=240.0)
    ap.add_argument("--progress", type=int, default=25)
    ap.add_argument("--compact", action="store_true", help="only dedupe results.jsonl -> results.final.jsonl")
    args = ap.parse_args()
    if args.compact:
        compact(args.out or ROOT / "data" / "probe" / args.chain)
        return
    if not args.tokens:
        ap.error("--tokens is required")
    asyncio.run(main_async(args))
    compact(args.out or ROOT / "data" / "probe" / args.chain)


if __name__ == "__main__":
    main()
