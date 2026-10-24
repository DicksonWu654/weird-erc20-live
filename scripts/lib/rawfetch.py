"""Fetch-and-save helper: every external response this project cites is saved verbatim under data/raw/.

Usage:
    from rawfetch import get, rpc
    body = get("https://boost-relay.flashbots.net/relay/v1/data/bidtraces/proposer_payload_delivered?block_number=17806056",
               source="relay-flashbots", key="payload_17806056")
    trace = rpc("debug_traceTransaction", [txhash, {"tracer": "callTracer"}], key=f"trace_{txhash}")

Each call writes data/raw/<source>/<key>.<ext> plus one line in data/raw/MANIFEST.jsonl
({path, url, method, params, retrieved_at, sha256, status}). Cached files are reused unless refresh=True,
so a timestamp on the site can always link to the saved raw response it came from.
"""
import hashlib, json, os, pathlib, re, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
MANIFEST = RAW / "MANIFEST.jsonl"
RPC_URL = os.environ.get("NFTU_RPC_URL", "https://eth.drpc.org")  # free endpoint with debug_/trace_ support
UA = {"User-Agent": os.environ.get("WEIRD_ERC20_USER_AGENT", "weird-erc20-live-research/1.0"), "Content-Type": "application/json"}


def _safe(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)[:180]


def _save(source, key, ext, data, meta):
    d = RAW / _safe(source)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{_safe(key)}.{ext}"
    p.write_bytes(data)
    rec = {"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(data).hexdigest(),
           "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **meta}
    with open(MANIFEST, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return p


def _cached(source, key, ext):
    p = RAW / _safe(source) / f"{_safe(key)}.{ext}"
    return p if p.exists() else None


def get(url, source, key, ext="json", refresh=False, timeout=60, headers=None, retries=3):
    """HTTP GET, saved raw. Returns parsed JSON for ext=json, else bytes."""
    if not refresh and (p := _cached(source, key, ext)):
        b = p.read_bytes()
        return json.loads(b) if ext == "json" else b
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                status = r.status
            redacted = re.sub(r"(apikey=)[^&]+", r"\1REDACTED", url)
            _save(source, key, ext, b, {"url": redacted, "method": "GET", "status": status})
            return json.loads(b) if ext == "json" else b
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def rpc(method, params, key, source="rpc", url=None, refresh=False, timeout=120, retries=4):
    """JSON-RPC call, saved raw (full response envelope). Returns result."""
    url = url or RPC_URL
    if not refresh and (p := _cached(source, key, "json")):
        return json.loads(p.read_bytes()).get("result")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
            j = json.loads(b)
            if "error" in j:
                raise RuntimeError(f"{method}: {j['error']}")
            _save(source, key, "json", b, {"url": url, "method": method, "params": params, "status": 200})
            return j["result"]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def etherscan(params, key, chainid=1, refresh=False):
    """Etherscan API v2 (key from ETHERSCAN_API_KEY in the environment); the key is redacted in MANIFEST."""
    k = os.environ.get("ETHERSCAN_API_KEY")
    if not k:
        raise RuntimeError("Set ETHERSCAN_API_KEY in the environment")
    q = "&".join(f"{a}={b}" for a, b in params.items())
    return get(f"https://api.etherscan.io/v2/api?chainid={chainid}&{q}&apikey={k}", source="etherscan", key=key,
               refresh=refresh)


# ---------------------------------------------------------------------------------------------------------------
# W3 extensions (2026-10-21): soft GET that records non-2xx / network failures, and a streaming downloader.
# ---------------------------------------------------------------------------------------------------------------
import urllib.error


def _log_only(meta):
    """Manifest line for a request that produced no saved file (network failure)."""
    rec = {"path": None, "sha256": None, "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **meta}
    with open(MANIFEST, "a") as f:
        f.write(json.dumps(rec) + "\n")


def get_soft(url, source, key, ext="json", refresh=False, timeout=30, headers=None, retries=2, cache_errors=True):
    """HTTP GET that never raises on HTTP errors. The raw body is saved whatever the status (so a 404/429 page is
    evidence too). Returns dict {status, body(bytes), json(parsed or None), path(rel or None), error}.
    Cached responses are reused (including saved error responses) unless refresh=True; the HTTP status of a cached
    response is looked up in the MANIFEST (last line for that path)."""
    p = _cached(source, key, ext)
    if p and not refresh:
        b = p.read_bytes()
        status = None
        rel = str(p.relative_to(ROOT))
        if MANIFEST.exists():
            for line in reversed(MANIFEST.read_text().splitlines()):
                if rel in line:
                    try:
                        j = json.loads(line)
                        if j.get("path") == rel:
                            status = j.get("status")
                            break
                    except ValueError:
                        pass
        try:
            parsed = json.loads(b)
        except ValueError:
            parsed = None
        return {"status": status, "body": b, "json": parsed, "path": rel, "error": None, "cached": True}
    redacted = re.sub(r"(apikey=)[^&]+", r"\1REDACTED", url)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    b, status = r.read(), r.status
            except urllib.error.HTTPError as e:
                b, status = e.read(), e.code
                if status in (429, 502, 503, 504) and i < retries - 1:
                    time.sleep(3 * (i + 1))
                    continue
            path = _save(source, key, ext, b, {"url": redacted, "method": "GET", "status": status})
            try:
                parsed = json.loads(b)
            except ValueError:
                parsed = None
            return {"status": status, "body": b, "json": parsed, "path": str(path.relative_to(ROOT)), "error": None}
        except Exception as e:  # noqa: BLE001  (DNS failure, TLS, timeout ...)
            last = e
            time.sleep(1 + i)
    _log_only({"url": redacted, "method": "GET", "status": None, "error": repr(last)[:300]})
    return {"status": None, "body": b"", "json": None, "path": None, "error": repr(last)[:300]}


def download(url, source, key, refresh=False, timeout=120, chunk=1 << 20, headers=None):
    """Stream a (possibly large/binary) file to data/raw/<source>/<key> exactly as named (no extension added),
    hashing while writing; records sha256 + size + status in MANIFEST. Returns pathlib.Path. Resumes nothing: a
    complete file is reused unless refresh=True. Written via a .part file so interrupted downloads never look complete."""
    d = RAW / _safe(source)
    d.mkdir(parents=True, exist_ok=True)
    p = d / _safe(key)
    if p.exists() and not refresh:
        return p
    h = hashlib.sha256()
    n = 0
    part = p.with_name(p.name + ".part")
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
        status = r.status
        hdrs = {k: r.headers.get(k) for k in ("Content-Length", "Last-Modified", "ETag", "Content-Type")}
        while True:
            b = r.read(chunk)
            if not b:
                break
            f.write(b)
            h.update(b)
            n += len(b)
    part.replace(p)
    rec = {"path": str(p.relative_to(ROOT)), "sha256": h.hexdigest(), "bytes": n, "url": url, "method": "GET",
           "status": status, "headers": hdrs, "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(MANIFEST, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return p


def raw_path(source, key, ext="json"):
    """Repo-relative path of the raw file get()/rpc()/get_soft() writes for (source, key); None if not saved."""
    p = RAW / _safe(source) / f"{_safe(key)}.{ext}"
    return str(p.relative_to(ROOT)) if p.exists() else None
