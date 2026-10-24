"""On-chain verification of universe candidates at a pinned block per chain (read-only eth_call / eth_getCode).

For each candidate address: eth_getCode, totalSupply(), decimals(), symbol(), name() at the pinned block, in JSON-RPC batches
(drpc free tier caps batches at 3 calls, publicnode allows 20; <=4.5 requests/s per endpoint). If decimals() does not
return a uint, a second pass asks supportsInterface(0x80ac58cd) (ERC-721) and supportsInterface(0xd9b67a26) (ERC-1155).
Every request/response exchange is saved verbatim (gzip) under data/raw/universe-rpc/<chain>/chunk_NNNN_<sha8>.json.gz
(+ a MANIFEST.jsonl line with sha256/retrieved_at). Results (never dropping failures) go to data/universe/<chain>.verify.jsonl.

Pinning: archive-capable endpoints (ethereum, polygon on drpc) use ONE pinned block per chain, stored in
data/universe/pins.json. Chains whose usable free endpoint only serves ~recent state (arbitrum, bsc, base, optimism on
publicnode; drpc base/optimism were 429-rate-limited for this entire run) re-pin (latest-4/8) for every chunk of 30-100 tokens
and record the block on each row (verify_block).
Usage: verify_onchain.py <chain> [--limit N]
"""
import csv, gzip, json, sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from common import *
import urllib.request, urllib.error
from eth_utils import keccak

# chain -> (url, batch size, windowed?, back offset)
EP = {
    "ethereum": ("https://eth.drpc.org", 3, False, 64),
    "base": ("https://base-rpc.publicnode.com", 20, True, 4),  # drpc base was rate-limited (429) for the whole run
    "polygon": ("https://polygon.drpc.org", 3, False, 300),
    "optimism": ("https://optimism-rpc.publicnode.com", 20, True, 4),  # drpc optimism was rate-limited (429) for the whole run
    "arbitrum": ("https://arbitrum-one-rpc.publicnode.com", 20, True, 4),
    "bsc": ("https://bsc-rpc.publicnode.com", 20, True, 8),
}
SEL = {"supply": "0x18160ddd", "decimals": "0x313ce567", "symbol": "0x95d89b41", "name": "0x06fdde03"}
SI721 = "0x01ffc9a7" + "80ac58cd".ljust(64, "0")
SI1155 = "0x01ffc9a7" + "d9b67a26".ljust(64, "0")
T = Throttle(0.22)
TRANSIENT = ("rate", "limit", "timeout", "header not found", "missing trie", "try again", "temporarily", "too many", "capacity")


def post(url, payload, tries=40):
    body = json.dumps(payload).encode()
    backoff = 5
    for i in range(tries):
        T.min_interval = 0.35 if "drpc.org" in url else 0.22
        T.wait()
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            txt = e.read()[:300]
            if e.code in (429, 502, 503, 504) or (e.code == 500 and b"rate" in txt.lower()):
                time.sleep(backoff); backoff = min(backoff * 2, 120); continue
            try:
                return e.code, json.loads(txt)
            except Exception:  # noqa: BLE001
                return e.code, txt.decode(errors="replace")
        except Exception as e:  # noqa: BLE001
            time.sleep(backoff); backoff = min(backoff * 2, 120)
    return None, "exhausted retries"


def is_transient(item, repin=None):
    e = item.get("error")
    if not e: return False
    m = str(e.get("message", "")).lower()
    if repin and "archive" in m: return True  # state fell out of the node's recent window: retry at a fresh block
    return any(k in m for k in TRANSIENT)


def run_calls(url, calls, bsz, log, repin=None):
    """calls: list of (id, method, params). Returns {id: response item}. Retries transient per-call errors up to 3 rounds.
    Every exchange appended to log."""
    results = {}
    pending = list(calls)
    for rnd in range(6):
        if rnd > 0 and repin:  # windowed chain: re-pin the retried calls to a fresh block (last param is the block tag)
            nb = repin()
            pending = [(cid, m, p[:-1] + [nb]) for cid, m, p in pending]
        nxt = []
        for i in range(0, len(pending), bsz):
            part = pending[i:i + bsz]
            payload = [{"jsonrpc": "2.0", "id": cid, "method": m, "params": p} for cid, m, p in part]
            st, resp = post(url, payload)
            log.append({"http_status": st, "request": payload, "response": resp, "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            by = {}
            if isinstance(resp, list):
                for it in resp:
                    by[str(it.get("id"))] = it
            for cid, m, p in part:
                it = by.get(str(cid))
                if it is None:
                    nxt.append((cid, m, p)); results[cid] = {"error": {"message": f"no response (http {st}): {str(resp)[:120]}"}, "_blk": p[-1]}
                elif is_transient(it, repin):
                    nxt.append((cid, m, p)); results[cid] = dict(it, _blk=p[-1])
                else:
                    results[cid] = dict(it, _blk=p[-1])
        pending = nxt
        if not pending: break
        time.sleep(3)
    return results


def hex_uint(res):
    if not isinstance(res, str) or not res.startswith("0x"): return None
    h = res[2:]
    if len(h) != 64: return None
    return int(h, 16)


def dec_str(res):
    """Return (string, encoding) for ABI string or bytes32 results."""
    if not isinstance(res, str) or not res.startswith("0x") or len(res) <= 2: return None, None
    h = res[2:]
    try:
        if len(h) == 64:
            raw = bytes.fromhex(h).rstrip(b"\x00")
            return raw.decode("utf-8", errors="replace"), "bytes32"
        if len(h) >= 128 and int(h[:64], 16) == 32:
            n = int(h[64:128], 16)
            if n > 4096 or len(h) < 128 + 2 * n: return None, "malformed"
            return bytes.fromhex(h[128:128 + 2 * n]).decode("utf-8", errors="replace"), "string"
    except Exception:  # noqa: BLE001
        return None, "malformed"
    return None, "malformed"


def classify(item):
    """-> (status, result_hex) where status in ok|revert|empty|rpc_error."""
    if item is None: return "rpc_error", None
    if "error" in item:
        m = str(item["error"].get("message", "")).lower()
        if "revert" in m or "execution" in m or item["error"].get("code") == 3 or "invalid opcode" in m or "out of gas" in m or "stack" in m:
            return "revert", item["error"].get("message")
        return "rpc_error", item["error"].get("message")
    r = item.get("result")
    if r in ("0x", None): return "empty", r
    return "ok", r


def get_pin(chain, url, back):
    pins = json.load(open(UNI / "pins.json")) if (UNI / "pins.json").exists() else {}
    if chain in pins: return pins[chain]
    st, r = post(url, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
    n = int(r["result"], 16) - back
    st, b = post(url, {"jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber", "params": [hex(n), False]})
    blk = b["result"]
    pin = {"block": int(blk["number"], 16), "hash": blk["hash"], "timestamp": int(blk["timestamp"], 16),
           "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(blk["timestamp"], 16))),
           "rpc": url, "pinned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    pins[chain] = pin
    json.dump(pins, open(UNI / "pins.json", "w"), indent=1)
    return pin


def window_pin(url, back):
    st, r = post(url, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
    return int(r["result"], 16) - back


def main(chain, limit=None):
    url, bsz, windowed, back = EP[chain]
    cand = list(csv.DictReader(open(UNI / f"{chain}.candidates.csv")))
    out_p = UNI / f"{chain}.verify.jsonl"
    done = set()
    if out_p.exists():
        for line in open(out_p):
            j = json.loads(line)
            if j["verify_status"] != "rpc_error": done.add(j["address"].lower())
    todo = [c for c in cand if c["address"].lower() not in done]
    if limit: todo = todo[:limit]
    print(chain, "candidates", len(cand), "todo", len(todo), flush=True)
    pin = None if windowed else get_pin(chain, url, back)
    if pin: print("pinned", pin, flush=True)
    rawdir = f"universe-rpc/{chain}"
    CH = {"arbitrum": 30, "base": 50, "optimism": 50}.get(chain, 100)  # publicnode serves only ~64-128 blocks of recent state
    for ci in range(0, len(todo), CH):
        chunk = todo[ci:ci + CH]
        blk_n = window_pin(url, back) if windowed else pin["block"]
        blk = hex(blk_n)
        repin = (lambda: hex(window_pin(url, back))) if windowed else None
        calls = []
        for i, c in enumerate(chunk):
            a = c["address"]
            calls.append((f"{i}:code", "eth_getCode", [a, blk]))
            for k, sel in SEL.items():
                calls.append((f"{i}:{k}", "eth_call", [{"to": a, "data": sel}, blk]))
        log = []
        res = run_calls(url, calls, bsz, log, repin)
        # second pass for ERC-721/1155 detection where decimals is not a uint
        calls2 = []
        for i, c in enumerate(chunk):
            st_dec, rr = classify(res.get(f"{i}:decimals"))
            code_ok = classify(res.get(f"{i}:code"))[1] not in (None, "0x")
            if code_ok and (st_dec != "ok" or hex_uint(rr) is None):
                calls2.append((f"{i}:si721", "eth_call", [{"to": c["address"], "data": SI721}, blk]))
                calls2.append((f"{i}:si1155", "eth_call", [{"to": c["address"], "data": SI1155}, blk]))
        if calls2: res.update(run_calls(url, calls2, bsz, log, repin))
        data = gzip.compress(json.dumps({"url": url, "chain": chain, "block": blk_n, "exchanges": log}).encode(), 6)
        h = hashlib.sha256(data).hexdigest()
        rawrel = save_raw(rawdir, f"chunk_{ci // CH:04d}_{h[:8]}", data, {"url": url, "method": "jsonrpc-batch", "block": blk_n,
                          "addresses": len(chunk), "status": 200}, ext="json.gz")
        rawrel = str(rawrel.relative_to(ROOT))
        with open(out_p, "a") as f:
            for i, c in enumerate(chunk):
                g = lambda k: classify(res.get(f"{i}:{k}"))
                st_code, code = g("code")
                has_code = st_code == "ok" and bool(code) and code != "0x"
                code_bytes = bytes.fromhex(code[2:]) if has_code else b""
                st_s, r_s = g("supply"); st_d, r_d = g("decimals"); st_y, r_y = g("symbol"); st_n, r_n = g("name")
                supply = hex_uint(r_s) if st_s == "ok" else None
                dec = hex_uint(r_d) if st_d == "ok" else None
                sym, sym_enc = dec_str(r_y) if st_y == "ok" else (None, None)
                nm, nm_enc = dec_str(r_n) if st_n == "ok" else (None, None)
                si721 = classify(res.get(f"{i}:si721")); si1155 = classify(res.get(f"{i}:si1155"))
                is721 = si721[0] == "ok" and hex_uint(si721[1]) == 1
                is1155 = si1155[0] == "ok" and hex_uint(si1155[1]) == 1
                statuses = [st_code, st_s, st_d, st_y, st_n]
                if st_code == "rpc_error" or (has_code and "rpc_error" in statuses): vs = "rpc_error"
                elif not has_code: vs = "no_code"
                elif is721: vs = "erc721"
                elif is1155: vs = "erc1155"
                elif supply is not None and dec is not None: vs = "ok"
                elif supply is not None: vs = "no_decimals"
                elif dec is not None: vs = "no_supply"
                else: vs = "neither"
                vb = res.get(f"{i}:code", {}).get("_blk", blk)
                rec = dict(chain=chain, address=c["address"], verify_block=int(vb, 16), verify_status=vs, has_code=has_code,
                           code_size=len(code_bytes), code_keccak=("0x" + keccak(code_bytes).hex()) if has_code else None,
                           supply_status=st_s, total_supply=str(supply) if supply is not None else None,
                           supply_raw=r_s if st_s == "ok" and supply is None else None,
                           decimals_status=st_d, decimals=dec, decimals_raw=r_d if st_d == "ok" and (dec is None or dec > 255) else None,
                           symbol_status=st_y, symbol_onchain=sym, symbol_encoding=sym_enc,
                           name_status=st_n, name_onchain=nm, name_encoding=nm_enc,
                           msg_supply=r_s if st_s in ("revert", "rpc_error") else None, msg_decimals=r_d if st_d in ("revert", "rpc_error") else None,
                           supports_721=is721 if si721[0] != "rpc_error" and (f"{i}:si721" in res) else None,
                           supports_1155=is1155 if (f"{i}:si1155" in res) else None,
                           raw=rawrel, raw_id_prefix=f"{i}:")
                f.write(json.dumps(rec) + "\n")
        print(f"{chain} chunk {ci // CH + 1}/{(len(todo) + CH - 1) // CH} block {blk_n} raw {rawrel}", flush=True)


if __name__ == "__main__":
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    main(sys.argv[1], lim)
