"""Async, rate-limited, micro-batching JSON-RPC client (aiohttp).

* one `Endpoint` per URL: global token-bucket (default 5 HTTP req/s), JSON-RPC batching up to `max_batch`
  (drpc free plan allows 3 per batch, publicnode allows 20), bounded in-flight requests,
  429 / 5xx / timeout retries with exponential backoff.
* `await ep.request(method, params)` returns the *raw* JSON-RPC response object
  ({"jsonrpc","id","result"|"error"}) plus the request, so callers can save full request+response evidence.
* Never sends transactions: only read/simulation methods are whitelisted.
"""
import asyncio, json, time, random
import aiohttp

READ_ONLY = {"eth_call", "eth_simulateV1", "eth_getLogs", "eth_getBlockByNumber", "eth_blockNumber", "eth_chainId",
             "eth_getCode", "eth_getBalance", "eth_getTransactionByHash", "eth_getTransactionReceipt",
             "eth_getStorageAt", "eth_getBlockReceipts", "debug_traceCall"}


class RpcTransportError(Exception):
    pass


class Endpoint:
    def __init__(self, url, name=None, rps=5.0, max_batch=1, inflight=4, timeout=60, max_retries=6, linger=0.02):
        self.url, self.name = url, name or url
        self.rps, self.max_batch, self.timeout, self.max_retries, self.linger = rps, max_batch, timeout, max_retries, linger
        self.inflight = asyncio.Semaphore(inflight)
        self._q = None
        self._next_slot = 0.0
        self._lock = asyncio.Lock()
        self._sess = None
        self._runner = None
        self.stats = {"http": 0, "items": 0, "retries": 0, "429": 0, "errors": 0}
        self._id = 0
        self._penalty_until = 0.0

    async def _start(self):
        if self._sess is None:
            self._sess = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout),
                                               headers={"User-Agent": "weird-erc20-sim/0.1 (research; read-only)"})
            self._q = asyncio.Queue()
            self._runner = asyncio.create_task(self._loop())

    async def close(self):
        if self._runner:
            self._runner.cancel()
        if self._sess:
            await self._sess.close()
        self._sess = None

    async def _slot(self):
        async with self._lock:
            now = time.monotonic()
            wait = max(self._next_slot, self._penalty_until) - now
            self._next_slot = max(now, self._next_slot) + 1.0 / self.rps
            if self._penalty_until > now:
                self._next_slot = max(self._next_slot, self._penalty_until)
        if wait > 0:
            await asyncio.sleep(wait)

    async def request(self, method, params):
        if method not in READ_ONLY:
            raise ValueError(f"method {method} not allowed (read-only client)")
        await self._start()
        fut = asyncio.get_running_loop().create_future()
        self._id += 1
        await self._q.put(({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}, fut))
        return await fut

    async def _loop(self):
        while True:
            first = await self._q.get()
            items = [first]
            if self.max_batch > 1:
                await asyncio.sleep(self.linger)
                while len(items) < self.max_batch and not self._q.empty():
                    items.append(self._q.get_nowait())
            await self.inflight.acquire()
            await self._slot()
            asyncio.create_task(self._send(items))

    async def _send(self, items):
        try:
            reqs = [r for r, _ in items]
            payload = reqs if (self.max_batch > 1 and len(reqs) > 1) else (reqs[0] if len(reqs) == 1 else reqs)
            delay = 1.0
            last = None
            for attempt in range(self.max_retries + 1):
                try:
                    self.stats["http"] += 1
                    self.stats["items"] += len(reqs)
                    async with self._sess.post(self.url, json=payload) as r:
                        txt = await r.text()
                        if r.status == 429 or r.status >= 500:
                            self.stats["429" if r.status == 429 else "errors"] += 1
                            # drpc batch-limit error is a 500 with code 31: not retryable
                            if "Batch of more than" in txt:
                                raise RpcTransportError("batch limit: " + txt[:200])
                            raise RpcTransportError(f"http {r.status}: {txt[:200]}")
                        data = json.loads(txt)
                    resp = data if isinstance(data, list) else [data]
                    byid = {x.get("id"): x for x in resp if isinstance(x, dict)}
                    # transient JSON-RPC level rate limits -> retry the whole HTTP request
                    transient = [x for x in resp if isinstance(x, dict) and "error" in x and
                                 any(s in json.dumps(x["error"]).lower() for s in ("rate limit", "too many", "capacity", "timeout", "try again"))]
                    if transient and attempt < self.max_retries:
                        raise RpcTransportError("transient rpc error: " + json.dumps(transient[0])[:200])
                    for req, fut in items:
                        rs = byid.get(req["id"])
                        if rs is None:
                            fut.set_exception(RpcTransportError("missing response id")) if not fut.done() else None
                        elif not fut.done():
                            fut.set_result({"request": req, "response": rs, "endpoint": self.name,
                                            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                    return
                except (aiohttp.ClientError, asyncio.TimeoutError, RpcTransportError, json.JSONDecodeError) as e:
                    last = e
                    self.stats["retries"] += 1
                    if isinstance(e, RpcTransportError) and "batch limit" in str(e):
                        break
                    if attempt < self.max_retries:
                        d = delay * (1 + random.random() * 0.3)
                        self._penalty_until = max(self._penalty_until, time.monotonic() + d)
                        await asyncio.sleep(d)
                        delay = min(delay * 2, 30)
            for _, fut in items:
                if not fut.done():
                    fut.set_exception(RpcTransportError(f"{self.name}: {last!r}"))
        finally:
            self.inflight.release()


def hx(n):
    return hex(n)


def result_of(raw):
    """(ok, result_or_error) from a raw response record."""
    r = raw["response"]
    if "error" in r:
        return False, r["error"]
    return True, r["result"]
