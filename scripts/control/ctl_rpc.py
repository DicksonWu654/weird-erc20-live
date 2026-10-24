"""JSON-RPC client for the W3 control classifier: rate limited, batched, retried, and every call saved as evidence.

Evidence line (one JSON object per request) in `<out>/evidence/<token>.jsonl` or `<out>/evidence/_ctrl/<addr>.jsonl`:
    {id, chain, endpoint, block, request, response, retrieved_at, sha256}
`sha256` is the SHA-256 of the canonical JSON (sort_keys, compact separators) of the *response object exactly as the
node returned it for that request id*. For eth_getCode results longer than OMIT_CODE_OVER chars the stored response has
the result replaced by {result_omitted, result_len, code_keccak} (disk policy: hash + selector set is enough);
`sha256` is still the hash of the full original response so it can be re-verified by re-fetching.
"""
import hashlib
import json
import os
import pathlib
import threading
import time

import requests
from eth_utils import keccak

OMIT_CODE_OVER = 12000  # hex chars (6 KB of code)

# chain -> dict(id, endpoints=[(url, max_batch)], trace=[urls], etherscan id)
CHAINS = {
    "ethereum": dict(id=1, eth=1, endpoints=[("https://ethereum-rpc.publicnode.com", 10), ("https://eth.drpc.org", 3)],
                     archive=[("https://eth.drpc.org", 3)], trace=["https://eth.drpc.org"]),
    "base": dict(id=8453, endpoints=[("https://base-rpc.publicnode.com", 10), ("https://base.drpc.org", 3)],
                 archive=[("https://base.drpc.org", 3)], trace=["https://base.drpc.org"]),
    "arbitrum": dict(id=42161, endpoints=[("https://arbitrum-one-rpc.publicnode.com", 10), ("https://arbitrum.drpc.org", 3)],
                     archive=[("https://arbitrum.drpc.org", 3)], trace=["https://arbitrum.drpc.org"]),
    "bsc": dict(id=56, endpoints=[("https://bsc-rpc.publicnode.com", 10), ("https://bsc-dataseed.bnbchain.org", 1)],
                archive=[("https://bsc-rpc.publicnode.com", 10)], trace=["https://bsc.drpc.org"]),
    "polygon": dict(id=137, endpoints=[("https://polygon-bor-rpc.publicnode.com", 10), ("https://polygon.drpc.org", 3)],
                    archive=[("https://polygon.drpc.org", 3)], trace=["https://polygon.drpc.org"]),
    "optimism": dict(id=10, endpoints=[("https://optimism-rpc.publicnode.com", 10), ("https://optimism.drpc.org", 3)],
                     archive=[("https://optimism.drpc.org", 3)], trace=["https://optimism.drpc.org"]),
}

if os.environ.get("CTL_LOCAL_RPC"):  # unit tests against a local anvil (tests/run_synthetic.py)
    _u = os.environ["CTL_LOCAL_RPC"]
    CHAINS["local"] = dict(id=31337, endpoints=[(_u, 20)], archive=[(_u, 20)], trace=[_u])

RPS = float(os.environ.get("CTL_RPS", "4.5"))          # per RPC endpoint (project limit: 5)
ETHERSCAN_RPS = float(os.environ.get("CTL_ETHERSCAN_RPS", "1.8"))  # key allows 3/s; project limit 4/s

_UA = {"User-Agent": "weird-erc20-live/w3-control (research; read-only)"}


class RateLimiter:
    def __init__(self, rate):
        self.interval = 1.0 / rate
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self, weight=1):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.interval * weight
        if t > now:
            time.sleep(t - now)


_LIMITERS = {}
_LIM_LOCK = threading.Lock()


CALL_RPS = float(os.environ.get("CTL_CALL_RPS", "15"))  # individual calls/s per endpoint (batch items count)


def limiter(key, rate=RPS):
    with _LIM_LOCK:
        if key not in _LIMITERS:
            _LIMITERS[key] = RateLimiter(rate)
        return _LIMITERS[key]


_tls = threading.local()


def session():
    if not hasattr(_tls, "s"):
        _tls.s = requests.Session()
        _tls.s.headers.update(_UA)
    return _tls.s


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha(obj):
    return hashlib.sha256(canon(obj).encode()).hexdigest()


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Ev:
    """Evidence collector for one token / controller address. refs look like 'evidence/0xabc.jsonl#e12'."""

    def __init__(self, relpath, out_root):
        self.rel = relpath
        self.root = pathlib.Path(out_root)
        self.entries = []
        self.lock = threading.Lock()

    def add(self, chain, endpoint, block, request, response, original_sha=None):
        with self.lock:
            eid = f"e{len(self.entries)}"
            self.entries.append({"id": eid, "chain": chain, "endpoint": endpoint, "block": block, "request": request,
                                 "response": response, "retrieved_at": now_iso(),
                                 "sha256": original_sha or sha(response)})
            return f"{self.rel}#{eid}"

    def flush(self):
        p = self.root / self.rel
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w") as f:
            for e in self.entries:
                f.write(json.dumps(e, separators=(",", ":")) + "\n")
        os.replace(tmp, p)


class Result:
    """One RPC result. kind: ok | revert | error. value: hex string (or json) when ok."""
    __slots__ = ("kind", "value", "msg", "ev")

    def __init__(self, kind, value=None, msg=None, ev=None):
        self.kind, self.value, self.msg, self.ev = kind, value, msg, ev

    @property
    def ok(self):
        return self.kind == "ok"

    def __repr__(self):
        return f"Result({self.kind},{str(self.value)[:40]},{self.msg})"


REVERT_WORDS = ("revert", "invalid opcode", "out of gas", "stack", "execution error", "invalid jump", "bad instruction",
                "abort", "invalid instruction", "invalidfeopcode", "evm error", "opcode", "vm execution", "gas required exceeds", "write protection", "not found")
TRANSIENT_WORDS = ("rate", "limit", "timeout", "timed out", "temporar", "try again", "capacity", "overload", "busy",
                   "too many", "unavailable", "header not found", "missing trie node", "429", "internal error", "service")


def classify_error(err):
    code = err.get("code")
    msg = str(err.get("message", "")).lower()
    data = str(err.get("data", "")).lower()
    if code == 3 or any(w in msg for w in REVERT_WORDS) or "revert" in data:
        if "limit" in msg and "gas" not in msg:
            return "transient"
        return "revert"
    if any(w in msg for w in TRANSIENT_WORDS) or code in (-32005, -32097, 429):
        return "transient"
    return "permanent"


class Client:
    def __init__(self, chain, out_root, block_is_head=True, endpoints=None, verbose=False):
        self.chain = chain
        self.cfg = CHAINS[chain]
        self.out_root = out_root
        self.verbose = verbose
        eps = endpoints or (self.cfg["endpoints"] if block_is_head else self.cfg["archive"] + self.cfg["endpoints"])
        # dedupe keep order
        seen, self.eps = set(), []
        for u, b in eps:
            if u not in seen:
                seen.add(u)
                self.eps.append([u, b, 0])  # url, max batch, hard-failure count
        self.trace_eps = list(self.cfg["trace"])
        self.stats = {"requests": 0, "http_calls": 0, "retries": 0}
        self.refusals, self.successes = {}, {}
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ low level
    BLK = "@BLK"   # sentinel block param: replaced by a fresh head-2 block at SEND time (after the rate limiter wait)

    def head_hex(self):
        """head-2 block (hex), cached 1.5 s (used for the rolling mode of pruned public RPCs)."""
        t, h = getattr(self, "_headc", (0.0, None))
        if h is None or time.time() - t > 1.5:
            r = self._post(self.eps[0][0], {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
            try:
                h = hex(int(r.json()["result"], 16) - 2)
                self._headc = (time.time(), h)
            except Exception:  # noqa: BLE001
                if h is None:
                    raise
        return h

    def _post(self, url, payload, timeout=60):
        limiter(url).wait()
        limiter(url + "#calls", CALL_RPS).wait(len(payload) if isinstance(payload, list) else 1)
        items = payload if isinstance(payload, list) else [payload]
        if any(self.BLK in (it.get("params") or []) for it in items):
            blk = self.head_hex()
            for it in items:
                it["params"] = [blk if x == self.BLK else x for x in it["params"]]
        with self.lock:
            self.stats["http_calls"] += 1
        r = session().post(url, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=timeout)
        return r

    def _send_chunk(self, chunk):
        """chunk: list of (method, params). Returns list of (response_obj|None, endpoint, err_kind, err_msg)."""
        n = len(chunk)
        orig = [p for _, p in chunk]  # unresolved params (a retry must pick a fresh block again)
        out = [None] * n
        pending = list(range(n))
        bad_for = {i: set() for i in range(n)}  # endpoints that refused this request for state/archive reasons
        attempt = 0
        ep_i = 0
        while pending and attempt < 9:
            live = [e for e in self.eps if e[2] < 4] or self.eps
            if attempt == 0 and len(live) > 1:  # spread load: endpoint whose limiters free up first
                ep = min(live, key=lambda e: max(limiter(e[0]).next, limiter(e[0] + "#calls", CALL_RPS).next))
            else:
                ep = live[min(ep_i, len(live) - 1)]
            first = pending[0]
            if ep[0] in bad_for[first]:
                alt = [e for e in live if e[0] not in bad_for[first]]
                if not alt:  # every endpoint refused this request: give up on it
                    out[first] = (None, ep[0], "permanent", "all endpoints refused (archive/state unavailable)")
                    pending = [i for i in pending if out[i] is None]
                    continue
                ep = alt[0]
            url, maxb = ep[0], ep[1]
            sub = [i for i in pending if url not in bad_for[i]][:maxb]
            payload = [{"jsonrpc": "2.0", "id": k, "method": chunk[i][0], "params": orig[i]} for k, i in enumerate(sub)]
            if len(payload) == 1:
                payload_send = payload[0]
            else:
                payload_send = payload
            try:
                r = self._post(url, payload_send)
                for k, i in enumerate(sub):  # params may have been resolved at send time (rolling block sentinel)
                    chunk[i] = (chunk[i][0], payload[k]["params"])
                if r.status_code in (429, 502, 503, 504) or (r.status_code == 500 and "Batch of more than" in r.text):
                    if "Batch of more than" in r.text and ep[1] > 1:
                        ep[1] = max(1, min(ep[1] - 1, 3))  # learned batch cap
                        continue
                    raise RuntimeError(f"http {r.status_code}: {r.text[:120]}")
                body = r.json()
                if isinstance(body, dict):
                    body = [body]
                byid = {b.get("id"): b for b in body if isinstance(b, dict)}
                for k, i in enumerate(sub):
                    b = byid.get(k)
                    if b is None:
                        continue
                    if "error" in b:
                        kind = classify_error(b["error"])
                        msg = str(b["error"].get("message"))[:200]
                        if kind == "transient":
                            continue
                        if kind == "permanent" and any(w in msg.lower() for w in (
                                "archive", "not supported", "does not exist", "not available", "unknown state",
                                "first available", "pruned", "missing trie", "state is not", "upgrade to")):
                            bad_for[i].add(url)
                            self.refusals[url] = self.refusals.get(url, 0) + 1
                            if self.refusals[url] > 15 and not self.successes.get(url):
                                ep[2] = 4  # this endpoint has never served a request and keeps refusing: stop using it
                            continue
                        out[i] = (b, url, kind, msg)
                    else:
                        out[i] = (b, url, "ok", None)
                        self.successes[url] = self.successes.get(url, 0) + 1
                pending = [i for i in pending if out[i] is None]
            except Exception as e:  # noqa: BLE001
                ep[2] += 1 if "http" not in str(e) else 0
                self.stats["retries"] += 1
                last = repr(e)[:160]
                if self.verbose:
                    print("rpc error", url, last)
            if pending:
                attempt += 1
                self.stats["retries"] += 1
                if attempt % 2 == 0:
                    ep_i += 1  # switch endpoint every second failed attempt
                time.sleep(min(1.5 * attempt, 8))
        for i in pending:
            out[i] = (None, self.eps[0][0], "transient", "retries exhausted")
        return out

    # ------------------------------------------------------------------ public
    def batch(self, ev, reqs, block=None):
        """reqs: list of (method, params). Saves evidence; returns list[Result] aligned with reqs."""
        results = []
        # chunk by the smallest max batch among live endpoints that we might use first
        step = max(1, min(20, self.eps[0][1]))
        i = 0
        while i < len(reqs):
            step = max(1, min(20, [e for e in self.eps if e[2] < 4][0][1] if [e for e in self.eps if e[2] < 4] else 1))
            chunk = reqs[i:i + step]
            i += step
            with self.lock:
                self.stats["requests"] += len(chunk)
            for (method, params), res in zip(chunk, self._send_chunk(chunk)):
                b, url, kind, msg = res
                request = {"jsonrpc": "2.0", "method": method, "params": params}
                if block == self.BLK:
                    block_i = params[-1] if isinstance(params[-1], str) and params[-1].startswith("0x") else None
                else:
                    block_i = block
                if b is None:
                    ref = ev.add(self.chain, url, block_i, request, {"error": {"message": msg, "transport": True}})
                    results.append(Result("error", msg=msg, ev=ref))
                    continue
                orig_sha = sha(b)
                stored = b
                if method == "eth_getCode" and isinstance(b.get("result"), str) and len(b["result"]) > OMIT_CODE_OVER:
                    stored = {"id": b.get("id"), "jsonrpc": "2.0",
                              "result_omitted": True, "result_len": len(b["result"]),
                              "code_keccak": "0x" + keccak(bytes.fromhex(b["result"][2:])).hex()}
                ref = ev.add(self.chain, url, block_i, request, stored, original_sha=orig_sha)
                if kind == "ok":
                    results.append(Result("ok", value=b.get("result"), ev=ref))
                elif kind == "revert":
                    results.append(Result("revert", msg=msg, ev=ref))
                else:
                    results.append(Result("error", msg=msg, ev=ref))
        return results

    def one(self, ev, method, params, block=None):
        return self.batch(ev, [(method, params)], block)[0]

    def trace_call(self, ev, to, data, block, tracer="callTracer", extra=None):
        """debug_traceCall on a trace-capable endpoint (single request, not batched). Returns Result."""
        cfg = {"tracer": tracer}
        if block == self.BLK:
            block = self.head_hex()
        if extra:
            cfg.update(extra)
        params = [{"to": to, "data": data}, block, cfg]
        request = {"jsonrpc": "2.0", "method": "debug_traceCall", "params": params}
        if not self.trace_eps:
            ref = ev.add(self.chain, None, block, request, {"error": {"message": "no trace endpoint configured for chain"}})
            return Result("error", msg="no trace endpoint for chain", ev=ref)
        last = "?"
        for url in self.trace_eps:
            for attempt in range(6):
                try:
                    r = self._post(url, {"jsonrpc": "2.0", "id": 1, "method": "debug_traceCall", "params": params}, timeout=90)
                    b = r.json()
                    if "error" in b:
                        last = str(b["error"].get("message"))[:200]
                        if classify_error(b["error"]) == "transient" or "rate limit" in last.lower():
                            time.sleep(3 * (attempt + 1))
                            continue
                        ref = ev.add(self.chain, url, block, request, b)
                        return Result("error", msg=last, ev=ref)
                    ref = ev.add(self.chain, url, block, request, b)
                    return Result("ok", value=b.get("result"), ev=ref)
                except Exception as e:  # noqa: BLE001
                    last = repr(e)[:160]
                    time.sleep(2 * (attempt + 1))
        ref = ev.add(self.chain, None, block, request, {"error": {"message": last, "transport": True}})
        return Result("error", msg=last, ev=ref)


    def trace_tx(self, ev, txhash):
        """debug_traceTransaction (callTracer) on a trace-capable endpoint. Returns Result (value = call tree)."""
        params = [txhash, {"tracer": "callTracer"}]
        request = {"jsonrpc": "2.0", "method": "debug_traceTransaction", "params": params}
        last = "no trace endpoint for chain"
        for url in self.trace_eps:
            for attempt in range(4):
                try:
                    r = self._post(url, {"jsonrpc": "2.0", "id": 1, "method": "debug_traceTransaction", "params": params}, timeout=90)
                    b = r.json()
                    if "error" in b:
                        last = str(b["error"].get("message"))[:200]
                        if classify_error(b["error"]) == "transient" or "rate limit" in last.lower():
                            time.sleep(3 * (attempt + 1))
                            continue
                        ref = ev.add(self.chain, url, None, request, b)
                        last_ref = ref
                        break
                    ref = ev.add(self.chain, url, None, request, b)
                    return Result("ok", value=b.get("result"), ev=ref)
                except Exception as e:  # noqa: BLE001
                    last = repr(e)[:160]
                    time.sleep(2 * (attempt + 1))
        ref = ev.add(self.chain, None, None, request, {"error": {"message": last}})
        return Result("error", msg=last, ev=ref)


# ---------------------------------------------------------------------------------------------------------------
# Etherscan v2 (logs only: RoleGranted / Rely lookups). Evidence request has the apikey redacted.
# ---------------------------------------------------------------------------------------------------------------
_ES_KEY = None


def etherscan_key():
    global _ES_KEY
    if _ES_KEY is None:
        k = os.environ.get("ETHERSCAN_API_KEY")
        _ES_KEY = k or ""
    return _ES_KEY


_ES_LOCK = threading.Lock()
_ES_STATE = {"interval": 0.7, "last": 0.0}


def etherscan_get(ev, chain_id, params, retries=14):
    """GET api.etherscan.io/v2/api with params; returns Result whose value is the parsed JSON body.
    One request in flight at a time (the free key is shared across research clients): adaptive spacing, backs off on
    'Max calls per sec'. The apikey is redacted in the saved evidence."""
    key = etherscan_key()
    q = dict(chainid=chain_id, **params)
    request = {"http": "GET", "url": "https://api.etherscan.io/v2/api", "params": dict(q, apikey="REDACTED")}
    if not key:
        ref = ev.add("?", "etherscan", None, request, {"error": "no ETHERSCAN_API_KEY"})
        return Result("error", msg="no etherscan key", ev=ref)
    last = "?"
    import fcntl
    with _ES_LOCK, open("/tmp/w3_etherscan.lock", "a+") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)  # also serialises our own concurrent processes on the shared key
        for a in range(retries):
            try:
                wait = _ES_STATE["last"] + _ES_STATE["interval"] - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                r = session().get("https://api.etherscan.io/v2/api", params=dict(q, apikey=key), timeout=60)
                _ES_STATE["last"] = time.monotonic()
                b = r.json()
                res = b.get("result")
                txt = res if isinstance(res, str) else ""
                if "rate limit" in txt.lower() or "max calls" in txt.lower():
                    last = txt
                    _ES_STATE["interval"] = min(_ES_STATE["interval"] * 1.5, 4.0)
                    continue
                _ES_STATE["interval"] = max(_ES_STATE["interval"] * 0.9, 0.5)
                ref = ev.add(str(chain_id), "etherscan-v2", None, request, b)
                if b.get("status") == "1" or (b.get("message") == "No records found"):
                    return Result("ok", value=b, ev=ref)
                return Result("error", msg=f"{b.get('message')}: {txt[:160]}", ev=ref)
            except Exception as e:  # noqa: BLE001
                last = repr(e)[:160]
                _ES_STATE["last"] = time.monotonic()
                time.sleep(1.5)
    ref = ev.add(str(chain_id), "etherscan-v2", None, request, {"error": last, "transport": True})
    return Result("error", msg=last, ev=ref)
