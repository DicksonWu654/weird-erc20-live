"""Turn cached provider envelopes into normalized per-token records (see data/validate/mapping.md).

normalize_honeypot(envelope) / normalize_goplus(envelope, addr) -> dict with
  coverage : ok | sim_failed | no_data | unsupported_chain | error      (never guess: errors stay errors)
  is_honeypot : True/False/None   buy_tax / sell_tax / transfer_tax in PERCENT (0-100) or None
  plus provider-specific extras; `path`/`retrieved_at`/`sha256` always carried for evidence links.

Taxes: honeypot.is reports percent (e.g. 5.2 = 5.2%); GoPlus reports a 0-1 fraction as a string ("0.05") or "" when
unknown -> multiplied by 100 here.
"""
import json
from common import latest_envelope, nearest_envelope, ROOT


def _f(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _b(x):
    if x is None or x == "":
        return None
    return str(x) == "1"


def _body(env):
    try:
        return json.loads(env.get("body_text") or "")
    except Exception:  # noqa: BLE001
        return None


def _meta(env, path):
    return {"http_status": env.get("status"), "retrieved_at": env.get("retrieved_at"),
            "retrieved_at_epoch_ms": env.get("retrieved_at_epoch_ms"), "sha256": env.get("body_sha256"),
            "path": str(path.relative_to(ROOT)) if path else None}


def normalize_honeypot(env, path=None):
    out = {"provider": "honeypot", "coverage": "error", "is_honeypot": None, "buy_tax": None, "sell_tax": None,
           "transfer_tax": None, **_meta(env, path)}
    st, j = env.get("status"), _body(env)
    if st is None or st == 429 or st >= 500 or j is None:
        out["error"] = env.get("error") or f"http {st}"
        return out
    if st == 400 and "chain" in str(j.get("error", "")).lower():
        out.update(coverage="unsupported_chain", error=j.get("error"))
        return out
    if st == 404 or (isinstance(j, dict) and j.get("code") == 404):
        out.update(coverage="no_data", error=j.get("error"))
        return out
    if st != 200 or not isinstance(j, dict) or "summary" not in j:
        out["error"] = f"unexpected: http {st} {str(j)[:120]}"
        return out
    hr, sr = j.get("honeypotResult") or {}, j.get("simulationResult") or {}
    summ = j.get("summary") or {}
    out.update(
        coverage="ok" if j.get("simulationSuccess") else "sim_failed",
        sim_success=bool(j.get("simulationSuccess")), sim_error=j.get("simulationError"),
        buy_failed=("BUY_FAILED" in str(j.get("simulationError") or "")),
        no_liquidity=("INSUFFICIENT_LIQUIDITY" in str(j.get("simulationError") or "")),
        is_honeypot=hr.get("isHoneypot") if "isHoneypot" in hr else None,
        honeypot_reason=hr.get("honeypotReason"),
        buy_tax=_f(sr.get("buyTax")), sell_tax=_f(sr.get("sellTax")), transfer_tax=_f(sr.get("transferTax")),
        risk=summ.get("risk"), risk_level=summ.get("riskLevel"),
        flags=[x.get("flag") for x in (summ.get("flags") or [])],
        pair=(j.get("pair") or {}).get("pairAddress") or j.get("pairAddress"),
        pair_name=((j.get("pair") or {}).get("pair") or {}).get("name"),
        pair_type=((j.get("pair") or {}).get("pair") or {}).get("type"),
        liquidity_usd=(j.get("pair") or {}).get("liquidity"),
        open_source=(j.get("contractCode") or {}).get("openSource"),
        root_open_source=(j.get("contractCode") or {}).get("rootOpenSource"),
        is_proxy=(j.get("contractCode") or {}).get("isProxy"),
        has_proxy_calls=(j.get("contractCode") or {}).get("hasProxyCalls"),
        holders_failed=_f((j.get("holderAnalysis") or {}).get("failed")),
        holders_analyzed=_f((j.get("holderAnalysis") or {}).get("holders")),
        siphoned=_f((j.get("holderAnalysis") or {}).get("siphoned")),
        max_sell_present="maxSell" in sr, max_buy_present="maxBuy" in sr,
        total_holders=(j.get("token") or {}).get("totalHolders"))
    return out


GOPLUS_BOOL = ["is_open_source", "is_proxy", "is_mintable", "can_take_back_ownership", "owner_change_balance",
               "hidden_owner", "selfdestruct", "external_call", "gas_abuse", "is_in_dex", "cannot_buy",
               "cannot_sell_all", "slippage_modifiable", "personal_slippage_modifiable", "is_honeypot",
               "transfer_pausable", "is_blacklisted", "is_whitelisted", "is_anti_whale", "anti_whale_modifiable",
               "trading_cooldown", "honeypot_with_same_creator", "trust_list"]


def normalize_goplus(env, addr, path=None):
    out = {"provider": "goplus", "coverage": "error", "is_honeypot": None, "buy_tax": None, "sell_tax": None,
           "transfer_tax": None, **_meta(env, path)}
    st, j = env.get("status"), _body(env)
    if st is None or st == 429 or st >= 500 or j is None:
        out["error"] = env.get("error") or f"http {st}"
        return out
    out["goplus_code"] = j.get("code")  # 1 = ok; 3 = ok but partial data (seen for UniV4-only tokens)
    if j.get("code") not in (1, 3):
        out["error"] = f"goplus code {j.get('code')}: {j.get('message')}"
        if j.get("code") in (4029, 4290):
            out["error"] = "rate_limited " + out["error"]
        return out
    res = j.get("result") or {}
    r = res.get(addr.lower()) or (next(iter(res.values())) if len(res) == 1 else None)
    if not r:
        out["coverage"] = "no_data"
        return out
    out["coverage"] = "ok"
    out["raw_fields_present"] = sorted(k for k in r if k not in ("holders", "lp_holders", "dex"))
    for k in GOPLUS_BOOL:
        out[k] = _b(r.get(k))
    out["is_honeypot"] = _b(r.get("is_honeypot"))
    for k in ("buy_tax", "sell_tax", "transfer_tax"):
        v = _f(r.get(k))
        out[k] = None if v is None else round(v * 100, 6)
    out["owner_address"] = r.get("owner_address")  # "" = no owner (renounced/ownerless); absent = unknown
    out["owner_address_present"] = "owner_address" in r
    out["creator_address"] = r.get("creator_address")
    out["holder_count"] = _f(r.get("holder_count"))
    out["total_supply"] = r.get("total_supply")
    out["token_symbol"] = r.get("token_symbol")
    out["dex_count"] = len(r.get("dex") or [])
    out["dex_liquidity_usd"] = sum(_f(d.get("liquidity")) or 0 for d in (r.get("dex") or []))
    out["honeypot_note"] = r.get("honeypot_note")
    out["other_potential_risks"] = r.get("other_potential_risks")
    out["note"] = r.get("note")
    return out


def get_honeypot(chain, addr, at_ms=None, store="honeypot"):
    p, e = (nearest_envelope(store, chain, addr, at_ms) if at_ms else latest_envelope(store, chain, addr, ok_only=True))
    if not e:
        p, e = latest_envelope(store, chain, addr)
    return normalize_honeypot(e, p) if e else None


def get_goplus(chain, addr, at_ms=None):
    p, e = (nearest_envelope("goplus", chain, addr, at_ms) if at_ms else latest_envelope("goplus", chain, addr, ok_only=True))
    if not e:
        p, e = latest_envelope("goplus", chain, addr)
    return normalize_goplus(e, addr, p) if e else None
