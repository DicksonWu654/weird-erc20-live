"""Raw-caching clients for honeypot.is (v2 IsHoneypot, v1 GetPairs) and GoPlus token_security.

All network access goes through `_http()`, which saves a full evidence envelope for EVERY attempt
(including 429 / 5xx / timeouts) via common.save_envelope. Parsing is left to normalize.py / compare.py.

honeypot.is: no API key needed; responses carry x-ratelimit-limit/remaining/reset headers (limit observed: 50);
  only chains 1 (Ethereum), 56 (BSC), 8453 (Base) -- others return {"code":400,"error":"Invalid chain"}.
GoPlus: no key => `contract_addresses` batching is silently truncated to the FIRST address, so we send one
  address per request. Chains 1,56,137,42161,10,8453 all supported.
Stored under data/raw/validate/{honeypot,honeypot_pairs,goplus}/<chain>/<addr>/<ts>.json
"""
import json, time, threading
import requests
from common import UA, RateLimiter, utcnow, save_envelope, sha256_text

HONEYPOT_CHAINS = {1, 56, 8453}
GOPLUS_CHAINS = {1, 56, 137, 42161, 10, 8453}

_sess = threading.local()


def _session():
    if not hasattr(_sess, "s"):
        _sess.s = requests.Session()
        _sess.s.headers["User-Agent"] = UA
    return _sess.s


# min seconds between request starts, per provider (shared across threads). Pacing: 0.6 s for honeypot.is, 6 s for keyless GoPlus, with backoff.
LIMITERS = {"honeypot": RateLimiter(0.6), "goplus": RateLimiter(6.0)}


def _goplus_code(body):
    try:
        return json.loads(body).get("code")
    except Exception:  # noqa: BLE001
        return None


def _http(limiter, store, chain, addr, endpoint, url, params, max_attempts=5, timeout=40):
    """GET with retries. Returns (path_of_last_envelope, envelope)."""
    lim = LIMITERS[limiter]
    path = env = None
    for attempt in range(1, max_attempts + 1):
        lim.wait()
        t0, iso = utcnow()
        status, hdrs, body, err = None, {}, "", None
        try:
            r = _session().get(url, params=params, timeout=timeout)
            status, hdrs, body = r.status_code, dict(r.headers), r.text
            full_url = r.url
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            full_url = requests.Request("GET", url, params=params).prepare().url
        env = {"provider": store, "endpoint": endpoint, "url": full_url, "params": params,
               "request_headers": {"User-Agent": UA}, "status": status, "response_headers": hdrs,
               "body_text": body, "body_sha256": sha256_text(body), "retrieved_at": iso,
               "retrieved_at_epoch_ms": int(t0 * 1000), "elapsed_ms": int((time.time() - t0) * 1000),
               "attempt": attempt, "error": err}
        path = save_envelope(store, chain, addr, env)
        throttled = status == 429 or (limiter == "goplus" and _goplus_code(body) in (4029, 4290))
        if limiter == "honeypot":
            rem, reset = hdrs.get("x-ratelimit-remaining"), hdrs.get("x-ratelimit-reset")
            try:
                if rem is not None and int(rem) <= 3 and reset:
                    lim.penalize(max(0.0, int(reset) - time.time()) + 0.5)
            except ValueError:
                pass
        if throttled:
            ra = hdrs.get("retry-after")
            lim.penalize(float(ra) if ra and ra.isdigit() else min(60, 10 * attempt))
            continue
        if err or (status and status >= 500):
            lim.penalize(3 * attempt)
            continue
        break
    return path, env


def honeypot_is_honeypot(chain, addr, pair=None):
    params = {"address": addr, "chainID": chain}
    if pair:
        params["pair"] = pair
    store = "honeypot_pinned" if pair else "honeypot"  # pinned = we told it which pair to use
    return _http("honeypot", store, chain, addr, "v2/IsHoneypot",
                 "https://api.honeypot.is/v2/IsHoneypot", params)


def honeypot_get_pairs(chain, addr):
    return _http("honeypot", "honeypot_pairs", chain, addr, "v1/GetPairs",
                 "https://api.honeypot.is/v1/GetPairs", {"address": addr, "chainID": chain})


def goplus_token_security(chain, addr):
    return _http("goplus", "goplus", chain, addr, f"token_security/{chain}",
                 f"https://api.gopluslabs.io/api/v1/token_security/{chain}", {"contract_addresses": addr})
