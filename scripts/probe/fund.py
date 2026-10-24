"""Balance-slot discovery: give the harness address a known balance of the token via an eth_call storage override.

Strategy
 1. `eth_createAccessList` of balanceOf(H) (H = a fresh address, so every storage read is a zero SLOAD) lists every
    storage slot balanceOf touches. Slots that are plain small integers or EIP-1967 constants are deprioritised; the
    remaining (hash-like) keys are the candidates. Works for proxies (keys are listed under the proxy address) and for
    tokens whose balances live in a separate storage contract (the key is listed under that contract's address).
 2. Each candidate is overridden (stateDiff) with two probe values (2^64+7 and 2^250+2^64+7) and balanceOf(H) is read.
    Whatever the slot->balance map is (identity, shares x rate, gons / gonsPerFragment, reflection r/rate, masked
    uint96/uint128 packing), the ratio balance/slot is calibrated and the slot value is scaled so the harness ends up with
    the TARGET balance; the result is verified by reading balanceOf again (up to 3 secant refinements).
 3. If the access list is unavailable (some RPCs reject it) or yields nothing usable, brute force: solidity
    keccak(H . slot) and vyper keccak(slot . H) for slot 0..N plus OZ-v5 ERC-7201 namespaced bases, probed 70 keys per
    eth_call and bisected.
 4. For identity-mapped slots the bit width of the balance field is measured (probe 2^95, 2^96, 2^127, 2^128 ...), which
    exposes uint96 (UNI/COMP) and uint128 balances without needing >2^96 funds.
"""
import itertools

from common import *

SA = (1 << 64) + 7
SB = (1 << 250) + (1 << 64) + 7
EIP1967 = {
    int("0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc", 16),
    int("0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103", 16),
    int("0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50", 16),
    int("0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3", 16),
}
ERC7201_ERC20 = int("0x52c63247e1f47db19d5ce0460030c497f067ca4cebf71ba98eeadabe20bace00", 16)
LADDER = [95, 96, 103, 104, 111, 112, 127, 128, 159, 160, 191, 192]


class Funding:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def overrides(self):
        if self.method == "native_balance":
            return {H: {"balance": hex(self.value)}}
        ov = {self.addr: {"stateDiff": {self.key: word(self.value)}}}
        for (a, k, v) in getattr(self, "companions", []):
            ov.setdefault(a, {}).setdefault("stateDiff", {})[k] = word(v)
        return ov

    def summary(self):
        return {k: v for k, v in self.__dict__.items() if k not in ("cids",)}


def _clean(s):
    """Scaled (non-identity) slot values: clear the low 16 bits. Tokens that store `balance << k | flags` (frozen /
    blacklisted / role bits in the low bits, e.g. Agora AUSD) would otherwise see random flags set by the probe
    value's low bits and revert with 'account frozen'."""
    return (s >> 16) << 16 if s >= (1 << 40) else s


def _ov(addr, key, value, comp=()):
    ov = {addr: {"stateDiff": {key: word(value)}}}
    for (a, k, v) in comp:
        ov.setdefault(a, {}).setdefault("stateDiff", {})[k] = word(v)
    return ov


def candidates_from_acl(acl, token):
    """-> list of (addr, key) ordered by priority"""
    primary, small, other = [], [], []
    if not acl:
        return []
    for ent in acl.get("accessList", []):
        a = ent["address"].lower()
        ai = int(a, 16)
        if ai < 0x100 or a in (H.lower(), SINK.lower(), REJ.lower(), E.lower()):
            continue
        for k in ent.get("storageKeys", []):
            ki = int(k, 16)
            if ki in EIP1967:
                continue
            key = "0x" + k[2:].rjust(64, "0")
            if ki < (1 << 32):
                small.append((a, key))
            elif a == token.lower():
                primary.append((a, key))
            else:
                other.append((a, key))
    return primary + other + small


def brute_keys():
    """(label, key) for H under solidity and vyper layouts"""
    slots = list(range(0, 201)) + [ERC7201_ERC20, ERC7201_ERC20 + 1]
    out = []
    hp = pad_addr(H)
    for s in slots:
        sp = s.to_bytes(32, "big")
        out.append((f"sol:{s}", hx(keccak(hp + sp))))
        out.append((f"vy:{s}", hx(keccak(sp + hp))))
    return out


async def _bal_calls(pool, rec, token, block, probes, tag):
    """probes: list of overrides dicts -> list of int|None (balanceOf(H) per override)"""
    data = hx(cd("balanceOf(address)", H))
    items = [("eth_call", [{"from": E, "to": token, "data": data}, block, ov], tag) for ov in probes]
    res = await pool.send(items, rec)
    out = []
    for r in res:
        v = None
        if "result" in r and r["result"] and len(r["result"]) >= 66:
            v = int(r["result"][:66], 16)
        out.append((v, r))
    return out


async def find_funding(pool, rec, token, block, acl, target, amt):
    """Return (Funding|None, reason|None, info dict). `target` = desired balance, `amt` = smallest balance we accept
    (>= 20 test amounts)."""
    info = {"candidates_tried": [], "acl_ok": acl is not None}
    token_l = token.lower()
    cands = candidates_from_acl(acl, token)[:8]
    method = "access_list"
    f = None
    if cands:
        f = await _try_candidates(pool, rec, token, block, cands, target, amt, info)
    if f is None:
        method = "brute_force"
        f = await _brute(pool, rec, token, block, target, amt, info)
    if f is None:
        method = "native_balance"
        f = await _native(pool, rec, token, block, target, amt, info)
    if f is None:
        return None, "no_balance_slot_found: access-list candidates, brute-force slot layouts and native-balance override all failed", info
    f.method = method
    return f, None, info


async def _try_candidates(pool, rec, token, block, cands, target, amt, info):
    probes = []
    for (a, k) in cands:
        probes.append(_ov(a, k, SA))
        probes.append(_ov(a, k, SB))
    res = await _bal_calls(pool, rec, token, block, probes, "fund_probe")
    scored = []
    for i, (a, k) in enumerate(cands):
        (ba, ra), (bb, rb) = res[2 * i], res[2 * i + 1]
        info["candidates_tried"].append({"addr": a, "key": k, "b_SA": ba, "b_SB": bb,
                                         "cids": [ra.get("_cid"), rb.get("_cid")]})
        if not ba and not bb:
            continue
        # prefer identity maps, then anything nonzero with the best precision
        pri = 0 if ba == SA else (1 if bb == SB else 2)
        scored.append((pri, i, a, k, ba, bb))
    scored.sort()
    for pri, i, a, k, ba, bb in scored[:3]:
        f = await _calibrate(pool, rec, token, block, a, k, ba, bb, target, amt, info)
        if f is not None:
            f.cands_probe_cids = info["candidates_tried"][i]["cids"]
            return f
    # joint pass: balance may be a function of two adjacent slots of one struct (principal x index / snapshot, ...).
    # Try pairs of hash-like keys <= 8 apart: variable slot = S, companion = 1 or = S.
    pairs = []
    for i, (a, k) in enumerate(cands):
        for j, (a2, k2) in enumerate(cands):
            if i != j and a == a2 and 0 < abs(int(k, 16) - int(k2, 16)) <= 8 and int(k, 16) >= (1 << 32):
                pairs.append(((a, k), (a2, k2)))
    for (a, k), (a2, k2) in pairs[:4]:
        for compv in (1, "S"):
            def mk(S, a=a, k=k, a2=a2, k2=k2, compv=compv):
                return _ov(a, k, S, [(a2, k2, S if compv == "S" else compv)])
            res = await _bal_calls(pool, rec, token, block, [mk(SA), mk(SB)], "fund_probe_joint")
            (ba, ra), (bb, rb) = res
            info["candidates_tried"].append({"addr": a, "key": k, "companion_key": k2, "companion_value": compv,
                                             "b_SA": ba, "b_SB": bb, "cids": [ra.get("_cid"), rb.get("_cid")]})
            if ba or bb:
                f = await _calibrate(pool, rec, token, block, a, k, ba, bb, target, amt, info,
                                     comp=[(a2, k2, 1)] if compv == 1 else None, comp_same=(compv == "S"), k2=(a2, k2))
                if f is not None:
                    return f
    return None


async def _calibrate(pool, rec, token, block, addr, key, ba, bb, target, amt, info, comp=None, comp_same=False, k2=None):
    # initial slot-value estimate
    if ba == SA or bb == SB:
        S, ratio = target, 1.0
    else:
        s0, b0 = (SB, bb) if (bb or 0) > (ba or 0) else (SA, ba)
        S = _clean(max(1, -(-target * s0 // b0)))
        ratio = b0 / s0
    last = None
    hist = []
    def mkov(Sv):
        if comp_same:
            return _ov(addr, key, Sv, [(k2[0], k2[1], Sv)])
        return _ov(addr, key, Sv, comp or ())

    for rnd in range(4):
        probes = [mkov(S)]
        ladder_idx = {}
        if rnd == 0 and (ba == SA or bb == SB):
            for kbit in LADDER:
                ladder_idx[kbit] = len(probes)
                probes.append(mkov(1 << kbit))
        res = await _bal_calls(pool, rec, token, block, probes, "fund_verify")
        b, r = res[0]
        hist.append({"slot_value": S, "balance": b, "cid": r.get("_cid")})
        if rnd == 0 and ladder_idx:
            fits = {}
            for kbit, j in ladder_idx.items():
                v = res[j][0]
                fits[kbit] = (v == (1 << kbit))
            info["width_probes"] = {str(k): v for k, v in fits.items()}
            info["width_cids"] = [res[j][1].get("_cid") for j in ladder_idx.values()]
        if b is None or b == 0:
            # value masked away / reverted: try a direct value once
            if rnd == 0 and S != target:
                S = target
                continue
            break
        last = (S, b)
        if abs(b - target) * 100 <= target:     # within 1%
            break
        if b >= amt * 20 and rnd >= 1:
            break
        # secant step (linear model)
        S2 = _clean(max(1, S * target // b))
        if S2 == S:
            break
        S = S2
    info.setdefault("calibration", []).append({"addr": addr, "key": key, "rounds": hist})
    if not last or last[1] < amt * 20:
        return None
    S, b = last
    bits = None
    bits_lo = None
    ratio_exact = (b == S)
    wp = info.get("width_probes")
    if wp and ratio_exact:
        # largest ladder bit that still fits => balance field width is at least k+1 ; first failing bit => width == that bit
        fail = [int(k) for k, v in wp.items() if not v]
        okk = [int(k) for k, v in wp.items() if v]
        bits = min(fail) if fail else 256                      # balance field is at most this many bits wide
        bits_lo = (max(okk) + 1) if okk else 0                 # ... and at least this many
    fo = Funding(addr=addr, key=key, value=S, balance=b, ratio=(b / S), identity=ratio_exact,
                 balance_bits=bits, balance_bits_lo=bits_lo, cids=[h["cid"] for h in hist])
    if comp:
        fo.companions = list(comp)
    elif comp_same:
        fo.companions = [(k2[0], k2[1], S)]
        fo.companion_same_as_slot = True
    return fo


async def _brute(pool, rec, token, block, target, amt, info):
    keys = brute_keys()
    GROUP = 70
    groups = [keys[i:i + GROUP] for i in range(0, len(keys), GROUP)]
    ovs = [{token.lower(): {"stateDiff": {k: word(SB) for _, k in g}}} for g in groups]
    res = await _bal_calls(pool, rec, token, block, ovs, "brute_group")
    hit = None
    info["brute_groups"] = [{"n": len(g), "b": r[0], "cid": r[1].get("_cid")} for g, r in zip(groups, res)]
    cur = [g for g, r in zip(groups, res) if r[0]]
    if not cur:
        return None
    cur = cur[0]
    # bisect to one key
    while len(cur) > 1:
        step = max(1, -(-len(cur) // 8))
        subs = [cur[i:i + step] for i in range(0, len(cur), step)]
        ovs = [{token.lower(): {"stateDiff": {k: word(SB) for _, k in g}}} for g in subs]
        res = await _bal_calls(pool, rec, token, block, ovs, "brute_bisect")
        nxt = [g for g, r in zip(subs, res) if r[0]]
        if not nxt:
            return None
        cur = nxt[0]
    label, key = cur[0]
    info["brute_hit"] = label
    # calibrate that single key
    res = await _bal_calls(pool, rec, token, block, [_ov(token.lower(), key, SA), _ov(token.lower(), key, SB)], "fund_probe")
    ba, bb = res[0][0], res[1][0]
    info["candidates_tried"].append({"addr": token.lower(), "key": key, "b_SA": ba, "b_SB": bb, "brute": label})
    if not ba and not bb:
        return None
    f = await _calibrate(pool, rec, token, block, token.lower(), key, ba, bb, target, amt, info)
    if f:
        f.brute_label = label
    return f


async def _native(pool, rec, token, block, target, amt, info):
    """Tokens whose balanceOf(x) is x.balance (POL on Polygon, CELO, zkSync ETH ...): override the native balance of H."""
    probes = [{H: {"balance": hex(SA)}}, {H: {"balance": hex(target)}}]
    res = await _bal_calls(pool, rec, token, block, probes, "fund_native")
    (ba, ra), (bt, rt) = res
    info["native_probe"] = {"b_SA": ba, "b_target": bt, "cids": [ra.get("_cid"), rt.get("_cid")]}
    if ba == SA and bt is not None and bt >= amt * 20:
        return Funding(addr=H, key="native_balance", value=target, balance=bt, ratio=bt / target, identity=(bt == target),
                       balance_bits=None, balance_bits_lo=None, cids=[rt.get("_cid"), ra.get("_cid")])
    return None
