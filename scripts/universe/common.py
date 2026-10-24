"""Shared helpers for W2 (token universe): polite HTTP with raw-evidence saving, chain config."""
import hashlib, json, os, pathlib, re, sys, time, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
UNI = ROOT / "data" / "universe"
MANIFEST = RAW / "MANIFEST.jsonl"
UA = f"weird-erc20-research/0.1 (token census; {os.environ.get('WEIRD_ERC20_CONTACT', 'Weird ERC-20 Live research project')})"

# chain -> (chainId, CoinGecko platform id, target count, primary RPC, fallback RPC)
CHAINS = {
    "ethereum": dict(chain_id=1, cg="ethereum", target=2000, rpc="https://eth.drpc.org", rpc2="https://ethereum-rpc.publicnode.com"),
    "base": dict(chain_id=8453, cg="base", target=500, rpc="https://base.drpc.org", rpc2="https://base-rpc.publicnode.com"),
    "arbitrum": dict(chain_id=42161, cg="arbitrum-one", target=500, rpc="https://arbitrum.drpc.org", rpc2="https://arbitrum-one-rpc.publicnode.com"),
    "bsc": dict(chain_id=56, cg="binance-smart-chain", target=500, rpc="https://bsc-rpc.publicnode.com", rpc2="https://bsc-dataseed.bnbchain.org"),
    "polygon": dict(chain_id=137, cg="polygon-pos", target=500, rpc="https://polygon.drpc.org", rpc2="https://polygon-bor-rpc.publicnode.com"),
    "optimism": dict(chain_id=10, cg="optimistic-ethereum", target=500, rpc="https://optimism.drpc.org", rpc2="https://optimism-rpc.publicnode.com"),
}


def safe(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)[:180]


def save_raw(source, key, data: bytes, meta, ext="json"):
    d = RAW / safe(source)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{safe(key)}.{ext}"
    p.write_bytes(data)
    rec = {"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(data).hexdigest(),
           "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **meta}
    with open(MANIFEST, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return p


class Throttle:
    def __init__(self, min_interval):
        self.min_interval = min_interval
        self.last = 0.0

    def wait(self):
        dt = time.time() - self.last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self.last = time.time()


def http_get(url, source, key, throttle: Throttle, headers=None, refresh=False, max_tries=8, timeout=60):
    """GET with caching, throttle, 429/5xx backoff. Saves raw body (success only) + per-response meta
    (url, status, retrieved_at, sha256 in MANIFEST.jsonl). Returns parsed JSON (or None on 404/final fail)."""
    p = RAW / safe(source) / f"{safe(key)}.json"
    if p.exists() and not refresh:
        return json.loads(p.read_bytes())
    backoff = 30
    for i in range(max_tries):
        throttle.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read(); st = r.status
            save_raw(source, key, b, {"url": url, "method": "GET", "status": st})
            return json.loads(b)
        except urllib.error.HTTPError as e:
            body = e.read()[:300]
            if e.code == 429 or e.code >= 500:
                ra = e.headers.get("Retry-After")
                wait = max(int(ra) + 2, 10 * (i + 1)) if ra and ra.isdigit() else backoff
                print(f"[{e.code}] {url} -> sleeping {wait}s (try {i+1})", file=sys.stderr, flush=True)
                time.sleep(wait); backoff = min(backoff * 2, 240)
                continue
            print(f"[{e.code}] {url} {body}", file=sys.stderr, flush=True)
            return None
        except Exception as e:  # noqa: BLE001
            print(f"[err] {url} {e!r} (try {i+1})", file=sys.stderr, flush=True)
            time.sleep(10 * (i + 1))
    return None
