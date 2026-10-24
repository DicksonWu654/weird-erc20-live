"""Real-holder funding (fallback when no balance slot can be overridden).

Some tokens cannot be funded through eth_call state overrides: protocol-level / precompile-style tokens (Base's 0xB2000...
Coinbase tokenized assets: eth_getCode == 0xef, no storage, code/state overrides on their address are ignored), tokens whose
balance lives behind logic the access list does not expose, etc.  For these we use `eth_simulateV1` (supported on every
endpoint we use): one simulated block whose first call is a *real holder* (found from recent Transfer logs) transferring
tokens to the harness address H, followed by the usual harness `run(...)` call from E.  State changes persist between the
calls of one simulated block, so the whole battery runs unchanged from a contract holder.  Nothing is sent to the chain.
"""
import re

from common import *
from fund import Funding

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
WINDOWS = (200, 2000, 9000)
MAX_CANDIDATES = 14


def _addr_of(topic):
    return "0x" + topic[-40:]


def sim_params(calls, block_hex, state_overrides=None):
    blk = {"calls": calls}
    if state_overrides:
        blk["stateOverrides"] = state_overrides
    return [{"blockStateCalls": [blk], "validation": False}, block_hex]


def sim_calls(resp):
    """eth_simulateV1 response item -> list of per-call dicts, or None"""
    r = resp.get("result")
    if isinstance(r, list) and r and isinstance(r[0], dict) and "calls" in r[0]:
        return r[0]["calls"]
    return None


def sim_unwrap(resp, idx):
    """per-call result of a simulate response in eth_call shape: {'result': returnData} or {'error': {...}}; keeps _cid"""
    cs = sim_calls(resp)
    if cs is None or idx >= len(cs):
        out = dict(resp)
        if "error" not in out:
            out["error"] = {"code": -1, "message": "eth_simulateV1: unexpected response shape"}
        return out
    c = cs[idx]
    out = {"_cid": resp.get("_cid"), "_url": resp.get("_url"), "_sim_index": idx}
    if str(c.get("status", "0x1")) in ("0x1", "1"):
        out["result"] = c.get("returnData", "0x")
    else:
        e = c.get("error") or {}
        out["error"] = {"code": e.get("code", 3), "message": e.get("message", "execution reverted"),
                        "data": c.get("returnData") or e.get("data")}
    return out


async def find_holder_funding(pool, rec, token, block, block_hex, target, why):
    """-> (Funding|None, reason|None, info). `target`: desired balance for H."""
    info = {"why": why, "log_windows": [], "candidates": [], "attempts": []}
    cand, seen = [], set()
    for w in WINDOWS:
        lo = max(block - w, 0)
        r = await pool.one("eth_getLogs", [{"address": token, "fromBlock": hex(lo), "toBlock": block_hex,
                                            "topics": [TRANSFER_TOPIC]}], f"holder_logs_{w}", rec)
        entry = {"window_blocks": w, "cid": r.get("_cid")}
        logs = r.get("result")
        if isinstance(logs, list):
            entry["n_logs"] = len(logs)
            for lg in reversed(logs):
                tp = lg.get("topics", [])
                if len(tp) < 3:
                    continue
                for t in (tp[2], tp[1]):
                    a = _addr_of(t).lower()
                    if int(a, 16) > 0xffff and a not in seen and a != token.lower() and a not in (H.lower(), E.lower()):
                        seen.add(a)
                        cand.append(a)
        else:
            entry["error"] = (r.get("error") or {}).get("message", "")[:200]
        info["log_windows"].append(entry)
        if len(cand) >= MAX_CANDIDATES:
            break
    cand = cand[:MAX_CANDIDATES]
    if not cand:
        return None, "no_holder_found(no Transfer logs in the recent window or eth_getLogs unavailable)", info
    items = []
    for a in cand:
        items.append(("eth_call", [{"to": token, "data": hx(cd("balanceOf(address)", a))}, block_hex], "holder_balance"))
        items.append(("eth_getCode", [a, block_hex], "holder_code"))
    res = await pool.send(items, rec)
    rows = []
    for i, a in enumerate(cand):
        b, c = res[2 * i], res[2 * i + 1]
        bal = parse_u256(b)
        code = c.get("result")
        rows.append((a, bal, code, b.get("_cid"), c.get("_cid")))
        info["candidates"].append({"addr": a, "balance": bal, "code_len": (len(code) - 2) // 2 if code else None,
                                   "cids": [b.get("_cid"), c.get("_cid")]})
    ok = [x for x in rows if x[1] and x[1] >= 10_000]
    # prefer plain accounts (no code), then the largest balance
    ok.sort(key=lambda x: (0 if x[2] in ("0x", None) else 1, -x[1]))
    for a, bal, code, cb, cc in ok[:3]:
        x = min(bal, target)
        sim = await pool.one("eth_simulateV1", sim_params([
            {"from": a, "to": token, "data": hx(cd("transfer(address,uint256)", H, x))},
            {"from": E, "to": token, "data": hx(cd("balanceOf(address)", H))},
        ], block_hex), "holder_fund", rec)
        att = {"holder": a, "holder_balance": bal, "requested": x, "cid": sim.get("_cid")}
        info["attempts"].append(att)
        cs = sim_calls(sim)
        if not cs or len(cs) < 2:
            att["error"] = (sim.get("error") or {}).get("message", "bad simulate response")[:200]
            continue
        t = sim_unwrap(sim, 0)
        got = parse_u256(sim_unwrap(sim, 1))
        att["transfer"] = "ok" if "result" in t else "reverts:" + (t["error"].get("message", ""))[:100]
        att["received"] = got
        if "result" in t and got and got >= 10_000:
            f = Funding(method="real_holder", holder=a, holder_balance=bal, holder_is_eoa=(code in ("0x", None)),
                        balance=got, requested=x, addr=token.lower(), key=None, value=None, ratio=None, identity=None,
                        balance_bits=None, cids=[cb, cc, sim.get("_cid")],
                        prefix_call={"from": a, "to": token, "data": hx(cd("transfer(address,uint256)", H, x))})
            return f, None, info
    return None, "no_balance_slot_and_no_usable_holder(" + (
        "no holder with balance>=10000 base units among recent Transfer counterparties" if not ok else
        "holder transfers failed") + ")", info
