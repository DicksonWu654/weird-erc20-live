#!/usr/bin/env python3
"""Targeted, cached read-only checks at each candidate's original control block.

No broad re-probing. One RPC at a time, <=1 req/s, full immutable raw responses.
Run with --live once; subsequent runs are offline and reproduce the derived overlay.
"""
import argparse
import hashlib
import json
import pathlib
import time

import requests
from ctl_abi import dec_addr, dec_bool, dec_uint, dec_addr_array, scan_code, sel, pad_addr
from legacy_forward import apply_legacy_forward, has_legacy_forward

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data/control/c19"
URL = "https://eth.drpc.org"


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def collect(row, rawfile):
    block, token = row["block"], row["token"]
    entries = []
    def rpc(tag, method, params):
        request = {"jsonrpc": "2.0", "id": len(entries), "method": method, "params": params}
        for attempt in range(4):
            time.sleep(1.1 * (2 ** attempt))
            retrieved = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            try:
                resp = requests.post(URL, json=request, timeout=30)
                body = resp.text
                try:
                    response = resp.json()
                except ValueError:
                    response = {"non_json_body": body}
                entry = {"id": f"e{len(entries)}", "tag": tag, "chain": row["chain"], "block": block,
                         "url": URL, "request": request, "response": response, "http_status": resp.status_code,
                         "body": body, "retrieved_at": retrieved,
                         "sha256": hashlib.sha256(canon(response).encode()).hexdigest(),
                         "body_sha256": hashlib.sha256(resp.content).hexdigest()}
            except requests.RequestException as exc:
                entry = {"id": f"e{len(entries)}", "tag": tag, "chain": row["chain"], "block": block,
                         "url": URL, "request": request, "response": {"transport_error": str(exc)}, "http_status": None,
                         "retrieved_at": retrieved}
                entry["sha256"] = hashlib.sha256(canon(entry["response"]).encode()).hexdigest()
            entries.append(entry)
            with rawfile.open("a") as f:
                f.write(json.dumps(entry) + "\n")
            if entry["http_status"] == 200 and not any(w in str(entry["response"].get("error", {})).lower() for w in ("rate limit", "429", "too many requests")):
                return entry["response"]
        return entries[-1]["response"]
    bh = hex(block)
    rpc("pinned_block", "eth_getBlockByNumber", [bh, False])
    rpc("code", "eth_getCode", [token, bh])
    def view(tag, address, signature):
        return rpc(tag, "eth_call", [{"to": address, "data": sel(signature)}, bh])
    view("deprecated", token, "deprecated()")
    view("upgradedAddress", token, "upgradedAddress()")
    owner = dec_addr(view("owner", token, "owner()").get("result"))
    if owner:
        rpc("owner_code", "eth_getCode", [owner, bh])
        view("owner_required", owner, "required()")
        view("owner_owners", owner, "getOwners()")
        for tag, caller in [("deprecate_owner", owner), ("deprecate_stranger", "0x1000000000000000000000000000000000000001")]:
            rpc(tag, "eth_call", [{"from": caller, "to": token, "data": sel("deprecate(address)") + pad_addr(token)}, bh])


def derive(row, rawfile):
    entries = [json.loads(l) for l in rawfile.read_text().splitlines()]
    by = {e["tag"]: e for e in entries}
    result = lambda tag: by.get(tag, {}).get("response", {}).get("result")
    code = result("code") or "0x"
    owner = dec_addr(result("owner"))
    stranger = by.get("deprecate_stranger", {}).get("response", {}).get("error", {})
    # Transport/rate failures are not evidence that an unauthorized caller reverts.
    stranger_reverts = "revert" in str(stranger.get("message", "")).lower()
    ok = (has_legacy_forward(scan_code(code)["selectors"]) and dec_bool(result("deprecated")) is not None
          and dec_addr(result("upgradedAddress")) is not None and owner is not None
          and result("deprecate_owner") is not None and stranger_reverts
          and result("pinned_block") is not None)
    return {"chain": row["chain"], "token": row["token"], "symbol": row["symbol"], "block": row["block"],
            "status": "confirmed" if ok else "unprobeable", "owner": owner,
            "deprecated": dec_bool(result("deprecated")), "upgraded_address": dec_addr(result("upgradedAddress")),
            "required": dec_uint(result("owner_required")), "owners": dec_addr_array(result("owner_owners")),
            "block_hash": (result("pinned_block") or {}).get("hash"),
            "ev": [str(rawfile.relative_to(ROOT)) + "#" + by[tag]["id"] for tag in
                   ["deprecate_owner", "deprecate_stranger", "owner", "code", "deprecated", "upgradedAddress", "pinned_block", "owner_code", "owner_required", "owner_owners"] if tag in by]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="fetch uncached candidates only")
    args = ap.parse_args()
    (OUT / "evidence").mkdir(parents=True, exist_ok=True)
    checks, derived = [], []
    # Discover candidates from saved selectors, not an address allowlist.
    for chain in (ROOT / "data/probe").iterdir():
        p = chain / "results.final.jsonl"
        cp = ROOT / "data/control" / chain.name / "control.jsonl"
        if not p.exists() or not cp.exists():
            continue
        ctl = {r["token"]: r for r in map(json.loads, cp.read_text().splitlines())}
        for r in map(json.loads, p.read_text().splitlines()):
            d = r.get("detail") or {}
            if r["test"] != "bytecode_selectors" or not has_legacy_forward(d.get("own_selectors") or []):
                continue
            c = ctl.get(r["token"])
            if c is None:
                raise RuntimeError(f"candidate without control row: {r['token']}")
            raw = OUT / "evidence" / f"{r['token']}-{c['block']}.jsonl"
            if not raw.exists():
                if not args.live:
                    raise RuntimeError("missing cached check; run once with --live")
                collect(c, raw)
            check = derive(c, raw)
            checks.append(check)
            derived.append(apply_legacy_forward(c, check))
            print(c["symbol"], c["block"], check["status"], derived[-1]["upgradeable"], flush=True)
    (OUT / "legacy_forward_checks.json").write_text(json.dumps(checks, indent=2) + "\n")
    (OUT / "derived.jsonl").write_text("".join(json.dumps(r) + "\n" for r in derived))
    if any(c["status"] != "confirmed" for c in checks):
        raise SystemExit("some legacy candidates remain unprobeable; inspect saved errors")


if __name__ == "__main__":
    main()
