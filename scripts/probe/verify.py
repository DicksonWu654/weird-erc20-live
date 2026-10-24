#!/usr/bin/env python3
"""Evidence integrity check for a probe output directory.

    .venv/bin/python scripts/probe/verify.py data/probe/ethereum [--sample 200]

For (a sample of) the rows in results.final.jsonl / results.jsonl: the evidence file exists, every cited call id exists
in it, and sha256(canonical JSON of the stored response) equals the sha256 recorded in the row. Also re-derives the
decoded harness steps of one script per sampled row to make sure the payload still decodes. Exit code 1 on any problem.
"""
import argparse
import gzip
import hashlib
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from rpc import sha256_obj  # noqa: E402
from common import decode_run, unhex  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--sample", type=int, default=0, help="0 = all rows")
    a = ap.parse_args()
    d = pathlib.Path(a.dir)
    p = d / "results.final.jsonl"
    if not p.exists():
        p = d / "results.jsonl"
    rows = [json.loads(line) for line in p.read_text().splitlines()]
    if a.sample and a.sample < len(rows):
        rows = random.Random(1).sample(rows, a.sample)
    cache, bad, checked = {}, [], 0
    for r in rows:
        ev = r.get("evidence")
        if not ev:
            if r["status"] not in ("error",):
                bad.append((r["token"], r["test"], "no evidence path"))
            continue
        f = d / ev
        if str(f) not in cache:
            if not f.exists():
                bad.append((r["token"], r["test"], f"missing {ev}"))
                continue
            cache[str(f)] = json.load(gzip.open(f))
        doc = cache[str(f)]
        calls = {c["cid"]: c for c in doc["calls"]}
        if len(r["evidence_calls"]) != len(r["evidence_sha256"]):
            bad.append((r["token"], r["test"], "calls/sha length mismatch"))
        for cid, sha in zip(r["evidence_calls"], r["evidence_sha256"]):
            c = calls.get(cid)
            if c is None:
                bad.append((r["token"], r["test"], f"call {cid} not in evidence"))
                continue
            if sha256_obj(c["response"]) != sha or c["response_sha256"] != sha:
                bad.append((r["token"], r["test"], f"sha mismatch on call {cid}"))
            checked += 1
            if r.get("script") and c["tag"].startswith("battery") and "result" in c["response"]:
                try:
                    res = c["response"]["result"]
                    if c.get("request", {}).get("method") == "eth_simulateV1":   # real-holder mode: harness run is call #2
                        res = res[0]["calls"][1]["returnData"]
                    decode_run(unhex(res))
                except Exception as e:
                    bad.append((r["token"], r["test"], f"payload does not decode: {e}"))
    print(f"{len(rows)} rows, {checked} cited responses checked, {len(bad)} problems")
    for b in bad[:30]:
        print("  PROBLEM", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
