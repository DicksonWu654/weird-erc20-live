"""Rate-limited, batching, failover JSON-RPC client with per-call evidence capture.

Design points
* One `Endpoint` per URL. Each has its own token-bucket-ish limiter (default 5 HTTP requests/s; AIMD: a 429 doubles
  the spacing, quiet periods shrink it back) and an adaptive max batch size (drpc free tier = 3, publicnode = 20).
* `Pool.send(items)` splits the items into batches, routes each batch to the endpoint that is free soonest (so two
  endpoints double throughput), retries with exponential backoff, and fails over on transport errors / rate limits /
  "archive requests require a token" / pruned-state errors.
* Every item that is finally returned is recorded in the `Recorder` with the FULL request object, the FULL response
  item, the endpoint url, retrieved_at, latency and sha256 of the canonical response JSON. That is the evidence file.
* Execution errors (revert, out of gas ...) are NOT failures of the transport: they are returned as data.
"""
import asyncio
import gzip
import hashlib
import json
import re
import time
from datetime import datetime, timezone

import aiohttp

RATE_LIMIT_PAT = re.compile(
    r"rate.?limit|too many|capacity|temporar|timed? ?out|timeout|try again|unavailable|overload|busy|"
    r"exceeded|throttl|limit exceeded|429|upstream|bad gateway|gateway|internal error|server error", re.I)
STATE_PAT = re.compile(
    r"missing trie node|header not found|state.*not available|block .*not found|too old|pruned|historical state|"
    r"archive|required historical|no state available|state is not available|unknown block|"
    r"not found|distance to target block exceeds|ancient", re.I)
STATE_RETRY_PAT = re.compile(
    r"missing trie node|header not found|pruned|archive|ancient|historical|distance to target|unknown state|"
    r"first available state|state.*not available|no state available|unknown block|block .*not found|not available", re.I)
BATCH_PAT = re.compile(r"batch of more than (\d+)|max batch|batch size|batch.*not allowed|batch.*too large|"
                       r"batch requests? (?:are )?not|exceeds? .*batch", re.I)


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_obj(obj):
    return hashlib.sha256(canon(obj).encode()).hexdigest()


class Recorder:
    """Collects every RPC call made while probing one token; written as <token>.json.gz."""

    def __init__(self, chain, token):
        self.chain, self.token = chain, token
        self.calls = []
        self.attempts = []   # transport-level failures (retries, failovers), kept for transparency

    def add(self, tag, url, request, response, latency_ms, http_status):
        response = json.loads(json.dumps(response))   # own copy: the caller later adds _cid/_url to its dict
        cid = len(self.calls)
        self.calls.append({
            "cid": cid, "tag": tag, "url": url, "retrieved_at": utcnow(), "latency_ms": latency_ms,
            "http_status": http_status, "request": request, "response": response,
            "response_sha256": sha256_obj(response),
        })
        return cid

    def note_attempt(self, url, what):
        self.attempts.append({"at": utcnow(), "url": url, "what": what})

    def save(self, path, extra=None):
        doc = {"chain": self.chain, "token": self.token, "calls": self.calls, "transport_failures": self.attempts}
        if extra:
            doc.update(extra)
        data = json.dumps(doc, separators=(",", ":")).encode()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wb", compresslevel=6) as f:
            f.write(data)
        tmp.replace(path)
        return hashlib.sha256(data).hexdigest()


class Endpoint:
    def __init__(self, url, rps=5.0, max_batch=20, archive=None):
        self.url = url
        self.base_interval = 1.0 / rps
        self.interval = self.base_interval
        self.max_batch = max_batch
        self.archive = archive       # None unknown / True / False
        self.next_ok = 0.0
        self.lock = asyncio.Lock()
        self.fail_streak = 0
        self.n_http = 0
        self.n_429 = 0
        self.last_429 = 0.0
        self.dead_until = 0.0

    async def acquire(self):
        async with self.lock:
            now = time.monotonic()
            t = max(now, self.next_ok)
            self.next_ok = t + self.interval
        if t > now:
            await asyncio.sleep(t - now)

    def penalize(self, retry_after=None):
        self.n_429 += 1
        self.last_429 = time.monotonic()
        self.interval = min(max(self.interval * 2, 0.4), 4.0)
        if retry_after:
            self.next_ok = max(self.next_ok, time.monotonic() + retry_after)
        else:
            self.next_ok = max(self.next_ok, time.monotonic() + self.interval * 2)

    def reward(self):
        # decay back toward the base interval after 20 quiet seconds
        if self.interval > self.base_interval and time.monotonic() - self.last_429 > 20:
            self.interval = max(self.base_interval, self.interval * 0.8)

    def score(self):
        return max(self.next_ok, time.monotonic())


class Pool:
    def __init__(self, chain, endpoints, session, timeout=60):
        self.chain = chain
        self.eps = endpoints
        self.session = session
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    # -- endpoint choice ------------------------------------------------------------------------------------
    def choose(self, need_archive=False, exclude=(), reserved=None):
        now = time.monotonic()
        pool = [e for e in self.eps if e.url not in exclude and e.dead_until <= now]
        if need_archive:
            pool = [e for e in pool if e.archive is not False]
            known = [e for e in pool if e.archive is True]
            if known:
                pool = known
        if not pool:
            return None
        # earliest completion of one more request; `reserved` = requests this call already queued per endpoint, so a big
        # batch spreads over endpoints and the big-batch endpoint (publicnode: 20) takes the larger share of the items
        rs = reserved or {}
        return min(pool, key=lambda e: e.score() + (rs.get(e.url, 0) + 1) * e.interval)

    # -- public ---------------------------------------------------------------------------------------------
    async def send(self, items, rec=None, need_archive=False):
        """items: list of (method, params, tag). Returns list of response dicts (aligned), each with 'result' or
        'error' plus our own '_cid' (evidence call id) and '_url'. Transport-level exhaustion yields
        {'error': {'code': -1, 'message': 'transport: ...'}, '_transport_error': True}."""
        out = [None] * len(items)
        idx = list(range(len(items)))
        tasks = []
        reserved = {}
        while idx:
            ep = self.choose(need_archive, reserved=reserved)
            if ep is None:
                for i in idx:
                    out[i] = {"error": {"code": -1, "message": "transport: no endpoint available"},
                              "_transport_error": True}
                break
            take, idx = idx[: ep.max_batch], idx[ep.max_batch:]
            reserved[ep.url] = reserved.get(ep.url, 0) + 1
            tasks.append(asyncio.create_task(self._run_chunk(ep, [(i, items[i]) for i in take], out, rec, need_archive)))
        if tasks:
            await asyncio.gather(*tasks)
        return out

    async def one(self, method, params, tag="", rec=None, need_archive=False):
        return (await self.send([(method, params, tag)], rec, need_archive))[0]

    # -- internals ------------------------------------------------------------------------------------------
    async def _run_chunk(self, ep, chunk, out, rec, need_archive):
        tried = set()
        last_err = "unknown"
        for attempt in range(7):
            if attempt:
                await asyncio.sleep(min(1.5 * 2 ** (attempt - 1), 20))
            cur = ep if (attempt < 2 and ep.url not in tried and ep.dead_until <= time.monotonic()) else None
            if cur is None:
                cur = self.choose(need_archive, exclude=tried) or self.choose(need_archive)
            if cur is None:
                break
            if len(chunk) > cur.max_batch:       # re-split for a smaller-batch endpoint
                head, tail = chunk[: cur.max_batch], chunk[cur.max_batch:]
                await asyncio.gather(self._run_chunk(cur, head, out, rec, need_archive),
                                     self._run_chunk(cur, tail, out, rec, need_archive))
                return
            res = await self._http(cur, chunk, rec)
            kind = res[0]
            if kind == "ok":
                _, items_resp, latency, status = res
                bad = None
                for (i, (m, p, tag)), r in zip(chunk, items_resp):
                    msg = (r.get("error") or {}).get("message", "") if isinstance(r, dict) else ""
                    if "error" in r and not r["error"].get("data"):
                        if RATE_LIMIT_PAT.search(msg) and not re.search(r"revert|execution aborted|out of gas|gas", msg, re.I):
                            bad = ("rate", msg)
                            break
                        if STATE_RETRY_PAT.search(msg) and "revert" not in msg.lower():
                            bad = ("state", msg)
                            break
                if bad is None:
                    cur.fail_streak = 0
                    cur.reward()
                    for (i, (m, p, tag)), r in zip(chunk, items_resp):
                        req = {"jsonrpc": "2.0", "id": i, "method": m, "params": p}
                        resp = {k: v for k, v in r.items()}
                        cid = rec.add(tag, cur.url, req, resp, latency, status) if rec else -1
                        resp["_cid"] = cid
                        resp["_url"] = cur.url
                        out[i] = resp
                    return
                kind, last_err = bad
                if kind == "rate":
                    cur.penalize()
                else:
                    if cur.archive is None or need_archive:
                        cur.archive = False if need_archive else cur.archive
                tried.add(cur.url)
                if rec:
                    rec.note_attempt(cur.url, f"item-level {kind}: {last_err[:160]}")
                continue
            if kind == "batch_limit":
                cur.max_batch = max(1, res[1])
                if rec:
                    rec.note_attempt(cur.url, f"batch limit -> {cur.max_batch}")
                continue
            if kind == "archive":
                cur.archive = False
                tried.add(cur.url)
                last_err = res[1]
                if rec:
                    rec.note_attempt(cur.url, f"archive refused: {last_err[:160]}")
                continue
            # transport / http failure
            last_err = res[1]
            cur.fail_streak += 1
            if cur.fail_streak >= 4:
                cur.dead_until = time.monotonic() + 15
            tried.add(cur.url)
            if rec:
                rec.note_attempt(cur.url, f"{kind}: {last_err[:160]}")
        for i, _ in chunk:
            out[i] = {"error": {"code": -1, "message": f"transport: {last_err[:200]}"}, "_transport_error": True}

    async def _http(self, ep, chunk, rec):
        reqs = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p, tag) in chunk]
        body = reqs[0] if len(reqs) == 1 else reqs
        await ep.acquire()
        t0 = time.monotonic()
        ep.n_http += 1
        try:
            async with self.session.post(ep.url, json=body, timeout=self.timeout,
                                         headers={"User-Agent": "weird-erc20-live/0.1 (research; contact via SEAL)"}) as r:
                text = await r.text()
                status = r.status
                ra = r.headers.get("Retry-After")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return ("transport", f"{type(e).__name__}: {e}")
        latency = int((time.monotonic() - t0) * 1000)
        if status == 429:
            ep.penalize(float(ra) if ra and ra.replace(".", "").isdigit() else None)
            return ("http429", text[:200])
        if status in (403, 401) and re.search(r"archive", text, re.I):
            return ("archive", text[:200])
        try:
            j = json.loads(text)
        except Exception:
            return ("http", f"HTTP {status}: {text[:200]}")
        # whole-batch rejection ({"error": ...} object, or list of identical errors)
        if isinstance(j, dict) and "error" in j and len(reqs) >= 1 and "result" not in j and j.get("id") in (None, 0) \
                or (isinstance(j, dict) and "error" in j and isinstance(body, list)):
            msg = str(j["error"].get("message", "")) if isinstance(j["error"], dict) else str(j["error"])
            m = BATCH_PAT.search(msg)
            if m and len(reqs) > 1:
                n = int(m.group(1)) if m.group(1) else max(1, len(reqs) // 2)
                return ("batch_limit", n)
        if isinstance(j, list):
            by = {x.get("id"): x for x in j if isinstance(x, dict)}
            # drpc-style batch rejection: every item is an error with the batch message
            first_err = next((x for x in j if isinstance(x, dict) and "error" in x), None)
            if first_err and len(reqs) > 1:
                msg = str(first_err["error"].get("message", ""))
                m = BATCH_PAT.search(msg)
                if m:
                    n = int(m.group(1)) if m.group(1) else max(1, len(reqs) // 2)
                    return ("batch_limit", min(n, len(reqs) - 1) if n >= len(reqs) else n)
            resp = []
            for i, _ in chunk:
                x = by.get(i)
                if x is None:
                    x = {"error": {"code": -1, "message": "missing response for id in batch"}}
                resp.append({k: v for k, v in x.items() if k not in ("id", "jsonrpc")})
            if status >= 500:
                return ("http", f"HTTP {status}: {text[:200]}")
            return ("ok", resp, latency, status)
        if isinstance(j, dict):
            if status == 429 or (status >= 500 and "result" not in j):
                return ("http", f"HTTP {status}: {text[:200]}")
            if "error" in j and len(reqs) == 1:
                msg = str(j["error"].get("message", "")) if isinstance(j["error"], dict) else str(j["error"])
                if re.search(r"archive requests require", msg, re.I):
                    return ("archive", msg)
            if len(reqs) == 1:
                return ("ok", [{k: v for k, v in j.items() if k not in ("id", "jsonrpc")}], latency, status)
            msg = str(j.get("error", ""))[:200]
            return ("http", f"unexpected object for batch: {msg}")
        return ("http", f"unexpected body: {text[:200]}")
