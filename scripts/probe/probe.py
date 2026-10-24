"""Per-token probing orchestration: P1 basics -> funding -> battery (+EOA mode, +drift) -> classification."""
import asyncio
import re
import time

from common import *
import battery as B
import fund as F
import holder as HD

IMPL_SLOTS = {
    "eip1967_impl": "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc",
    "zos_impl": "0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3",
}
GAS_LADDER = [120_000_000, 60_000_000, 30_000_000, 15_000_000]
OP_GAS_CAP = 1_500_000
VIEW_GAS_CAP = 100_000
SCRIPT_GAS = 30_000_000


class Ctx:
    """Everything known about one token probe (used by classify)."""

    def __init__(self, chain, token, block, block_hex):
        self.chain, self.token, self.block, self.block_hex = chain, token, block, block_hex
        self.rows = []
        self.notes = {}
        self.funding = None
        self.fund_reason = None
        self.fund_info = {}
        self.results = {}       # script name -> {label: Step}
        self.script_errors = {}
        self.eoa = {}
        self.drift = {}
        self.code = None
        self.impl_code = None


async def probe_token(pool, chain_cfg, chain, token, block, block_hex, rec, drift_block=None, run_extras=True):
    ctx = Ctx(chain, token, block, block_hex)
    T = token
    bal_data = hx(cd("balanceOf(address)", H))
    # ---- P1 ---------------------------------------------------------------------------------------------------
    p1 = [
        ("eth_getCode", [T, block_hex], "code"),
        ("eth_createAccessList", [{"from": chain_cfg["rich"], "to": T, "data": bal_data}, block_hex], "acl_balanceOf"),
        ("eth_call", [{"from": E, "to": T, "data": hx(cd("decimals()"))}, block_hex], "decimals"),
        ("eth_call", [{"from": E, "to": T, "data": hx(cd("totalSupply()"))}, block_hex], "totalSupply"),
        ("eth_getStorageAt", [T, IMPL_SLOTS["eip1967_impl"], block_hex], "slot_eip1967_impl"),
        ("eth_getStorageAt", [T, IMPL_SLOTS["zos_impl"], block_hex], "slot_zos_impl"),
        ("eth_call", [{"from": E, "to": T, "data": bal_data}, block_hex], "balanceOf_baseline"),
        ("eth_call", [{"from": E, "to": T, "data": hx(cd("implementation()"))}, block_hex], "implementation()"),
        ("eth_getStorageAt", [T, "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50", block_hex], "slot_eip1967_beacon"),
    ]
    r = await pool.send(p1, rec)
    ctx.p1 = r
    if "error" in r[0]:     # transport failure, state unavailable, ... : NOT "no code"
        ctx.transport_error = r[0]["error"].get("message", str(r[0]["error"]))
        return ctx
    code = r[0].get("result")
    ctx.code = code
    if not code or code == "0x":
        ctx.no_code = True
        ctx.no_code_reason = "eth_getCode returned 0x at the pinned block (no contract: not deployed yet, self-destructed, or an EOA)"
        return ctx
    ctx.native_code = False
    if code.lower().startswith("0xef"):
        # EIP-7702 delegation designator (23 bytes: 0xef0100||addr) -> an EOA with delegated code, not a token we can use;
        # a bare 1-byte 0xef answering ERC-20 calls is a protocol-level (native) token, e.g. Base's 0xB2000... assets
        if len(code) >= 48:
            ctx.no_code = True
            ctx.no_code_reason = ("code is an EIP-7702 delegation designator (0xef0100||address): an EOA that delegates, "
                                  f"raw code {code[:60]}")
            return ctx
        ctx.native_code = True
    acl = r[1].get("result") if "result" in r[1] else None
    ctx.acl_err = r[1].get("error")
    ts = parse_u256(r[3])
    dec_raw = parse_u256(r[2])
    dec = dec_raw if dec_raw is not None and dec_raw <= 255 else None
    ctx.ts, ctx.dec = ts, dec
    impl = None
    for i in (4, 5):
        v = r[i].get("result")
        if v and int(v, 16):
            impl = "0x" + v[-40:]
            break
    impl = impl or minimal_proxy_target(code)
    if not impl:   # custom proxies that expose implementation()
        iv = parse_u256(r[7])
        if iv and iv < (1 << 160) and iv > 0x100:
            impl = "0x" + hex(iv)[2:].rjust(40, "0")
    ctx.impl = impl
    ctx.beacon = None
    bv = r[8].get("result")
    if bv and int(bv, 16):
        ctx.beacon = "0x" + bv[-40:]
    # baseline balanceOf(H) must work, otherwise no slot override can make it work
    base = r[6]
    baseline_error = None
    if "error" in base and not base.get("_transport_error"):
        baseline_error = "balanceOf(address) reverts at the pinned block for a fresh address: " + (base["error"].get("message") or "")[:200]
    # target balance and amounts
    tgt = max(min((ts or 0) // 2, 2 ** 90), 10 ** 6) if ts else 10 ** 24
    a_guess = B.choose_amounts(ts, dec, tgt)
    ctx.target = tgt
    # ---- funding ------------------------------------------------------------------------------------------------
    if baseline_error:
        fnd, reason, info = None, baseline_error, {"candidates_tried": [], "baseline_error": base["error"]}
    elif ctx.native_code:
        fnd, reason, info = None, ("native_protocol_token: eth_getCode is the single byte 0xef yet the address answers ERC-20 "
                                   "calls; it has no EVM storage, so state overrides are ignored"), {"candidates_tried": []}
    else:
        fnd, reason, info = await F.find_funding(pool, rec, T, block_hex, acl, tgt, max(a_guess // 2, 1))
    if fnd is None and not baseline_error:
        # last resort: a real holder funds the harness inside an eth_simulateV1 block
        hf, hreason, hinfo = await HD.find_holder_funding(pool, rec, T, block, block_hex, tgt, reason)
        info["holder_fallback"] = hinfo
        if hf is not None:
            fnd, reason = hf, None
        else:
            info["holder_fallback_reason"] = hreason
            reason = f"{reason}; holder fallback: {hreason}"
    ctx.holder_mode = fnd is not None and fnd.method == "real_holder"
    ctx.funding, ctx.fund_reason, ctx.fund_info = fnd, reason, info
    extras = []
    if not impl and ctx.beacon:
        bi = await pool.one("eth_call", [{"from": E, "to": ctx.beacon, "data": hx(cd("implementation()"))}, block_hex],
                            "beacon_implementation", rec)
        iv = parse_u256(bi)
        if iv and iv < (1 << 160) and iv > 0x100:
            impl = "0x" + hex(iv)[2:].rjust(40, "0")
            ctx.impl = impl
    if impl:
        extras.append(("eth_getCode", [impl, block_hex], "impl_code"))
    if fnd is not None:
        Bal = fnd.balance
        a = B.choose_amounts(ts, dec, Bal)
        ctx.a, ctx.B = a, Bal
        scripts = B.build_scripts(T, a, Bal, ts)
    else:
        # unfunded: still run the read-only views script (decimals/name/symbol/pause/blocklist/permit/accessors)
        Bal, a = 0, 0
        scripts = {"views": B.build_views(T)}
    names = list(scripts)
    ctx.script_names = names
    ov = {H: {"code": PROBE_CODE}, SINK: {"code": SINK_CODE}, REJ: {"code": REJ_CODE}}
    if fnd is not None and not ctx.holder_mode:
        ov = merge_overrides(ov, fnd.overrides())
    ctx.scripts = scripts

    async def run_group(group):
        data = hx(encode_run(T, OP_GAS_CAP, VIEW_GAS_CAP, SCRIPT_GAS, [scripts[n] for n in group]))
        last = None
        for g in GAS_LADDER:
            if ctx.holder_mode:
                sim = await pool.one("eth_simulateV1", HD.sim_params(
                    [fnd.prefix_call, {"from": E, "to": H, "data": data, "gas": hex(g)}], block_hex, ov),
                    f"battery_gas{g}", rec)
                rr = HD.sim_unwrap(sim, 1) if "result" in sim else sim
            else:
                rr = await pool.one("eth_call", [{"from": E, "to": H, "data": data, "gas": hex(g)}, block_hex, ov],
                                    f"battery_gas{g}", rec)
            last = rr
            msg = (rr.get("error") or {}).get("message", "")
            if "error" in rr and re.search(r"gas", msg, re.I) and not rr.get("_transport_error") \
                    and not re.search(r"abort|timeout", msg, re.I):
                continue
            break
        return last

    async def battery_call():
        """one eth_call with every script; if the node aborts it (execution timeout on a gas-hungry token) the scripts
        are re-sent in small groups so one pathological script cannot take the others down"""
        resp = await run_group(names)
        groups = [(names, resp)]
        msg = (resp.get("error") or {}).get("message", "")
        if "error" in resp and re.search(r"abort|timeout|timed out|deadline|gas|limit|too (large|big)", msg, re.I):
            chunks = [["views"] if "views" in names else []] + [
                [n for n in names[i:i + 6] if n != "views"] for i in range(0, len(names), 6)]
            chunks = [c for c in chunks if c]
            resps = await asyncio.gather(*[run_group(c) for c in chunks])
            groups = list(zip(chunks, resps))
        return groups

    eoa_items = []
    if run_extras and fnd is not None and not ctx.holder_mode:
        fo = fnd.overrides()
        frac = max(min((ts or 0) // 20 if ts else Bal // 2, Bal), 1)
        for lab, amt_ in (("eoa_transfer", a), ("eoa_big_frac", frac), ("eoa_zero", 0)):
            eoa_items.append(("eth_call", [{"from": H, "to": T, "data": hx(cd("transfer(address,uint256)", R, amt_))},
                                           block_hex, fo], lab))
    drift_items = []
    if run_extras and drift_block and fnd is not None and not ctx.holder_mode:
        fo = fnd.overrides()
        dh = hex(drift_block)
        drift_items = [
            ("eth_call", [{"from": E, "to": T, "data": bal_data}, dh, fo], "drift_balanceOf_old"),
            ("eth_call", [{"from": E, "to": T, "data": hx(cd("totalSupply()"))}, dh], "drift_totalSupply_old"),
        ]

    async def extras_call():
        if not extras and not eoa_items:
            return []
        return await pool.send(extras + eoa_items, rec)

    async def drift_call():
        if not drift_items:
            return []
        return await pool.send(drift_items, rec, need_archive=True)

    groups, ex, dr = await asyncio.gather(battery_call(), extras_call(), drift_call())
    ctx.battery_groups = groups
    ctx.battery_resp = groups[0][1]
    if extras:
        ctx.impl_code = ex[0].get("result")
        ex = ex[1:]
    ctx.eoa_resp = dict(zip([i[2] for i in eoa_items], ex))
    ctx.drift_resp = dict(zip([i[2] for i in drift_items], dr))
    ctx.drift_block = drift_block
    ctx.script_calls = {}
    ctx.battery_errors = []
    for gnames, br in groups:
        if "result" in br and br["result"]:
            try:
                outs = decode_run(unhex(br["result"]))
                for n, o in zip(gnames, outs):
                    ctx.script_calls[n] = br.get("_cid")
                    if o is None:
                        ctx.script_errors[n] = "script produced no payload (out of gas inside the harness / pathological token)"
                    else:
                        ctx.results[n] = {lab: o[i] for lab, i in scripts[n].labels.items()}
            except Exception as e:  # decoding problem is a harness bug: surface it
                ctx.battery_decode_error = f"{type(e).__name__}: {e}"
        else:
            ctx.battery_errors.append({"scripts": gnames, "error": br.get("error"), "cid": br.get("_cid")})
            for n in gnames:
                ctx.script_errors[n] = "harness eth_call failed: " + str((br.get("error") or {}).get("message", ""))[:160]
    return ctx
