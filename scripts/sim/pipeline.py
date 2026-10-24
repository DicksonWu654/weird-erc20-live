"""Per-pair orchestration: probe+buy (phase 1), full buy/transfer/sell (phase 2), metric extraction."""
import asyncio, gzip, hashlib, json, os, time
from abi import *
import engine as E
from engine import R, parse_sim, Plan
from rpc import RpcTransportError
import v4 as V4

ETH = 10 ** 18
DUST_UNITS = 10_000  # buys yielding fewer raw units cannot be split into the sell ladder
DEFAULT_AMTS = [ETH // 100, ETH // 2000]  # 0.01 ETH, then 0.0005 ETH if the first buy reverts


class RawLog:
    """Collects every request/response for one pair; written gzipped as one JSON file."""

    def __init__(self):
        self.items = []

    def add(self, label, raw):
        self.items.append({"label": label, **raw})

    def dump(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = json.dumps(self.items, separators=(",", ":")).encode()
        with gzip.open(path, "wb", compresslevel=6) as f:
            f.write(data)
        return hashlib.sha256(data).hexdigest()


async def sim(ctx, rl, label, plan, block):
    """Run a plan with eth_simulateV1 at `block` (int). Returns (parsed dict, metas) or raises SimError."""
    if getattr(ctx, "sim_fn", None):  # alternative backend (anvil fork cross-check)
        return await ctx.sim_fn(rl, label, plan, block)
    params = plan.params(block)
    last_err = None
    for ep in ctx.sim_endpoints:
        try:
            raw = await ep.request("eth_simulateV1", params)
        except RpcTransportError as e:
            last_err = f"transport:{e}"
            continue
        rl.add(label, raw)
        resp = raw["response"]
        if "error" in resp:
            last_err = "rpc:" + json.dumps(resp["error"])[:300]
            continue
        try:
            return parse_sim(plan, resp["result"])
        except Exception as e:  # malformed result
            last_err = f"parse:{e!r}"
            continue
    raise SimError(last_err or "no endpoint")


class SimError(Exception):
    pass


def classify_structure(p, token):
    """Detect pool kind from probe calls of phase 1. Returns dict(kind, router, quote, fee, reason)."""
    tok = token.lower()
    f, t0, t1 = p["pool_factory"], p["pool_token0"], p["pool_token1"]
    if p["pool_tok_addr"].ok and len(p["pool_tok_addr"].ret) >= 66:
        ta = p["pool_tok_addr"].addr()
        if ta == tok:
            return {"kind": "v1", "router": None, "quote": WETH}
    if not f.ok or f.ret in ("0x", "") or len(f.ret) < 66:
        return {"kind": None, "reason": "no_pool_at_block"}
    fac = f.addr()
    if fac == V3_FACTORY and p["pool_fee"].ok:
        a0, a1 = t0.addr(), t1.addr()
        other = a1 if a0 == tok else a0
        return {"kind": "v3", "quote": to_checksum_address(other), "fee": p["pool_fee"].u()}
    if fac in V2_ROUTERS:
        a0, a1 = t0.addr(), t1.addr()
        if tok not in (a0, a1):
            return {"kind": None, "reason": "token_not_in_pool"}
        other = a1 if a0 == tok else a0
        return {"kind": "v2", "dex": V2_ROUTERS[fac][0], "router": V2_ROUTERS[fac][1], "quote": to_checksum_address(other)}
    return {"kind": None, "reason": f"unsupported_factory:{fac}"}


def probe_calls(P, token, pool, meta):
    P.call("pool_factory", pool, enc("factory()"))
    P.call("pool_token0", pool, enc("token0()"))
    P.call("pool_token1", pool, enc("token1()"))
    P.call("pool_reserves", pool, enc("getReserves()"))
    P.call("pool_fee", pool, enc("fee()"))
    P.call("pool_tok_addr", pool, enc("tokenAddress()"))
    P.call("pool_liquidity", pool, enc("liquidity()"))
    if meta:
        E.token_meta_calls(P, token)


def phase1_plan(kind, token, pool, amt, router=None, quote=WETH, fee=None, meta=True):
    P = Plan().block(E.first_state())
    probe_calls(P, token, pool, meta)
    if kind == "v2":
        E.v2_buy_block(P, token, router, quote, amt)
    elif kind == "v3":
        E.v3_buy_block(P, token, fee, amt)
    elif kind == "v1":
        E.v1_buy_block(P, token, pool, amt)
    return P


def phase2_plan(kind, token, pool, amt, received, router=None, quote=WETH, fee=None, owner=None):
    if kind == "v2":
        P = E.v2_phase2(token, pool, router, quote, amt, received)
    elif kind == "v3":
        P = E.v3_phase2(token, pool, fee, amt, received)
    else:
        P = E.v1_phase2(token, pool, amt, received)
    if owner and owner not in (ZERO,):
        P.blocks[0]["calls"].append(("owner_size", {"from": BUYER, "to": H_SIZE, "data": E.a32(owner), "gas": hex(100000), "gasPrice": "0x0"}))
    return P


def meta_from(p):
    m = {}
    if "t_decimals" not in p:
        return m
    m["decimals"] = p["t_decimals"].u()
    m["symbol"] = p["t_symbol"].string() if p["t_symbol"].ok else None
    m["name"] = p["t_name"].string() if p["t_name"].ok else None
    m["total_supply"] = p["t_supply"].u()
    for k in ("t_owner", "t_getOwner"):
        r = p[k]
        m[k[2:]] = r.addr() if r.ok and len(r.ret) >= 66 else None
    code = p["t_code"].ret if p["t_code"].ok else "0x"
    m["code_len"] = (len(code) - 2) // 2
    m["code_hex"] = code
    m["dead_balance"] = p["t_bal_dead"].u()
    return m


def pct(x):
    return None if x is None else round(x, 6)


def tax_of(expected, got):
    if expected is None or got is None or expected <= 0:
        return None
    return max(0.0, 1 - got / expected)


def summarize_swaps(p, kind, received, pool_id=None, sell_zero_for_one=None):
    """Sell ladder attempts from phase 2 results."""
    out = []
    for tag, delayed in (("s1", False), ("s2", True)):
        ladder = E.SELL2_FR if delayed else E.SELL1_FR
        for k, (n, d) in enumerate(ladder):
            pre = f"{tag}{k}"
            if pre not in p or not p[pre].present:
                continue
            r = p[pre]
            amount = received if kind == "v4" and received < 1000 and pre == "s10" else received * n // d
            exp = p[pre + "_quote"].arr_last() if kind == "v2" else p[pre + "_quote"].u()
            pre_eth, post_eth = p[pre + "_eth_pre"].u(), p[pre + "_eth_post"].u()
            got = (post_eth - pre_eth) if (r.ok and pre_eth is not None and post_eth is not None) else None
            bal_pre, bal_post = p[pre + "_bal_pre"].u(), p[pre + "_bal_post"].u()
            debit = (bal_pre - bal_post) if (bal_pre is not None and bal_post is not None) else None
            tax = pct(tax_of(exp, got)) if r.ok else None
            core = None
            if kind == "v4" and r.ok:
                core_tax, core = V4.core_tax(r.logs, pool_id, sell_zero_for_one, debit, got)
                if core_tax is not None:
                    tax = core_tax
            item = {"id": pre, "delayed": delayed, "frac": "1/1" if kind == "v4" and received < 1000 and pre == "s10" else f"{n}/{d}", "amount": amount, "ok": r.ok, "gas": r.gas,
                    "reason": r.reason, "expected_eth": exp, "received_eth": got,
                    "tax": tax, "tokens_debited": debit, "quote_ok": p[pre + "_quote"].ok}
            if kind == "v4":
                item["core_swap"] = core
            out.append(item)
    return out


async def _simulate_v4_pair(ctx, token, pool, block, amts, pair_data, rl):
    """V4 equivalent of ``simulate_pair``; keeps the legacy route untouched."""
    res = {"token": token, "pool": pool, "block": block, "buy": None, "transfer": None,
           "sells": [], "taxes": {}, "meta": {}, "unprobeable": None, "pool_info": {}}
    row = pair_data or {"pool": pool}
    key = V4.pair_key(row, token)
    if key is None:
        res["unprobeable"] = "v4_missing_pool_key"
        return res, rl
    mask, names = V4.hook_permissions(key["hooks"])
    res["pool_info"] = {
        "kind": "v4", "dex": "uniswap_v4", "router": V4.V4_ROUTER,
        "quote": key["currency0"] if key["zero_for_one_buy"] else key["currency1"],
        "pool_id": pool, "token0": key["currency0"], "token1": key["currency1"],
        "fee": key["fee"], "tick_spacing": key["tick_spacing"], "hooks": key["hooks"],
        "hook_permission_bits": mask, "hook_permissions": names,
        "quote_is_token0": key["zero_for_one_buy"],
    }
    if not (V4.is_native(key["currency0"]) or key["currency0"].lower() == WETH.lower()
            or V4.is_native(key["currency1"]) or key["currency1"].lower() == WETH.lower()):
        res["unprobeable"] = "v4_non_eth_quote"
        return res, rl

    used_amt, p1, p1meta = None, None, None
    dust_retry = False
    try:
        for i, amount in enumerate(list(amts)):
            plan, _ = V4.phase1(token, pool, row, amount, meta=(p1meta is None))
            p1, metas = await sim(ctx, rl, f"phase1:v4:amt={amount}", plan, block)
            if p1meta is None:
                p1meta = p1
                res["meta"] = meta_from(p1)
            b, q = p1.get("buy"), p1.get("buy_quote")
            # A pre-deployment call can report status=1 with empty returndata.
            # UniversalRouter.execute has no return value, so a successful
            # buy itself legitimately returns 0x.  Use buy_bal (and the
            # quoter's two-word return) to detect the historical predeploy
            # empty-call quirk.
            if b is None or not b.present or (q.ok and len(q.ret) < 66) \
                    or (b.ok and p1.get("buy_bal") is not None and p1["buy_bal"].ok and len(p1["buy_bal"].ret) < 66):
                res["unprobeable"] = "router_not_deployed_at_block"
                return res, rl
            used_amt = amount
            if b.ok:
                received = p1.get("buy_bal").u()
                if received is not None and 0 < received < DUST_UNITS and amount < 5 * 10**16 and not dust_retry:
                    dust_retry = True
                    # Repeat with the larger probe amount below.
                    amts = [amount * 100] + list(amts[i + 1:])
                    continue
                break
            # Quoter's no-liquidity failure and an explicit zero-liquidity
            # StateView response both stop the smaller retry.
            liq = p1.get("v4_liquidity").u() if p1.get("v4_liquidity") else None
            if liq == 0 or "LIQUIDITY" in (q.reason or "").upper():
                break
    except SimError as e:
        res["unprobeable"] = f"sim_error:{e}"
        return res, rl
    if p1 is None:
        res["unprobeable"] = "sim_error:no_phase1"
        return res, rl
    b, q = p1["buy"], p1["buy_quote"]
    exp = q.u()
    recv = p1["buy_bal"].u() if b.ok else None
    pre_eth, post_eth = p1["buy_quote_bal_pre"].u(), p1["buy_quote_bal_post"].u()
    spent = (pre_eth - post_eth) if b.ok and pre_eth is not None and post_eth is not None else None
    core_tax, core = V4.core_tax(b.logs, pool, key["zero_for_one_buy"], spent, recv) if b.ok else (None, None)
    res["buy"] = {"ok": b.ok, "amount_in_wei": used_amt, "expected_out": exp, "received": recv,
                   "tax": core_tax if core_tax is not None else (pct(tax_of(exp, recv)) if b.ok else None),
                   "quote_spent_wei": spent, "core_swap": core, "gas": b.gas,
                   "reason": b.reason, "quote_ok": q.ok, "quote_reason": q.reason}
    liq = p1.get("v4_liquidity").u() if p1.get("v4_liquidity") else None
    res["pool_info"]["liquidity"] = liq
    if not b.ok:
        if liq == 0 or ("LIQUIDITY" in (q.reason or "").upper()) or exp == 0:
            res["unprobeable"] = "no_liquidity"
        else:
            res["unprobeable"] = f"buy_reverts:{b.reason}"
        return res, rl
    if not recv:
        res["buy"]["tax"] = 1.0
        res["buy"]["received"] = 0
        return res, rl
    if recv < 1000:
        res["pool_info"]["integer_lot_units"] = recv

    try:
        plan2, _ = V4.phase2(token, pool, row, used_amt, recv)
        p2, metas = await sim(ctx, rl, "phase2:v4", plan2, block)
    except SimError as e:
        res["unprobeable"] = f"sim_error_phase2:{e}"
        return res, rl
    res["blocks"] = metas
    res["replay_consistent"] = (p2.get("buy_bal") is not None and p2["buy_bal"].u() == recv)
    x = p2["xfer"]
    sent = 0 if recv < 1000 else recv * E.XFER_FR[0] // E.XFER_FR[1]
    b0, b1 = p2["xfer_bal_b0"].u(), p2["xfer_bal_b"].u()
    got_b = b1 - b0 if b0 is not None and b1 is not None else None
    a_after = p2["xfer_bal_a"].u()
    ret = x.ret
    res["transfer"] = {"ok": x.ok, "gas": x.gas, "reason": x.reason, "sent": sent,
                       "received_by_b": got_b, "tax": pct(tax_of(sent, got_b)) if x.ok else None,
                       "sender_debit": recv - a_after if a_after is not None else None,
                       "returned_false": (x.ok and len(ret) >= 66 and int(ret[2:66], 16) == 0) if x.ok else None,
                       "returned_empty": x.ok and ret == "0x"}
    if sent == 0:
        res["transfer"] = None
    ap = p2["approve"]
    p2allow = p2.get("permit2_allowance")
    res["approve"] = {"ok": ap.ok, "reason": ap.reason, "gas": ap.gas,
                       "allowance": p2["allowance"].u(),
                       "permit2_ok": p2.get("permit2_approve").ok if p2.get("permit2_approve") else None,
                       "permit2_allowance": p2allow.u() if p2allow else None}
    res["sells"] = summarize_swaps(p2, "v4", recv, pool, not key["zero_for_one_buy"])
    origins = []
    for s in res["sells"]:
        if not s["ok"]:
            o = V4.classify_revert(s.get("reason"), key["hooks"])
            s["revert_origin"] = o
            origins.append(o)
    if not x.ok:
        origins.append(V4.classify_revert(x.reason, key["hooks"]))
    if not b.ok:
        origins.append(V4.classify_revert(b.reason, key["hooks"]))
    res["pool_info"]["revert_origins"] = sorted(set(origins))
    res["pool_info"]["hook_revert_origin"] = "hook" if "hook" in origins else ("token" if "token" in origins else None)
    return res, rl


async def simulate_pair(ctx, token, pool, block, dex_hint=None, amts=None, with_code=True, pair_data=None):
    """Full sim of one pair at `block` (int). Returns result dict (no label) + RawLog."""
    token = to_checksum_address(token)
    # V4's pool identifier is bytes32, not an address.  Preserve it verbatim;
    # V1/V2/V3 continue to use checksum pool addresses exactly as before.
    hint0 = (dex_hint or "").lower()
    if hint0 in ("v4", "uniswap_v4") or (isinstance(pool, str) and len(pool) == 66):
        pool = "0x" + pool[2:].lower().zfill(64)
    else:
        pool = to_checksum_address(pool)
    rl = RawLog()
    res = {"token": token, "pool": pool, "block": block, "buy": None, "transfer": None, "sells": [], "taxes": {},
           "meta": {}, "unprobeable": None, "pool_info": {}}
    amts = amts or DEFAULT_AMTS
    hint = hint0
    if hint in ("v4", "uniswap_v4") or len(pool) == 66:
        return await _simulate_v4_pair(ctx, token, pool, block, amts or DEFAULT_AMTS, pair_data, rl)
    kind = {"uniswap_v3": "v3", "v3": "v3", "uniswap_v1": "v1", "v1": "v1"}.get(hint, "v2")
    router, quote, fee = V2_ROUTERS["0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"][1], WETH, 3000
    struct = None
    p1 = None
    used_amt = None
    meta_done = False
    best = None
    dust_retry = False
    try:
        amts = list(amts)
        attempt = -1
        while attempt + 1 < len(amts):
            attempt += 1
            amt = amts[attempt]
            for rerun in range(2):
                plan = phase1_plan(kind, token, pool, amt, router=router, quote=quote, fee=fee, meta=not meta_done)
                p1_new, metas = await sim(ctx, rl, f"phase1:{kind}:amt={amt}", plan, block)
                if not meta_done:
                    res["meta"] = meta_from(p1_new)
                    meta_done = True
                    saved_probe = p1_new
                p1 = p1_new
                if "pool_factory" not in p1:
                    p1.update({k: v for k, v in saved_probe.items() if k.startswith(("pool_", "t_"))})
                struct = classify_structure(saved_probe, token)
                if struct["kind"] is None:
                    res["unprobeable"] = struct["reason"]
                    return res, rl
                if struct["kind"] != kind or (kind == "v2" and (struct["router"].lower() != router.lower() or struct["quote"].lower() != quote.lower())) \
                        or (kind == "v3" and (struct["quote"].lower() != WETH.lower() or struct["fee"] != fee)):
                    # speculation was wrong -> re-plan with detected structure
                    kind = struct["kind"]
                    if kind == "v2":
                        router, quote = struct["router"], struct["quote"]
                    elif kind == "v3":
                        fee = struct["fee"]
                        if struct["quote"].lower() != WETH.lower():
                            res["unprobeable"] = f"v3_non_weth_quote:{struct['quote']}"
                            res["pool_info"] = {"kind": kind, "quote": struct["quote"]}
                            return res, rl
                    continue
                break
            used_amt = amt
            if p1["buy"].ok:
                best = (amt, p1)
                got = p1["buy_bal"].u()
                if got is not None and 0 < got < DUST_UNITS and amt < ETH and not dust_retry:
                    dust_retry = True
                    amts.insert(attempt + 1, min(amt * 100, ETH))  # dust output (e.g. cTokens, WBTC-like price): buy 100x more so ladder amounts are non-zero
                    continue
                break
            # no liquidity at all? don't retry smaller amounts
            q = p1["buy_quote"]
            if not q.ok and "INSUFFICIENT_LIQUIDITY" in (q.reason or ""):
                break
    except SimError as e:
        res["unprobeable"] = f"sim_error:{e}"
        return res, rl
    if best and not p1["buy"].ok:  # a larger follow-up buy failed; keep the earlier successful (dust-sized) one
        used_amt, p1 = best

    quote_tok = struct.get("quote", WETH)
    res["pool_info"] = {"kind": kind, "dex": struct.get("dex", {"v1": "uniswap_v1", "v3": "uniswap_v3"}.get(kind)),
                        "router": router if kind == "v2" else (V3_ROUTER if kind == "v3" else pool),
                        "quote": quote_tok, "fee": fee if kind == "v3" else None}
    if kind == "v2":
        rr = p1["pool_reserves"]
        if rr.ok and len(rr.ret) >= 2 + 192:
            r0, r1 = rr.u(0), rr.u(1)
            res["pool_info"].update(reserve_token=r0 if p1["pool_token0"].addr() == token.lower() else r1,
                                    reserve_quote=r1 if p1["pool_token0"].addr() == token.lower() else r0)
    res["pool_info"]["token0"] = p1["pool_token0"].addr()
    res["pool_info"]["quote_is_token0"] = (p1["pool_token0"].addr() == quote_tok.lower()) if kind in ("v2", "v3") else None
    res["pool_info"]["pool_used"] = any(l["address"].lower() == pool.lower() for l in p1["buy"].logs) if p1["buy"].ok else None
    b = p1["buy"]
    qr = p1["buy_quote"]
    if qr.ok and ((kind == "v2" and qr.arr_last() is None) or (kind != "v2" and qr.u() is None)):
        # a call to a not-yet-deployed router/quoter "succeeds" with empty return data
        res["unprobeable"] = "router_not_deployed_at_block"
        return res, rl
    exp = p1["buy_quote"].arr_last() if kind == "v2" else p1["buy_quote"].u()
    recv = p1["buy_bal"].u() if b.ok else None
    res["buy"] = {"ok": b.ok, "amount_in_wei": used_amt, "expected_out": exp, "received": recv, "tax": pct(tax_of(exp, recv)) if b.ok else None,
                  "gas": b.gas, "reason": b.reason, "quote_ok": p1["buy_quote"].ok, "quote_reason": p1["buy_quote"].reason}
    if not b.ok:
        qr = p1["buy_quote"].reason or ""
        qreason = p1["buy_quote"].reason or ""
        liq0 = kind == "v3" and p1["pool_liquidity"].ok and p1["pool_liquidity"].u() == 0
        if (not p1["buy_quote"].ok and "LIQUIDITY" in qreason.upper()) or liq0:
            res["unprobeable"] = "no_liquidity"
        elif kind == "v3" and (b.reason or "") in ("SPL", "LOK", "AS"):
            res["unprobeable"] = f"v3_pool_state:{b.reason}"
        else:
            res["unprobeable"] = f"buy_reverts:{b.reason}"
        return res, rl
    if not recv:  # buy "succeeds" but nothing arrives: 100% buy tax
        res["buy"]["tax"] = 1.0
        res["buy"]["received"] = 0
        return res, rl

    if recv < 1000:  # cannot split into a sell ladder
        res["unprobeable"] = f"dust_buy:{recv}_units"
        return res, rl
    owner = res["meta"].get("owner") or res["meta"].get("getOwner")
    plan2 = phase2_plan(kind, token, pool, used_amt, recv, router=router, quote=quote, fee=fee, owner=owner)
    try:
        p2, metas = await sim(ctx, rl, "phase2", plan2, block)
    except SimError as e:
        res["unprobeable"] = f"sim_error_phase2:{e}"
        return res, rl
    res["blocks"] = metas
    # replay consistency
    res["replay_consistent"] = (p2["buy_bal"].u() == recv)
    x = p2["xfer"]
    sent = recv * E.XFER_FR[0] // E.XFER_FR[1]
    b0, b1 = p2["xfer_bal_b0"].u(), p2["xfer_bal_b"].u()
    got_b = (b1 - b0) if (b0 is not None and b1 is not None) else None
    a_after = p2["xfer_bal_a"].u()
    ret = x.ret
    res["transfer"] = {"ok": x.ok, "gas": x.gas, "reason": x.reason, "sent": sent, "received_by_b": got_b,
                       "tax": pct(tax_of(sent, got_b)) if x.ok else None,
                       "sender_debit": (recv - a_after) if (a_after is not None) else None,
                       "returned_false": (x.ok and len(ret) >= 66 and int(ret[2:66], 16) == 0) if x.ok else None,
                       "returned_empty": (x.ok and ret == "0x")}
    ap = p2["approve"]
    res["approve"] = {"ok": ap.ok, "reason": ap.reason, "gas": ap.gas, "allowance": p2["allowance"].u()}
    if "owner_size" in p2:
        res["meta"]["owner_code_len"] = p2["owner_size"].u()
    res["sells"] = summarize_swaps(p2, kind, recv)
    return res, rl


def sell_summary(res):
    """taxes + sell status from sells ladder."""
    sells = res["sells"]
    ok = [s for s in sells if s["ok"]]
    first = next((s for s in sells if s["id"] == "s1_0" or s["id"] == "s10"), None)
    return sells, ok, first
