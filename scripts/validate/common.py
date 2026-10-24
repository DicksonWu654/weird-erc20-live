"""Shared helpers for W6 validation clients: raw-evidence envelopes + a polite rate limiter.

Every provider query is saved as one JSON envelope:
  data/raw/validate/<provider>/<chain_id>/<address>/<UTC-compact-ts>.json
  {provider, endpoint, url, params, request_headers (no secrets), status, response_headers,
   body_text, body_sha256, retrieved_at (UTC ISO, second precision), retrieved_at_epoch_ms,
   elapsed_ms, attempt, error}
plus one line per query in data/raw/validate/MANIFEST.jsonl (path, url, status, sha256, retrieved_at).
Nothing is overwritten: re-querying the same token later adds a new timestamped file (latest-state answers
drift, so we keep every observation). `latest_envelope()` / `nearest_envelope()` pick among them.
"""
import hashlib, json, os, pathlib, re, threading, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "validate"
VAL = ROOT / "data" / "validate"
MANIFEST = RAW / "MANIFEST.jsonl"
UA = f"weird-erc20-research/0.1 (academic measurement study; {os.environ.get('WEIRD_ERC20_CONTACT', 'Weird ERC-20 Live research project')})"
_mlock = threading.Lock()


def utcnow():
    t = time.time()
    return t, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def norm_addr(a):
    return a.strip().lower()


def env_dir(provider, chain, addr):
    return RAW / provider / str(chain) / norm_addr(addr)


def save_envelope(provider, chain, addr, env):
    d = env_dir(provider, chain, addr)
    d.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(env["retrieved_at_epoch_ms"] / 1000))
    p = d / f"{ts}.json"
    n = 1
    while p.exists():  # same-second re-query: never overwrite
        p = d / f"{ts}_{n}.json"
        n += 1
    p.write_text(json.dumps(env, indent=1))
    rec = {"path": str(p.relative_to(ROOT)), "provider": provider, "chain": chain, "address": norm_addr(addr),
           "url": env["url"], "status": env["status"], "sha256": env["body_sha256"],
           "retrieved_at": env["retrieved_at"]}
    with _mlock, open(MANIFEST, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return p


def list_envelopes(provider, chain, addr):
    d = env_dir(provider, chain, addr)
    return sorted(d.glob("*.json")) if d.exists() else []


def load_envelope(p):
    return json.loads(pathlib.Path(p).read_text())


def latest_envelope(provider, chain, addr, ok_only=False):
    for p in reversed(list_envelopes(provider, chain, addr)):
        e = load_envelope(p)
        if ok_only and (not e.get("status") or e["status"] >= 500 or e["status"] == 429
                        or '"code":4029' in (e.get("body_text") or "").replace(" ", "")):
            continue
        return p, e
    return None, None


def nearest_envelope(provider, chain, addr, epoch_ms):
    best = None
    for p in list_envelopes(provider, chain, addr):
        e = load_envelope(p)
        d = abs(e["retrieved_at_epoch_ms"] - epoch_ms)
        if best is None or d < best[0]:
            best = (d, p, e)
    return (best[1], best[2]) if best else (None, None)


class RateLimiter:
    """Min-interval limiter shared by threads; `penalize(sec)` pushes the next slot out (429/backoff)."""

    def __init__(self, min_interval):
        self.min_interval = min_interval
        self.next_ok = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            t = max(now, self.next_ok)
            self.next_ok = t + self.min_interval
        if t > now:
            time.sleep(t - now)

    def penalize(self, sec):
        with self.lock:
            self.next_ok = max(self.next_ok, time.time() + sec)


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()
