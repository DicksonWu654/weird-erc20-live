"""Polite JSON-RPC client for the census (<=RPS req/s per process, 429/5xx backoff, raw evidence log).

Every request/response is appended to data/raw/census/<stream>.jsonl.gz as one line:
  {url, method, params, retrieved_at, status, sha256 (of the response body bytes), response}
`store`: 'full' keeps the response, 'hash' keeps only sha256+size (use for huge trace_block bodies that are
re-fetchable from an archive node at a pinned block), or a callable(result)->extract to keep a derived subset.
"""
import gzip, hashlib, json, os, pathlib, threading, time
import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAWDIR = ROOT / "data/raw/census"
DRPC = "https://eth.drpc.org"


class RPC:
    def __init__(self, url=DRPC, rps=4.0, stream="rpc", timeout=120):
        self.url, self.min_dt, self.timeout = url, 1.0 / rps, timeout
        self.stream = stream
        self.lock = threading.Lock()
        self.next_t = 0.0
        self.sess = requests.Session()
        RAWDIR.mkdir(parents=True, exist_ok=True)
        self.logf = gzip.open(RAWDIR / f"{stream}.jsonl.gz", "at")
        self.nreq = 0

    def _wait(self):
        with self.lock:
            now = time.time()
            t = max(now, self.next_t)
            self.next_t = t + self.min_dt
        if t > now:
            time.sleep(t - now)

    def call(self, method, params, store="full", retries=6, meta=False):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        err = None
        for i in range(retries):
            self._wait()
            try:
                r = self.sess.post(self.url, json=body, timeout=self.timeout)
                raw = r.content
                if r.status_code in (429, 502, 503, 504):
                    err = f"http {r.status_code}"; time.sleep(2 ** i); continue
                j = json.loads(raw)
            except Exception as e:  # noqa: BLE001
                err = repr(e)[:200]; time.sleep(2 ** i); continue
            if "error" in j:
                msg = str(j["error"])
                self._log(method, params, r.status_code, raw, j, store="full")
                if any(k in msg.lower() for k in ("rate", "limit exceeded", "timeout", "temporar", "capacity")) and "ranges over" not in msg:
                    err = msg[:200]; time.sleep(2 ** i); continue
                raise RuntimeError(f"{method} {msg[:300]}")
            self._log(method, params, r.status_code, raw, j, store)
            if meta:
                return j["result"], hashlib.sha256(raw).hexdigest()
            return j["result"]
        raise RuntimeError(f"{method} failed after retries: {err}")

    def batch(self, calls, retries=6):
        """calls: list of (method, params), len<=3 on the drpc free plan. Returns list of (result|None, error|None), full responses logged."""
        body = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
        err = None
        for i in range(retries):
            self._wait()
            try:
                r = self.sess.post(self.url, json=body, timeout=self.timeout)
                raw = r.content
                if r.status_code in (429, 502, 503, 504):
                    err = f"http {r.status_code}"; time.sleep(2 ** i); continue
                js = sorted(json.loads(raw), key=lambda x: x["id"])
            except Exception as e:  # noqa: BLE001
                err = repr(e)[:200]; time.sleep(2 ** i); continue
            rec = {"url": self.url, "method": "batch", "params": body, "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "status": r.status_code, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "response": js}
            with self.lock:
                self.logf.write(json.dumps(rec, separators=(",", ":")) + "\n"); self.nreq += 1
            return [(x.get("result"), x.get("error")) for x in js]
        raise RuntimeError(f"batch failed after retries: {err}")

    def _log(self, method, params, status, raw, j, store):
        rec = {"url": self.url, "method": method, "params": params,
               "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": status,
               "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        if store == "full":
            rec["response"] = j
        elif callable(store):
            rec["extract"] = store(j.get("result"))
        with self.lock:
            self.logf.write(json.dumps(rec, separators=(",", ":")) + "\n")
            self.nreq += 1
            if self.nreq % 50 == 0:
                self.logf.flush()

    def close(self):
        self.logf.close()
