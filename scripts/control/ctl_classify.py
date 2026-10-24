"""Core of the W3 control classifier: proxy detection, ownership / role discovery, controller classification.

Public API:  Env(...), probe_token(env, token) -> row dict, classify_addr(env, addr) -> node dict.
See README.md for the semantics of every output field.
"""
import json
import os
import pathlib
import threading
import time

from ctl_abi import (CAP_BY_SEL, GETTER_BUCKET, OWNER_GETTERS, PRIV_CAPS, ROLE_BUCKET, ROLE_HASH, ROLE_NAMES, SEL,
                     SLOTS, VIEWS, WEAK_CAPS, ZERO, DEAD, capabilities, clone_target, dec_addr, dec_addr_array,
                     dec_bool, dec_str, dec_uint, is_7702, k, pad_addr, scan_code, sel)
from ctl_rpc import CHAINS, Client, Ev, Result, etherscan_get, now_iso
from legacy_forward import has_legacy_forward

MAX_DEPTH = 4
ETHERSCAN_FREE_CHAINS = {"ethereum", "arbitrum", "polygon"}  # chains the free Etherscan v2 key can query (tested 2026-10-21)
L2_ALIAS = {"arbitrum", "optimism", "base"}
ALIAS_OFFSET = 0x1111000000000000000000000000000000001111
ROLE_GRANTED = "0x2f8788117e7eff1d82e926ec794901d17c78024a50270940304540a733656f0d"
EMPTY32 = "00" * 32
APP_MANAGER_ROLE = "0x" + k("APP_MANAGER_ROLE").hex()
UPGRADE_SELS = {sel("upgradeTo(address)"): "upgradeTo", sel("upgradeToAndCall(address,bytes)"): "upgradeToAndCall"}
TRACE_PROBE = "0x313ce567"  # decimals()


# =====================================================================================================================
# Environment
# =====================================================================================================================
class Env:
    def __init__(self, chain, out_root, block=None, verbose=False, l1=True, endpoints=None, block_is_head=True):
        self.chain = chain
        self.out_root = pathlib.Path(out_root)
        self.out_root.mkdir(parents=True, exist_ok=True)
        self.client = Client(chain, self.out_root, block_is_head=block_is_head, endpoints=endpoints, verbose=verbose)
        self.verbose = verbose
        self._block_int = None
        self._block_hex = None
        self.block_meta = {}
        self.rolling = False          # True: every token is probed at a freshly pinned head-2 block (thread-local pin)
        self._tl = threading.local()
        self._head = (0.0, None)
        self.ctrl = {}
        self.ctrl_locks = {}
        self.glock = threading.Lock()
        self.ctrl_file = self.out_root / "controllers.jsonl"
        self.l1_enabled = l1 and chain in L2_ALIAS
        self._l1 = None
        self.etherscan_ok = chain in ETHERSCAN_FREE_CHAINS and not os.environ.get("CTL_NO_ETHERSCAN")
        self.writelock = threading.Lock()

    # ---- block pinning -------------------------------------------------------------------------------------------
    @property
    def block_int(self):
        return getattr(self._tl, "block", None) or self._block_int

    @block_int.setter
    def block_int(self, v):
        self._block_int = v

    @property
    def block_hex(self):
        b = getattr(self._tl, "block", None)
        if b and self.rolling:
            return self.client.BLK  # resolved to a fresh head-2 block when the request is actually sent
        return hex(b) if b else self._block_hex

    @block_hex.setter
    def block_hex(self, v):
        self._block_hex = v

    def repin_thread(self, force=False):
        """Rolling mode: pin THIS thread to a fresh head-2 block (head cached for 3 s). Public RPCs only keep recent state,
        so long runs cannot stay on one block; every row / evidence line records the block it was read at."""
        t, h = self._head
        if h is None or force or time.time() - t > 3:
            ev = Ev("evidence/_head.jsonl", self.out_root)
            r = self.client.one(ev, "eth_blockNumber", [])
            if r.ok:
                h = int(r.value, 16) - 2
                self._head = (time.time(), h)
        if h is not None:
            self._tl.block = h
            if not hasattr(self._tl, "blocks"):
                self._tl.blocks = set()
            self._tl.blocks.add(h)

    def pin_block(self, block):
        ev = Ev("evidence/_block.jsonl", self.out_root)
        if block in (None, "latest"):
            r = self.client.one(ev, "eth_blockNumber", [])
            if not r.ok:
                raise RuntimeError(f"cannot resolve head block: {r.msg}")
            self.block_int = int(r.value, 16) - 2  # lag 2 blocks: load-balanced nodes may not have the head yet
        else:
            self.block_int = int(block)
        self.block_hex = hex(self.block_int)
        r = self.client.one(ev, "eth_getBlockByNumber", [self.block_hex, False], block=self.block_hex)
        if r.ok and r.value:
            self.block_meta = {"number": self.block_int, "hash": r.value.get("hash"),
                               "timestamp": int(r.value.get("timestamp", "0x0"), 16), "evidence": r.ev}
        else:
            self.block_meta = {"number": self.block_int, "error": r.msg, "evidence": r.ev}
        ev.flush()
        return self.block_int

    def load_ctrl_cache(self):
        if self.ctrl_file.exists():
            for line in self.ctrl_file.read_text().splitlines():
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if self.rolling or j.get("block") == self.block_int:
                    self.ctrl[j["address"]] = j["node"]

    def l1_env(self):
        if self._l1 is None:
            with self.glock:
                if self._l1 is None:
                    e = Env("ethereum", self.out_root / "_l1ethereum", l1=False, verbose=self.verbose)
                    e.pin_block("latest")
                    e.load_ctrl_cache()
                    self._l1 = e
        return self._l1

    def save_node(self, addr, node):
        with self.writelock:
            with open(self.ctrl_file, "a") as f:
                f.write(json.dumps({"address": addr, "block": self.block_int, "node": node}, separators=(",", ":")) + "\n")


# =====================================================================================================================
# Query helper
# =====================================================================================================================
class Q:
    def __init__(self, env, ev):
        self.env, self.ev, self.items, self.keys = env, ev, [], []

    def code(self, a, key=None):
        self._add(key or ("code", a), "eth_getCode", [a, self.env.block_hex])

    def nonce(self, a, key=None):
        self._add(key or ("nonce", a), "eth_getTransactionCount", [a, self.env.block_hex])

    def slot(self, a, slot, key=None):
        self._add(key or ("slot", a, slot), "eth_getStorageAt", [a, SLOTS.get(slot, slot), self.env.block_hex])

    def call(self, a, data, key=None):
        self._add(key or ("call", a, data), "eth_call", [{"to": a, "data": data}, self.env.block_hex])

    def view(self, a, name):
        self.call(a, VIEWS[name], ("v", a, name))

    def _add(self, key, m, p):
        if key in self.keys:
            return
        self.keys.append(key)
        self.items.append((m, p))

    def run(self):
        res = self.env.client.batch(self.ev, self.items, block=self.env.block_hex) if self.items else []
        return dict(zip(self.keys, res))


def addr_of(res):
    return dec_addr(res.value) if res is not None and res.ok else None


def nz(a):
    return a is not None and a != ZERO


def slot_addr(res):
    """Address stored in a storage slot (non-zero) or None."""
    if res is None or not res.ok:
        return None
    a = dec_addr(res.value)
    return a if nz(a) else None


def errors_of(results):
    return [{"key": str(k_), "msg": r.msg, "ev": r.ev} for k_, r in results.items() if r.kind == "error"]


# =====================================================================================================================
# Proxy detection (shared by tokens and controllers)
# =====================================================================================================================
def detect_proxy_basic(addr, code, scan, r):
    """Decide the proxy type from round-1 results `r` (dict keyed by ('slot', addr, name) / ('v', addr, name))."""
    S = lambda n: r.get(("slot", addr, n))  # noqa: E731
    V = lambda n: r.get(("v", addr, n))  # noqa: E731
    out = {"proxy_type": None, "implementation": None, "admin_slot": None, "beacon": None, "confidence": None,
           "via": None, "ev": [], "admin_slot_name": None, "kernel": None, "facets": None}
    cl = clone_target(code)
    if cl:
        out.update(proxy_type=cl[0], implementation=cl[1], confidence="bytecode_pattern", via="eip1167_bytecode",
                   immutable_clone=True)
        return out
    adm = slot_addr(S("eip1967_admin"))
    if slot_addr(S("eip1967_impl")):
        out.update(proxy_type="eip1967", implementation=slot_addr(S("eip1967_impl")), admin_slot=adm,
                   admin_slot_name="eip1967_admin", confidence="storage_slot", via="eip1967_impl_slot",
                   ev=[S("eip1967_impl").ev, S("eip1967_admin").ev])
        return out
    if slot_addr(S("eip1967_beacon")):
        out.update(proxy_type="eip1967_beacon", beacon=slot_addr(S("eip1967_beacon")), confidence="storage_slot",
                   via="eip1967_beacon_slot", ev=[S("eip1967_beacon").ev])
        return out
    if slot_addr(S("zos_impl")):
        out.update(proxy_type="zos_legacy", implementation=slot_addr(S("zos_impl")),
                   admin_slot=slot_addr(S("zos_admin")), admin_slot_name="zos_admin", confidence="storage_slot",
                   via="zos_impl_slot", ev=[S("zos_impl").ev, S("zos_admin").ev])
        return out
    if slot_addr(S("eip1822_proxiable")):
        out.update(proxy_type="eip1822_uups", implementation=slot_addr(S("eip1822_proxiable")),
                   confidence="storage_slot", via="eip1822_proxiable_slot", ev=[S("eip1822_proxiable").ev])
        return out
    kern, impl_g = addr_of(V("kernel")), addr_of(V("implementation"))
    if nz(kern) and nz(impl_g):
        pt = dec_uint(V("proxyType").value) if V("proxyType") is not None and V("proxyType").ok else None
        out.update(proxy_type="aragon_app_proxy", implementation=impl_g, kernel=kern, confidence="getter",
                   via="implementation()+kernel()", ev=[V("kernel").ev, V("implementation").ev], eip897_type=pt)
        return out
    fac = V("facetAddresses")
    if fac is not None and fac.ok:
        arr = dec_addr_array(fac.value)
        if arr and arr["count"] > 0:
            out.update(proxy_type="eip2535_diamond", facets=arr, confidence="getter", via="facetAddresses()",
                       ev=[fac.ev])
            return out
    if nz(impl_g):
        pt = V("proxyType")
        if pt is not None and pt.ok and dec_uint(pt.value) in (1, 2):
            out.update(proxy_type="eip897", implementation=impl_g, confidence="getter",
                       via="implementation()+proxyType()", ev=[V("implementation").ev, pt.ev],
                       eip897_type=dec_uint(pt.value))
            return out
        out.update(proxy_type="implementation_getter", implementation=impl_g, confidence="getter_only",
                   via="implementation()", ev=[V("implementation").ev])
        return out
    tg = addr_of(V("target"))
    if nz(tg) and scan["size"] < 8000:
        out.update(proxy_type="synthetix_target_proxy", implementation=tg, confidence="getter_only", via="target()",
                   ev=[V("target").ev])
        return out
    gi = addr_of(V("getImplementation"))
    if nz(gi):
        out.update(proxy_type="implementation_getter", implementation=gi, confidence="getter_only",
                   via="getImplementation()", ev=[V("getImplementation").ev])
        return out
    return out


def delegate_targets(trace, frm):
    found = []

    def walk(n):
        if not isinstance(n, dict):
            return
        if n.get("type") == "DELEGATECALL" and str(n.get("from", "")).lower() == frm:
            found.append(str(n.get("to", "")).lower())
        for c in n.get("calls", []) or []:
            walk(c)

    walk(trace)
    return found


# =====================================================================================================================
# Controller classification (cached per address; evidence in evidence/_ctrl/<addr>.jsonl)
# =====================================================================================================================
CTRL_VIEWS = ["getThreshold", "getOwners", "VERSION", "required", "getMinDelay", "delay", "admin", "owner", "getAdmin",
              "getDelay", "votingPeriod", "COUNTING_MODE", "proposalCount", "quorumVotes", "timelock", "authority",
              "hat", "acl", "voteTime", "supportRequiredPct", "implementation", "proxyType", "kernel", "pendingOwner",
              "token", "proposalThreshold", "getPayloadsCount", "guardian", "appId"]


def node(addr, kind, summary=None, details=None, nxt=None, ev=None):
    return {"address": addr, "kind": kind, "summary": summary or kind, "details": details or {}, "next": nxt or [],
            "ev": [e for e in (ev or []) if e]}


def classify_addr(env, addr):
    addr = addr.lower()
    if addr in env.ctrl:
        return env.ctrl[addr]
    with env.glock:
        lk = env.ctrl_locks.setdefault(addr, threading.Lock())
    with lk:
        if addr in env.ctrl:
            return env.ctrl[addr]
        nd = None
        for attempt in range(3):  # transient state refusals / rate limits must not leave a poisoned "unclassified" node behind
            try:
                nd = _classify(env, addr)
            except Exception as e:  # noqa: BLE001
                nd = node(addr, "unclassified", "unclassified (internal error)", {"error": repr(e)[:300]})
                if os.environ.get("CTL_DEBUG"):
                    raise
            if nd["kind"] != "unclassified":
                break
            time.sleep(2 * (attempt + 1))
        nd["block"] = env.block_int
        if nd["kind"] == "unclassified":
            return nd  # not cached (neither in memory nor on disk): the next token retries it
        env.ctrl[addr] = nd
        if not nd["details"].get("probe_errors"):
            env.save_node(addr, nd)
        return nd


def _classify(env, addr):
    if addr == ZERO:
        return node(addr, "renounced", "renounced (zero address)", {"flavor": "zero_address"})
    if addr == DEAD or addr.startswith("0x000000000000000000000000000000000000dead"):
        return node(addr, "renounced", "renounced (0x...dEaD)", {"flavor": "dead_address"})
    if int(addr, 16) <= 0xffff:
        return node(addr, "renounced", f"renounced (burn-style address {addr[:6]}…{addr[-4:]} in the EVM precompile range: no signing key can exist)",
                    {"flavor": "precompile_range_address"})
    ev = Ev(f"evidence/_ctrl/{addr}.jsonl", env.out_root)
    try:
        q = Q(env, ev)
        q.code(addr)
        q.nonce(addr)
        r = q.run()
        code_r = r[("code", addr)]
        if not code_r.ok:
            return node(addr, "unclassified", "unclassified (getCode failed)",
                        {"probe_errors": errors_of(r), "error": code_r.msg}, ev=[code_r.ev])
        code = code_r.value or "0x"
        nonce = dec_uint_any(r[("nonce", addr)])
        if code == "0x":
            return _eoa(env, addr, nonce, [code_r.ev, r[("nonce", addr)].ev], ev)
        d7 = is_7702(code)
        if d7:
            return node(addr, "eoa_7702", f"EOA with EIP-7702 delegation -> {d7}",
                        {"delegate": d7, "nonce": nonce}, ev=[code_r.ev, r[("nonce", addr)].ev])
        return _contract(env, addr, code, ev, [code_r.ev])
    finally:
        ev.flush()


def dec_uint_any(res):
    try:
        return int(res.value, 16) if res.ok else None
    except Exception:  # noqa: BLE001
        return None


def _eoa(env, addr, nonce, evrefs, ev):
    base_details = {"nonce": nonce}
    if env.l1_enabled:
        l1a = "0x" + ((int(addr, 16) - ALIAS_OFFSET) % (1 << 160)).to_bytes(20, "big").hex()
        l1 = env.l1_env()
        l1_ev = Ev(f"evidence/_ctrl/l1alias_{addr}.jsonl", env.out_root)
        res = l1.client.one(l1_ev, "eth_getCode", [l1a, l1.block_hex], block=l1.block_hex)
        l1_ev.flush()
        if res.ok and res.value not in ("0x", None) and not is_7702(res.value):
            l1node = classify_addr(l1, l1a)
            return node(addr, "l1_alias", f"L1-alias of {l1a} ({l1node['summary']})",
                        {"l1_address": l1a, "l1_block": l1.block_int, "l1_node": l1node, "nonce": nonce,
                         "note": "address = L1 contract + 0x1111...1111 (cross-domain alias); controlled by the L1 contract"},
                        ev=evrefs + [res.ev])
        base_details["l1_alias_check"] = {"l1_address": l1a, "result": "no code" if res.ok else f"error: {res.msg}",
                                          "ev": res.ev}
    return node(addr, "eoa", "EOA (no code)", base_details, ev=evrefs)


def _contract(env, addr, code, ev, evrefs):
    scan = scan_code(code)
    q = Q(env, ev)
    for s in ("eip1967_impl", "eip1967_admin", "eip1967_beacon", "zos_impl", "zos_admin", "slot0", "eip1822_proxiable"):
        q.slot(addr, s, ("slot", addr, s))
    for n in CTRL_VIEWS:
        q.view(addr, n)
    q.call(addr, SEL["hasRole"] + EMPTY32 + pad_addr(ZERO), ("hasRole0", addr))
    q.call(addr, SEL["getRoleMemberCount"] + EMPTY32, ("rmc0", addr))
    q.call(addr, VIEWS["facetAddresses"], ("v", addr, "facetAddresses"))
    r = q.run()
    V = lambda n: r.get(("v", addr, n))  # noqa: E731
    errs = errors_of(r)
    px = detect_proxy_basic(addr, code, scan, r)
    impl = px["implementation"]
    union = set(scan["selectors"])
    # follow the implementation once to get its selectors (needed to recognise ProxyAdmin / AccessControl)
    impl_code_ref = None
    if impl:
        q2 = Q(env, ev)
        q2.code(impl)
        r2 = q2.run()
        errs += errors_of(r2)
        ic = r2[("code", impl)]
        if ic.ok and ic.value not in (None, "0x"):
            union |= scan_code(ic.value)["selectors"]
            impl_code_ref = ic.ev
    elif scan["delegatecall"] and scan["size"] < 6000 and px["proxy_type"] is None:
        # tiny contract with DELEGATECALL: may be a Safe-style proxy (masterCopy at slot 0)
        s0 = slot_addr(r.get(("slot", addr, "slot0")))
        if nz(s0):
            px.update(proxy_type="slot0_delegate_proxy", implementation=s0, confidence="storage_slot+bytecode",
                      via="slot0 (masterCopy)", ev=[r[("slot", addr, "slot0")].ev])
            impl = s0
            q2 = Q(env, ev)
            q2.code(impl)
            r2 = q2.run()
            ic = r2[("code", impl)]
            if ic.ok and ic.value not in (None, "0x"):
                union |= scan_code(ic.value)["selectors"]
    ok = lambda n: V(n) is not None and V(n).ok and V(n).value not in (None, "0x")  # noqa: E731
    owner, adm_c = addr_of(V("owner")), addr_of(V("admin"))
    details = {"code_size": scan["size"], "probe_errors": errs}
    if px["proxy_type"]:
        details["proxy"] = {"type": px["proxy_type"], "implementation": impl, "beacon": px["beacon"],
                            "admin_slot": px["admin_slot"], "confidence": px["confidence"]}
    evr = evrefs + [e for e in px["ev"] if e]

    # ---- Safe ----
    thr, ow = V("getThreshold"), V("getOwners")
    if ok("getThreshold") and ok("getOwners"):
        t = dec_uint(thr.value)
        arr = dec_addr_array(ow.value)
        ver = dec_str(V("VERSION").value) if ok("VERSION") else None
        if arr is not None and t is not None:
            details.update(threshold=t, owners=arr["items"], owner_count=arr["count"], version=ver,
                           singleton=impl)
            sole = [{"address": arr["items"][0], "label": "sole signer of a 1-of-1 Safe"}] if (t == 1 and arr["count"] == 1) else []
            return node(addr, "safe", f"Safe {t}-of-{arr['count']}" + (f" v{ver}" if ver else ""), details, nxt=sole,
                        ev=evr + [thr.ev, ow.ev])
    if ok("required") and ok("getOwners"):
        t = dec_uint(V("required").value)
        arr = dec_addr_array(ow.value)
        if arr is not None and t is not None:
            details.update(threshold=t, owners=arr["items"], owner_count=arr["count"])
            sole = [{"address": arr["items"][0], "label": "sole signer of a 1-of-1 multisig"}] if (t == 1 and arr["count"] == 1) else []
            return node(addr, "multisig_legacy", f"legacy multisig {t}-of-{arr['count']}", details, nxt=sole,
                        ev=evr + [V("required").ev, ow.ev])
    # ---- timelocks ----
    if ok("getMinDelay"):
        delay = dec_uint(V("getMinDelay").value)
        details.update(delay_seconds=delay, flavor="openzeppelin_timelock_controller")
        holders = ac_holders(env, ev, addr, union, errs, only=["PROPOSER_ROLE", "EXECUTOR_ROLE", "CANCELLER_ROLE",
                                                               "TIMELOCK_ADMIN_ROLE", "DEFAULT_ADMIN_ROLE"])
        details["role_holders"] = holders["by_role"]
        details["role_holders_status"] = holders["status"]
        nxt = []
        for h in holders["by_role"].get("PROPOSER_ROLE", []):
            nxt.append({"label": "PROPOSER_ROLE", "address": h["address"]})
        for h in holders["by_role"].get("TIMELOCK_ADMIN_ROLE", []) + holders["by_role"].get("DEFAULT_ADMIN_ROLE", []):
            if h["address"] != addr:
                nxt.append({"label": "TIMELOCK_ADMIN_ROLE", "address": h["address"]})
        details["probe_errors"] = errs
        return node(addr, "timelock", f"timelock {fmt_delay(delay)} (OZ TimelockController)", details, nxt,
                    ev=evr + [V("getMinDelay").ev] + holders["ev"])
    if ok("delay") and ok("admin") and dec_uint(V("delay").value) is not None:
        delay = dec_uint(V("delay").value)
        a = addr_of(V("admin"))
        details.update(delay_seconds=delay, flavor="compound_timelock")
        return node(addr, "timelock", f"timelock {fmt_delay(delay)} (Compound-style)", details,
                    [{"label": "admin", "address": a}] if a is not None else [], ev=evr + [V("delay").ev, V("admin").ev])
    if ok("getDelay") and ok("getAdmin"):
        delay = dec_uint(V("getDelay").value)
        a = addr_of(V("getAdmin"))
        details.update(delay_seconds=delay, flavor="aave_executor")
        return node(addr, "timelock", f"timelock {fmt_delay(delay)} (Aave-style executor)", details,
                    [{"label": "getAdmin", "address": a}] if a is not None else [],
                    ev=evr + [V("getDelay").ev, V("getAdmin").ev])
    if ok("delay") and dec_uint(V("delay").value) is not None and (ok("owner") or ok("authority")):
        delay = dec_uint(V("delay").value)
        nxt = []
        for n in ("owner", "authority"):
            a = addr_of(V(n))
            if nz(a):
                nxt.append({"label": n, "address": a})
        details.update(delay_seconds=delay, flavor="ds_pause_like")
        return node(addr, "timelock", f"timelock {fmt_delay(delay)} (DSPause-style)", details, nxt,
                    ev=evr + [V("delay").ev])
    # ---- governors / DAOs ----
    if ok("COUNTING_MODE") or (ok("votingPeriod") and (ok("proposalThreshold") or ok("proposalCount"))):
        tl = addr_of(V("timelock"))
        vp = dec_uint(V("votingPeriod").value) if ok("votingPeriod") else None
        flavor = "openzeppelin_governor" if ok("COUNTING_MODE") else ("governor_bravo" if ok("quorumVotes") else "governor")
        details.update(flavor=flavor, timelock=tl, voting_period_blocks_or_seconds=vp,
                       token=addr_of(V("token")))
        return node(addr, "governor", f"governor ({flavor})", details, ev=evr + [V("votingPeriod").ev if V("votingPeriod") else None])
    if ok("getPayloadsCount") and ok("guardian"):
        details.update(flavor="aave_v3_payloads_controller", guardian=addr_of(V("guardian")),
                       payloads_count=dec_uint(V("getPayloadsCount").value))
        return node(addr, "governor", "governor (Aave v3 PayloadsController; payloads queued by governance votes)",
                    details, ev=evr + [V("getPayloadsCount").ev])
    if ok("voteTime") and (ok("supportRequiredPct") or ok("token")):
        details.update(flavor="aragon_voting", token=addr_of(V("token")), vote_time=dec_uint(V("voteTime").value))
        return node(addr, "governor", "governor (Aragon Voting)", details, ev=evr + [V("voteTime").ev])
    if ok("hat"):
        details.update(flavor="ds_chief", hat=addr_of(V("hat")))
        return node(addr, "governor", "governor (DSChief, MKR-weighted approval voting)", details, ev=evr + [V("hat").ev])
    # ---- Aragon kernel ----
    acl = addr_of(V("acl"))
    if nz(acl):
        q3 = Q(env, ev)
        q3.call(acl, SEL["getPermissionManager"] + pad_addr(addr) + APP_MANAGER_ROLE[2:], ("pm", addr))
        r3 = q3.run()
        pm = addr_of(r3[("pm", addr)])
        details.update(flavor="aragon_kernel", acl=acl, app_manager_role_manager=pm)
        return node(addr, "aragon_kernel", "Aragon Kernel (upgrades gated by ACL APP_MANAGER_ROLE)", details,
                    [{"label": "ACL.getPermissionManager(kernel, APP_MANAGER_ROLE)", "address": pm}] if nz(pm) else [],
                    ev=evr + [r3[("pm", addr)].ev])
    if ok("kernel") and ok("appId") and nz(addr_of(V("kernel"))):
        details.update(flavor="aragon_app", kernel=addr_of(V("kernel")), app_id=V("appId").value)
        return node(addr, "dao_other", "DAO-controlled Aragon app (permissions via ACL; not followed)", details,
                    ev=evr + [V("kernel").ev, V("appId").ev])
    # ---- AccessControl contract (e.g. Arbitrum UpgradeExecutor) ----
    has_ac = (SEL["hasRole"] in union) or (r.get(("hasRole0", addr)) is not None and r[("hasRole0", addr)].ok
                                           and dec_bool(r[("hasRole0", addr)].value) is not None)
    if has_ac:
        holders = ac_holders(env, ev, addr, union, errs,
                             only=["DEFAULT_ADMIN_ROLE", "ADMIN_ROLE", "EXECUTOR_ROLE", "PROPOSER_ROLE",
                                   "UPGRADER_ROLE", "OPERATOR_ROLE", "MANAGER_ROLE"])
        details["role_holders"] = holders["by_role"]
        details["role_holders_status"] = holders["status"]
        nxt = []
        for role in ("EXECUTOR_ROLE", "DEFAULT_ADMIN_ROLE", "ADMIN_ROLE", "PROPOSER_ROLE", "UPGRADER_ROLE"):
            for h in holders["by_role"].get(role, []):
                if h["address"] != addr:
                    nxt.append({"label": role, "address": h["address"]})
        if owner is not None:
            nxt.append({"label": "owner", "address": owner})
        details["flavor"] = "access_control"
        details["probe_errors"] = errs
        return node(addr, "access_control_contract", "AccessControl contract", details, nxt[:8],
                    ev=evr + holders["ev"])
    # ---- ownable chain ----
    if owner is not None:
        flavor = "proxy_admin" if SEL["getProxyAdmin"] in union else "ownable"
        details["flavor"] = flavor
        nxt = [{"label": "owner", "address": owner}]
        return node(addr, "ownable_contract", f"{flavor} contract", details, nxt, ev=evr + [V("owner").ev])
    if nz(adm_c):
        details["flavor"] = "admin_getter"
        return node(addr, "ownable_contract", "contract with admin()", details, [{"label": "admin", "address": adm_c}],
                    ev=evr + [V("admin").ev])
    return node(addr, "other_contract", "other contract (no recognised control interface)", details, ev=evr)


def fmt_delay(s):
    if s is None:
        return "?"
    if s % 86400 == 0 and s:
        return f"{s // 86400}d"
    if s % 3600 == 0 and s:
        return f"{s // 3600}h"
    return f"{s}s"


# =====================================================================================================================
# AccessControl holders (enumerable -> direct; else RoleGranted logs from Etherscan + hasRole verification)
# =====================================================================================================================
def ac_holders(env, ev, addr, union, errs, only=None, max_pairs=60, candidates=None):
    """Returns {by_role: {ROLE_NAME|hash: [{address, via, ev}]}, status, ev:[...]}"""
    out = {"by_role": {}, "status": None, "ev": []}
    names = [n for n in ["DEFAULT_ADMIN_ROLE"] + ROLE_NAMES if only is None or n in only]
    role_hash = {n: ("0x" + EMPTY32 if n == "DEFAULT_ADMIN_ROLE" else "0x" + k(n).hex()) for n in names}
    if SEL["getRoleMember"] in union and SEL["getRoleMemberCount"] in union:
        q = Q(env, ev)
        for n, h in role_hash.items():
            q.call(addr, SEL["getRoleMemberCount"] + h[2:], ("rmc", n))
        r = q.run()
        errs += errors_of(r)
        q2 = Q(env, ev)
        counts = {}
        for n in names:
            res = r.get(("rmc", n))
            c = dec_uint(res.value) if res is not None and res.ok else None
            if c:
                counts[n] = c
                for i in range(min(c, 10)):
                    q2.call(addr, SEL["getRoleMember"] + role_hash[n][2:] + i.to_bytes(32, "big").hex(), ("rm", n, i))
        r2 = q2.run()
        errs += errors_of(r2)
        for n, c in counts.items():
            for i in range(min(c, 10)):
                res = r2.get(("rm", n, i))
                a = addr_of(res)
                if nz(a):
                    out["by_role"].setdefault(n, []).append({"address": a, "via": "getRoleMember", "ev": res.ev})
            if c > 10:
                out["by_role"].setdefault(n, []).append({"address": None, "via": f"truncated: {c} members", "ev": None})
        out["status"] = "enumerated"
        return out
    if SEL["hasRole"] not in union and not any(True for _ in []):
        # still try logs only when the contract is AccessControl-like; TimelockController always is
        pass
    res = None
    if env.etherscan_ok:
        res = etherscan_get(ev, CHAINS[env.chain]["id"],
                            dict(module="logs", action="getLogs", fromBlock=0, toBlock=env.block_int, address=addr,
                                 topic0=ROLE_GRANTED, page=1, offset=1000))
        out["ev"].append(res.ev)
    if res is None or not res.ok:
        out["status"] = "logs_unavailable: " + ("no free Etherscan access on this chain" if res is None else str(res.msg))
        if res is not None:
            errs.append({"key": "etherscan_RoleGranted_logs", "msg": res.msg, "ev": res.ev})
            if "free" in str(res.msg).lower() or "not supported" in str(res.msg).lower():
                env.etherscan_ok = False
        cands = [c for c in dict.fromkeys(candidates or []) if nz(c)][:8]
        if cands and SEL["hasRole"] in union:
            cnames = [n for n in ("DEFAULT_ADMIN_ROLE", "ADMIN_ROLE", "MINTER_ROLE", "PAUSER_ROLE", "UPGRADER_ROLE",
                                  "BURNER_ROLE", "BLACKLISTER_ROLE", "OPERATOR_ROLE") if only is None or n in only]
            q = Q(env, ev)
            for n in cnames:
                for cnd in cands:
                    q.call(addr, SEL["hasRole"] + role_hash[n][2:] + pad_addr(cnd), ("hrc", n, cnd))
            rc = q.run()
            errs.extend(errors_of(rc))
            for n in cnames:
                for cnd in cands:
                    x = rc.get(("hrc", n, cnd))
                    if x is not None and x.ok and dec_bool(x.value):
                        out["by_role"].setdefault(n, []).append(
                            {"address": cnd, "via": "hasRole() on discovered candidate (no log access; INCOMPLETE)",
                             "ev": x.ev})
            out["status"] += "; candidate-only hasRole check (holders may be missing)"
        return out
    logs = res.value.get("result") or []
    if not isinstance(logs, list):
        logs = []
    pairs = []
    for lg in logs:
        t = lg.get("topics", [])
        if len(t) >= 3:
            role = t[1].lower()
            acct = "0x" + t[2][-40:].lower()
            nm = ROLE_HASH.get(role, role)
            if (nm, acct) not in [(p[0], p[1]) for p in pairs]:
                pairs.append((nm, acct, role))
    if only is not None:
        pairs = [p for p in pairs if p[0] in only or p[0] not in ROLE_HASH.values() and False]
    pairs = pairs[:max_pairs]
    q = Q(env, ev)
    for nm, acct, role in pairs:
        q.call(addr, SEL["hasRole"] + role[2:] + pad_addr(acct), ("hr", nm, acct))
    r = q.run() if pairs else {}
    errs += errors_of(r)
    for nm, acct, role in pairs:
        res2 = r.get(("hr", nm, acct))
        if res2 is not None and res2.ok and dec_bool(res2.value):
            out["by_role"].setdefault(nm, []).append({"address": acct, "via": "RoleGranted log + hasRole()", "ev": res2.ev})
    out["status"] = "logs" + (" (truncated at 1000 logs)" if len(logs) >= 1000 else "")
    return out


# =====================================================================================================================
# Token probe
# =====================================================================================================================
def fmt_node(nd, depth, via, parent):
    return {"address": nd["address"], "kind": nd["kind"], "summary": nd["summary"], "depth": depth, "via": via,
            "parent": parent, "ev": nd["ev"][:6], "details": brief_details(nd)}


def brief_details(nd):
    d = nd["details"]
    keep = {}
    for key in ("threshold", "owner_count", "owners", "version", "delay_seconds", "flavor", "delegate", "l1_address",
               "nonce", "proxy", "code_size", "timelock", "token", "hat", "acl", "app_manager_role_manager",
               "role_holders_status", "l1_alias_check"):
        if key in d:
            keep[key] = d[key]
    if "role_holders" in d:
        keep["role_holders"] = {rn: [h["address"] for h in hs] for rn, hs in d["role_holders"].items()}
    if "l1_node" in d:
        keep["l1_node"] = {"kind": d["l1_node"]["kind"], "summary": d["l1_node"]["summary"],
                           "details": brief_details(d["l1_node"])}
    return keep


def expand(env, roots):
    """roots: list of (address, via). Returns (chain_nodes, paths)."""
    chain_nodes, paths, seen = [], [], {}

    def rec(addr, via_label, path_nodes, depth, labels, parent):
        nd = classify_addr(env, addr)
        if addr not in seen or depth < seen[addr]["depth"]:
            fn = fmt_node(nd, depth, via_label, parent)
            if addr in seen:
                chain_nodes[chain_nodes.index(seen[addr])] = fn
            else:
                chain_nodes.append(fn)
            seen[addr] = fn
        here = path_nodes + [nd]
        lab = labels + [via_label]
        nxt = [n for n in nd["next"] if n["address"] not in [p["address"] for p in here]]
        if depth >= MAX_DEPTH and nxt:
            paths.append(path_entry(here, lab, truncated=True))
            return
        if not nxt:
            paths.append(path_entry(here, lab, truncated=False))
            return
        for n in nxt[:8]:
            if len(paths) > 60:
                break
            rec(n["address"], n["label"], here, depth + 1, lab, addr)

    for a, via in roots:
        rec(a, via, [], 1, [], None)
    return chain_nodes, paths


def path_entry(nodes, labels, truncated):
    delay = sum(n["details"].get("delay_seconds") or 0 for n in nodes if n["kind"] == "timelock")
    import re as _re
    m_ = _re.search(r"access_manager\(.*delay=(\d+)s", labels[0])
    am_delay = int(m_.group(1)) if m_ else 0
    delay += am_delay
    last = nodes[-1]
    tk = last["kind"]
    if truncated:
        tk = f"truncated_depth>{MAX_DEPTH}:{tk}"
    return {"via": labels[0], "addresses": [n["address"] for n in nodes], "kinds": [n["kind"] for n in nodes],
            "labels": labels, "verified": "[verified" in labels[0], "access_manager_delay_seconds": am_delay, "assumed_or_unverified": ("[assumed" in labels[0] or "[unverified" in labels[0]),
            "terminal_kind": tk, "terminal_summary": last["summary"],
            "terminal_address": last["address"], "timelock_delay_seconds": delay or None, "truncated": truncated}


def probe_token(env, token):
    t0 = time.time()
    token = token.lower()
    if env.rolling:
        env._tl.blocks = set()
        env.repin_thread()
    ev = Ev(f"evidence/{token}.jsonl", env.out_root)
    errs = []
    row = {"chain": env.chain, "token": token, "block": env.block_int, "probed_at": now_iso(), "status": "ok"}
    try:
        _probe_token(env, token, ev, row, errs)
    except Exception as e:  # noqa: BLE001
        row["status"] = "error"
        row["error"] = repr(e)[:300]
        if os.environ.get("CTL_DEBUG"):
            raise
    finally:
        ev.flush()
    for a_, n_ in (row.get("controllers") or {}).items():
        if n_.get("kind") == "unclassified":
            errs.append({"key": f"controller {a_}", "msg": "controller could not be classified: " + n_.get("summary", ""), "ev": None})
    row["probe_errors"] = errs
    row["completeness"] = "complete" if not errs else "partial"
    if env.rolling and len(getattr(env._tl, "blocks", ())) > 1:
        row["blocks_used"] = sorted(env._tl.blocks)  # the probe was re-pinned because the node pruned the first block
    row["evidence"] = [f"evidence/{token}.jsonl"] + sorted(
        {e.split("#")[0] for n in row.get("controllers", {}).values() for e in n.get("ev", []) if "#" in e})
    row["elapsed_s"] = round(time.time() - t0, 2)
    return row


def _probe_token(env, token, ev, row, errs):
    q = Q(env, ev)
    q.code(token)
    for s in SLOTS:
        q.slot(token, s, ("slot", token, s))
    r1_views = ["symbol", "decimals", "totalSupply", "implementation", "proxyType", "kernel", "facetAddresses", "target",
                "owner", "getOwner", "_owner", "admin", "governance", "governor", "gov", "authority", "pendingOwner",
                "paused", "getImplementation", "getAdmin", "proxyOwner"]
    for n in r1_views:
        q.view(token, n)
    r = q.run()
    errs.extend(errors_of(r))
    code_r = r[("code", token)]
    if not code_r.ok:
        row["status"] = "unprobeable"
        row["reason"] = f"eth_getCode failed: {code_r.msg}"
        return
    code = code_r.value or "0x"
    if code == "0x":
        row["status"] = "not_a_contract"
        row["reason"] = "no code at address at pinned block"
        return
    if code == "0xef":  # 1-byte code: Base B20 precompile-backed token (or something else answering ERC-20 calls)
        _probe_b20(env, token, ev, row, errs, r, code_r)
        return
    V = lambda n: r.get(("v", token, n))  # noqa: E731
    scan = scan_code(code)
    if has_legacy_forward(scan["selectors"]):
        q = Q(env, ev)
        q.view(token, "deprecated")
        q.view(token, "upgradedAddress")
        legacy = q.run()
        errs.extend(errors_of(legacy))
        dep = legacy[("v", token, "deprecated")]
        target = legacy[("v", token, "upgradedAddress")]
        if dep.ok and dec_bool(dep.value) is not None and addr_of(target) is not None:
            row["upgrade_path_type"] = "legacy_deprecate_forward"
            row["legacy_forward"] = {"deprecated": dec_bool(dep.value), "upgraded_address": addr_of(target),
                                     "ev": [dep.ev, target.ev]}
    from eth_utils import keccak
    row["code"] = {"size": scan["size"], "keccak": "0x" + keccak(bytes.fromhex(code[2:])).hex(),
                   "has_delegatecall": scan["delegatecall"], "has_selfdestruct": scan["selfdestruct"],
                   "ev": code_r.ev}
    if V("symbol") is not None and V("symbol").ok:
        row["symbol"] = dec_str(V("symbol").value)
    if V("decimals") is not None and V("decimals").ok:
        row["decimals"] = dec_uint(V("decimals").value)
    px = detect_proxy_basic(token, code, scan, r)
    # ---- custom DELEGATECALL proxy: ask a trace-capable node where `decimals()` goes -------------------------------
    trace_note = None
    if px["proxy_type"] is None and scan["delegatecall"]:
        tr = env.client.trace_call(ev, token, TRACE_PROBE, env.block_hex)
        if tr.ok:
            tgts = delegate_targets(tr.value, token)
            if tgts:
                s0 = slot_addr(r.get(("slot", token, "slot0")))
                px.update(proxy_type="slot0_delegate_proxy" if s0 == tgts[0] else "custom_delegatecall",
                          implementation=tgts[0], confidence="observed_delegatecall_trace",
                          via="debug_traceCall(decimals()) DELEGATECALL target", ev=[tr.ev])
                trace_note = f"delegatecall target(s) in trace: {tgts[:3]}"
            else:
                trace_note = "trace of decimals(): no DELEGATECALL from token (DELEGATECALL opcode present but not on this path)"
        else:
            trace_note = f"trace unavailable ({tr.msg}); bytecode contains DELEGATECALL, proxy status undetermined"
            errs.append({"key": "debug_traceCall", "msg": tr.msg, "ev": tr.ev, "severity": "info"})
    row["delegatecall_note"] = trace_note
    # ---- diamond: facet list is the implementation set --------------------------------------------------------------
    impls_chain, union, impl_code_refs, impl_is_proxy = [], set(scan["selectors"]), [], False
    impl = px["implementation"]
    beacon = px["beacon"]
    if beacon:
        q = Q(env, ev)
        q.view(beacon, "implementation")
        q.view(beacon, "owner")
        q.code(beacon)
        rb = q.run()
        errs.extend(errors_of(rb))
        impl = addr_of(rb[("v", beacon, "implementation")])
        px["beacon_owner"] = addr_of(rb[("v", beacon, "owner")])
        px["ev"] += [rb[("v", beacon, "implementation")].ev, rb[("v", beacon, "owner")].ev]
        px["implementation"] = impl
        row["beacon"] = beacon
    cur = impl
    layers = 0
    while nz(cur) and layers < 3:
        impls_chain.append(cur)
        q = Q(env, ev)
        q.code(cur)
        for s in ("eip1967_impl", "eip1967_beacon", "zos_impl", "eip1822_proxiable"):
            q.slot(cur, s, ("slot", cur, s))
        q.view(cur, "implementation")
        rr = q.run()
        errs.extend(errors_of(rr))
        ic = rr[("code", cur)]
        if ic.ok and ic.value not in (None, "0x"):
            sc = scan_code(ic.value)
            union |= sc["selectors"]
            impl_code_refs.append(ic.ev)
            nxt = (slot_addr(rr[("slot", cur, "eip1967_impl")]) or slot_addr(rr[("slot", cur, "zos_impl")]) or
                   slot_addr(rr[("slot", cur, "eip1822_proxiable")]))
            if nxt and nxt != cur and px["proxy_type"] != "eip1822_uups":
                impl_is_proxy = True
                cur = nxt
                layers += 1
                continue
        break
    facets = px.get("facets")
    if px["proxy_type"]:
        row["is_proxy"] = True
    elif not scan["delegatecall"] or (trace_note and "no DELEGATECALL" in trace_note):
        row["is_proxy"] = False
    else:
        row["is_proxy"] = None
    row["proxy_type"] = px["proxy_type"] if row["is_proxy"] is not None else "unknown_delegatecall_present"
    row["proxy_confidence"] = px["confidence"]
    row["proxy_detected_via"] = px["via"]
    row["implementation"] = impls_chain[-1] if impls_chain else None
    row["implementation_chain"] = impls_chain
    row["impl_is_proxy"] = impl_is_proxy
    if facets:
        row["facets"] = facets
    row["selector_count"] = len(union)
    # ---- capabilities (static selector hints) ----------------------------------------------------------------------
    caps = capabilities(union)
    row["capability_hints"] = {c: sorted(v) for c, v in caps.items()}
    unresolved_proxy = row["is_proxy"] is None
    # ---- role getters, gated on selectors (or all when the code can't be fully seen) -------------------------------
    q = Q(env, ev)
    names = [n for n in GETTER_BUCKET if VIEWS[n] in union or unresolved_proxy]
    for n in names:
        q.view(token, n)
    r3 = q.run() if q.items else {}
    errs.extend(errors_of(r3))
    # ---- AccessControl / wards --------------------------------------------------------------------------------------
    ac = {"by_role": {}, "status": "n/a", "ev": []}
    if SEL["hasRole"] in union:
        cand = [addr_of(r.get(("v", token, n))) for n in OWNER_GETTERS if r.get(("v", token, n)) is not None]
        cand += [addr_of(r3.get(("v", token, n))) for n in names if r3.get(("v", token, n)) is not None]
        cand += [px["admin_slot"], px.get("beacon_owner")]
        ac = ac_holders(env, ev, token, union, errs, candidates=[x for x in cand if x])
    wards = {"holders": [], "status": "n/a"}
    if SEL["wards"] in union:
        wards = wards_holders(env, ev, token, errs)
    # ---- build roots -----------------------------------------------------------------------------------------------
    owner_roots, owner_seen = [], set()
    for n in OWNER_GETTERS:
        if n == "admin" and px["proxy_type"] in ("eip1967", "zos_legacy"):
            continue  # admin() on a transparent proxy is reached through the admin slot, not delegated
        a = addr_of(V(n)) if V(n) is not None else None
        if a is not None and (a, n) not in owner_seen and (n != "authority" or nz(a)):
            owner_seen.add((a, n))
            owner_roots.append((a, f"{n}()"))
    for h in wards["holders"]:
        owner_roots.append((h["address"], "wards[x]==1"))
    for h in ac["by_role"].get("DEFAULT_ADMIN_ROLE", []):
        if h["address"]:
            owner_roots.append((h["address"], "DEFAULT_ADMIN_ROLE"))
    # de-dup keeping the first via
    seen_a, owner_roots2 = set(), []
    for a, via in owner_roots:
        if (a, via) not in seen_a:
            seen_a.add((a, via))
            owner_roots2.append((a, via))
    owner_roots = owner_roots2
    # upgrade roots
    up_roots, basis = [], None
    pt = px["proxy_type"]
    if row.get("upgrade_path_type") == "legacy_deprecate_forward":
        up_roots = [(a, v + " [legacy deprecate-forward]") for a, v in owner_roots if v in ("owner()", "getOwner()")]
        basis = "legacy deprecate-forward (complete forwarding interface fingerprint); owner() path"
    elif pt in ("eip1967", "zos_legacy"):
        if nz(px["admin_slot"]):
            up_roots.append((px["admin_slot"], px["admin_slot_name"] + "_slot"))
            basis = "admin slot"
        elif any(s in union for s in UPGRADE_SELS):
            basis = "uups (admin slot empty; implementation has upgradeTo*; _authorizeUpgrade is custom code: candidates (owner getters, wards, AccessControl holders) are tested by eth_call simulation of upgradeTo from each)"
        else:
            basis = "admin slot empty and implementation has no upgradeTo* selector"
        if not nz(px["admin_slot"]) and any(s in union for s in UPGRADE_SELS):
            up_roots += uups_candidates(owner_roots, ac)
    elif pt == "eip1822_uups":
        basis = "uups (eip1822); authority = _authorizeUpgrade/updateCodeAddress, candidates verified by eth_call simulation"
        up_roots += uups_candidates(owner_roots, ac)
    elif pt == "eip1967_beacon":
        bo = px.get("beacon_owner")
        basis = "beacon owner (beacon.owner())"
        if bo is not None:
            up_roots.append((bo, "beacon.owner()"))
    elif pt == "aragon_app_proxy":
        up_roots.append((px["kernel"], "kernel()"))
        basis = "Aragon kernel ACL"
    elif pt == "eip2535_diamond":
        up_roots += [(a, v + " [diamond owner]") for a, v in owner_roots if v.startswith("owner")]
        basis = "diamond owner (IERC173 owner())"
    elif pt in ("eip1167", "eip1167_0age", "eip7511_push0", "eip1167_vanity"):
        basis = "minimal proxy clone: implementation address is hard-coded in bytecode"
    elif pt:
        po = addr_of(V("proxyOwner")) if V("proxyOwner") is not None else None
        if po is not None:
            up_roots.append((po, "proxyOwner()"))
            basis = f"{pt}: proxyOwner() (Matic/Polygon UChildERC20Proxy-style onlyProxyOwner upgrades)"
        else:
            up_roots += [(a, v + " [assumed]") for a, v in owner_roots]
            basis = f"{pt}: authority not standard; owner/admin getters assumed"
    row["upgrade_authority_basis"] = basis
    row["upgrade_simulation"] = None
    if row.get("upgrade_path_type") == "legacy_deprecate_forward" and up_roots:
        up_roots, row["upgrade_simulation"] = simulate_upgrades(env, ev, token, token, up_roots, errs, kind="deprecate")
    if pt == "implementation_getter" and up_roots and row["implementation"] and not any(v.startswith("proxyOwner") for _, v in up_roots):
        up_roots, row["upgrade_simulation"] = simulate_upgrades(env, ev, token, row["implementation"], up_roots, errs, kind="cdelegator")
        if row["upgrade_simulation"]["verified_any"]:
            basis += "; authority VERIFIED by eth_call simulation of _setImplementation(currentImplementation,false,'') (Compound CErc20Delegator)"
        row["upgrade_authority_basis"] = basis
    if pt == "synthetix_target_proxy" and up_roots and row["implementation"]:
        up_roots, row["upgrade_simulation"] = simulate_upgrades(env, ev, token, row["implementation"], up_roots, errs, kind="setTarget")
        if row["upgrade_simulation"]["verified_any"]:
            basis += "; authority VERIFIED by eth_call simulation of setTarget(currentTarget)"
        row["upgrade_authority_basis"] = basis
    if pt in ("eip1967", "zos_legacy", "eip1822_uups") and up_roots and row["implementation"]:
        orig_roots = list(up_roots)
        up_roots, row["upgrade_simulation"] = simulate_upgrades(env, ev, token, row["implementation"], orig_roots, errs)
        if not row["upgrade_simulation"]["verified_any"] and not nz(px["admin_slot"]):
            extra, note = last_upgrade_callers(env, ev, token, errs)
            row["upgrade_history_probe"] = {"note": note, "callers": [a for a, _ in extra]}
            for role in row["upgrade_simulation"].get("needed_roles", [])[:2]:
                nh, n2 = needed_role_holders(env, ev, token, role, errs)
                row["upgrade_history_probe"].setdefault("needed_role_holders", {})[role] = {"note": n2, "holders": [a for a, _ in nh]}
                extra += nh
            have = {a for a, _ in orig_roots}
            extra = [(a, v) for a, v in extra if a not in have and nz(a)]
            if extra:
                up_roots, row["upgrade_simulation"] = simulate_upgrades(env, ev, token, row["implementation"], orig_roots + extra, errs)
            if not row["upgrade_simulation"]["verified_any"]:
                am, am_meta = access_manager_upgraders(env, ev, token, errs)
                if am_meta:
                    row["upgrade_access_manager"] = am_meta
                if am:
                    up_roots = [x for x in up_roots if x[0] not in {a for a, _ in am}] + am
                    row["upgrade_simulation"]["verified_via_access_manager"] = True
                    basis += "; upgrade gated by an OZ AccessManager: members verified with canCall()"
            if not row["upgrade_simulation"]["verified_any"] and not row["upgrade_simulation"].get("verified_via_access_manager"):
                for role in row["upgrade_simulation"].get("needed_roles", [])[:1]:
                    ra, n3 = role_admin_upgraders(env, ev, token, role, errs)
                    row["upgrade_history_probe"]["role_admin_selfgrant"] = {"role": role, "note": n3, "addresses": [a for a, _ in ra]}
                    if ra:
                        up_roots = [x for x in up_roots if x[0] not in {a for a, _ in ra}] + ra
                        row["upgrade_simulation"]["verified_via_role_admin"] = True
                        basis += "; upgrade role has no direct simulated holder, role-admin holders can self-grant (simulated)"
        if row["upgrade_simulation"]["verified_any"]:
            basis += "; authority VERIFIED by eth_call simulation"
        row["upgrade_authority_basis"] = basis
    # ---- expansion -------------------------------------------------------------------------------------------------
    up_chain, up_paths = expand(env, up_roots) if up_roots else ([], [])
    ow_chain, ow_paths = expand(env, owner_roots) if owner_roots else ([], [])
    row["upgrade_admin_chain"] = up_chain
    row["upgrade_paths"] = up_paths
    row["owner_chain"] = ow_chain
    row["owner_paths"] = ow_paths
    # ---- roles -------------------------------------------------------------------------------------------------------
    roles = {}
    role_roots = []
    for n in names:
        res = r3.get(("v", token, n))
        a = addr_of(res)
        if a is not None:
            roles.setdefault(GETTER_BUCKET[n], []).append({"address": a, "via": f"{n}()", "ev": res.ev})
    for rn, hs in ac["by_role"].items():
        b = ROLE_BUCKET.get(rn, "other_role:" + rn)
        for h in hs:
            if h["address"]:
                roles.setdefault(b, []).append({"address": h["address"], "via": f"AccessControl {rn} ({h['via']})",
                                                "ev": h["ev"]})
    # each role holder is classified and followed (cached)
    for b, hs in roles.items():
        for h in hs:
            ch, ps = expand(env, [(h["address"], h["via"])])
            h["kind"] = ch[0]["kind"] if ch else None
            h["summary"] = ch[0]["summary"] if ch else None
            h["terminal_kinds"] = sorted({p["terminal_kind"] for p in ps})
            h["terminals"] = [{"address": p["terminal_address"], "kind": p["terminal_kind"],
                               "summary": p["terminal_summary"], "timelock_delay_seconds": p["timelock_delay_seconds"]}
                              for p in ps][:6]
    row["roles"] = roles
    row["access_control"] = {"status": ac["status"], "holders": {rn: [h["address"] for h in hs]
                                                                  for rn, hs in ac["by_role"].items()}}
    row["wards"] = {"status": wards["status"], "holders": [h["address"] for h in wards["holders"]]}
    paused = r.get(("v", token, "paused"))
    row["paused"] = dec_bool(paused.value) if paused is not None and paused.ok else None
    pend = r.get(("v", token, "pendingOwner"))
    pa = addr_of(pend) if pend is not None else None
    row["pending_owner"] = pa if nz(pa) else None
    # ---- controllers lookup table (shared nodes, full details) -----------------------------------------------------
    allc = {}
    for nd in up_chain + ow_chain:
        allc[nd["address"]] = env.ctrl[nd["address"]]
    for hs in roles.values():
        for h in hs:
            for p in expand(env, [(h["address"], "")])[0]:
                allc[p["address"]] = env.ctrl[p["address"]]
    row["controllers"] = {a: {"kind": n["kind"], "summary": n["summary"], "details": brief_details(n), "next": n["next"],
                              "ev": n["ev"]} for a, n in allc.items()}
    # ---- verdict ---------------------------------------------------------------------------------------------------
    row["upgradeable"], row["upgradeable_reason"] = upgradeability(row, px, union)
    row["upgrade_authority_firm"] = bool(any(not p.get("assumed_or_unverified") for p in up_paths)) if up_paths else None
    row["upgrade_terminal_kinds"] = sorted({p["terminal_kind"] for p in up_paths})
    row["owner_terminal_kinds"] = sorted({p["terminal_kind"] for p in ow_paths})
    priv = sorted(c for c in caps if c in PRIV_CAPS)
    row["privileged_capabilities"] = priv
    row["control_summary"] = control_summary(row, priv)


def uups_candidates(owner_roots, ac):
    """Candidate UUPS authorities: owner getters, wards, AccessControl holders of any role (UPGRADER first)."""
    out, seen = [], set()

    def add(a, via):
        if a and (a, via) not in seen and len({x for x, _ in out}) < 10:
            seen.add((a, via))
            out.append((a, via))
    for role in ("UPGRADER_ROLE", "UPGRADE_ROLE"):
        for h in ac["by_role"].get(role, []):
            add(h["address"], role)
    for a, v in owner_roots:
        add(a, v)
    for role, hs in ac["by_role"].items():
        for h in hs:
            add(h["address"], role)
    return out


UPGRADED_TOPIC = "0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b"


def _walk_calls(node, token, out):
    if not isinstance(node, dict):
        return
    if (node.get("to") or "").lower() == token and (node.get("input") or "0x")[:10] in (UPGRADE_TO_HEX, UPGRADE_TO_AND_CALL_HEX):
        out.append((node.get("from") or "").lower())
    for ch in node.get("calls") or []:
        _walk_calls(ch, token, out)


def needed_role_holders(env, ev, token, role, errs):
    """The failed upgrade simulation named the role the proxy's _authorizeUpgrade requires ("AccessControl: account X is missing role 0x..").
    Find that role's current holders: RoleGranted logs filtered by the role id (Etherscan), each verified with hasRole()."""
    if not env.etherscan_ok:
        return [], "etherscan logs unavailable on this chain"
    cid = CHAINS[env.chain]["id"]
    res = etherscan_get(ev, cid, dict(module="logs", action="getLogs", fromBlock=0, toBlock=env.block_int, address=token,
                                      topic0=ROLE_GRANTED, topic1=role, topic0_1_opr="and", page=1, offset=1000))
    if not res.ok:
        errs.append({"key": "etherscan_needed_role_logs", "msg": res.msg, "ev": res.ev})
        return [], f"role logs unavailable: {res.msg}"
    logs = res.value.get("result") or []
    cands = []
    if isinstance(logs, list):
        for lg in logs:
            tp = lg.get("topics", [])
            if len(tp) >= 3:
                a = "0x" + tp[2][-40:].lower()
                if a not in cands:
                    cands.append(a)
    q = Q(env, ev)
    for a in cands:
        q.call(token, SEL["hasRole"] + role[2:] + pad_addr(a), ("hr", a))
    rr = q.run() if cands else {}
    errs.extend(errors_of(rr))
    out = [(a, f"needed_role({role[:10]})") for a in cands if rr[("hr", a)].ok and dec_uint(rr[("hr", a)].value) == 1]
    return out, f"{len(cands)} RoleGranted for {role[:10]}, {len(out)} still hold it"


def role_admin_upgraders(env, ev, token, role, errs):
    """If nobody (or nobody useful) holds the role that gates upgrades, the holders of that role's admin role can still grant it to themselves
    and upgrade (two transactions). Each holder of getRoleAdmin(role) is tested by read-only eth_call simulation of grantRole(role, holder)
    from itself, with a stranger baseline that must revert."""
    if not env.etherscan_ok:
        return [], "etherscan logs unavailable on this chain"
    q = Q(env, ev)
    q.call(token, sel("getRoleAdmin(bytes32)") + role[2:], "ra")
    rr = q.run()
    errs.extend(errors_of(rr))
    if not rr["ra"].ok or not rr["ra"].value or len(rr["ra"].value) < 66:
        return [], "getRoleAdmin unavailable"
    admin_role = "0x" + rr["ra"].value[2:66]
    holders, note = needed_role_holders(env, ev, token, admin_role, errs)
    if not holders:
        return [], f"role admin {admin_role[:10]}: {note}"
    gdata = lambda who: sel("grantRole(bytes32,address)") + role[2:] + pad_addr(who)  # noqa: E731
    q = Q(env, ev)
    for who in [BASELINE_CALLER] + [a for a, _ in holders]:
        q._add(("gr", who), "eth_call", [{"from": who, "to": token, "data": gdata(who)}, env.block_hex])
    r = q.run()
    errs.extend(errors_of(r))
    if r[("gr", BASELINE_CALLER)].ok:
        return [], "grantRole baseline did not revert: inconclusive"
    out = [(a, f"role_admin({admin_role[:10]}) of upgrade role {role[:10]} [verified: grantRole(upgrade role, self) simulation succeeds; upgrade needs 2 txs]")
           for a, _ in holders if r[("gr", a)].ok]
    return out, f"admin role {admin_role[:10]}: {note}; {len(out)} can self-grant"


def solmate_style_authority(env, ev, token, auth, fsel, errs):
    """authority() is a contract with canCall(address,address,bytes4) but not an OZ AccessManager (Solmate RolesAuthority, Spiko
    PermissionManager, ...). Holders are not enumerable through a standard getter: take every address that appears in the authority's own event logs
    (indexed topics and address-shaped data words) as a candidate and keep those for which canCall(candidate, token, upgradeToAndCall) is true."""
    meta = {"manager": auth, "kind": "canCall_authority(bool)", "members": []}
    if not env.etherscan_ok:
        meta["note"] = "etherscan logs unavailable on this chain"
        return [], meta
    cid = CHAINS[env.chain]["id"]
    res = etherscan_get(ev, cid, dict(module="logs", action="getLogs", fromBlock=0, toBlock=env.block_int, address=auth, page=1, offset=1000))
    if not res.ok:
        errs.append({"key": "etherscan_authority_logs", "msg": res.msg, "ev": res.ev})
        meta["note"] = f"authority logs unavailable: {res.msg}"
        return [], meta
    cands = []
    for lg in (res.value.get("result") or []) if isinstance(res.value.get("result"), list) else []:
        words = [t for t in lg.get("topics", [])[1:]] + ["0x" + lg.get("data", "0x")[2:][i:i + 64] for i in range(0, len(lg.get("data", "0x")) - 2, 64)]
        for w in words:
            w = w[2:]
            if len(w) == 64 and w[:24] == "0" * 24 and int(w, 16) > 0xffff:
                a = "0x" + w[24:]
                if a not in cands and a != token:
                    cands.append(a)
    cands = cands[:60]
    q = Q(env, ev)
    for a in cands:
        q.call(auth, sel("canCall(address,address,bytes4)") + pad_addr(a) + pad_addr(token) + fsel[2:] + "0" * 56, ("cc", a))
    rr = q.run() if cands else {}
    errs.extend(errors_of(rr))
    out = []
    for a in cands:
        x = rr[("cc", a)]
        if x.ok and len(x.value) >= 66 and int(x.value[2:66], 16) == 1:
            meta["members"].append({"address": a, "immediate": True, "ev": x.ev})
            out.append((a, f"authority({auth[:10]}).canCall [verified: canCall(upgradeToAndCall) == true]"))
    meta["note"] = f"{len(cands)} addresses seen in the authority's logs, {len(out)} pass canCall(upgradeToAndCall)"
    return out, meta


def access_manager_upgraders(env, ev, token, errs):
    """OpenZeppelin v5 AccessManaged tokens: upgradeToAndCall is gated by authority().canCall(caller, token, selector) -> (immediate, delay).
    Read manager.getTargetFunctionRole(token, upgradeToAndCall), enumerate the role's members from the manager's RoleGranted logs and test each
    with canCall(): immediate = can upgrade now, delay > 0 = can schedule an upgrade that executes after `delay` seconds."""
    q = Q(env, ev)
    q.call(token, sel("authority()"), "auth")
    rr = q.run()
    mgr = addr_of(rr["auth"]) if rr["auth"].ok else None
    if not nz(mgr):
        return [], None
    fsel = sel("upgradeToAndCall(address,bytes)")
    q = Q(env, ev)
    q.call(mgr, sel("getTargetFunctionRole(address,bytes4)") + pad_addr(token) + fsel[2:] + "0" * 56, "role")
    rr = q.run()
    errs.extend(errors_of(rr))
    if not rr["role"].ok:
        return solmate_style_authority(env, ev, token, mgr, fsel, errs)
    role = dec_uint(rr["role"].value)
    meta = {"manager": mgr, "role_id": role, "function": "upgradeToAndCall(address,bytes)", "members": []}
    if role == 2 ** 64 - 1:
        meta["note"] = "PUBLIC_ROLE: anyone may call"
        return [], meta
    if not env.etherscan_ok:
        meta["note"] = "etherscan logs unavailable on this chain"
        return [], meta
    cid = CHAINS[env.chain]["id"]
    topic0 = "0x" + k("RoleGranted(uint64,address,uint32,uint48,bool)").hex()
    res = etherscan_get(ev, cid, dict(module="logs", action="getLogs", fromBlock=0, toBlock=env.block_int, address=mgr,
                                      topic0=topic0, topic1="0x" + role.to_bytes(32, "big").hex(), topic0_1_opr="and", page=1, offset=1000))
    if not res.ok:
        errs.append({"key": "etherscan_manager_logs", "msg": res.msg, "ev": res.ev})
        meta["note"] = f"manager role logs unavailable: {res.msg}"
        return [], meta
    cands = []
    for lg in (res.value.get("result") or []):
        tp = lg.get("topics", [])
        if len(tp) >= 3:
            a = "0x" + tp[2][-40:].lower()
            if a not in cands:
                cands.append(a)
    q = Q(env, ev)
    for a in cands:
        q.call(mgr, sel("canCall(address,address,bytes4)") + pad_addr(a) + pad_addr(token) + fsel[2:] + "0" * 56, ("cc", a))
    rr = q.run() if cands else {}
    errs.extend(errors_of(rr))
    out = []
    for a in cands:
        x = rr[("cc", a)]
        if not x.ok or len(x.value) < 130:
            continue
        immediate = int(x.value[2:66], 16) == 1
        delay = int(x.value[66:130], 16)
        meta["members"].append({"address": a, "immediate": immediate, "delay_seconds": delay, "ev": x.ev})
        if immediate or delay > 0:
            out.append((a, f"access_manager({mgr[:10]}) role {role} [verified: canCall(upgradeToAndCall) immediate={immediate} delay={delay}s]"))
    meta["note"] = f"{len(cands)} RoleGranted for role {role} on the manager, {len(out)} can upgrade (now or after delay)"
    return out, meta


def last_upgrade_callers(env, ev, token, errs):
    """Evidence-based extra UUPS candidates: the address that called upgradeTo*/upgradeToAndCall on the proxy in the transaction of the
    most recent `Upgraded` event (debug_traceTransaction call tree), plus that transaction's from/to. They are then tested by simulation
    like every other candidate (so a stale upgrader that lost the right does not count). Needs Etherscan logs (eth/arb/polygon)."""
    if not env.etherscan_ok:
        return [], "etherscan logs unavailable on this chain"
    cid = CHAINS[env.chain]["id"]
    res = etherscan_get(ev, cid, dict(module="logs", action="getLogs", fromBlock=0, toBlock=env.block_int, address=token,
                                      topic0=UPGRADED_TOPIC, page=1, offset=1000))
    if not res.ok:
        errs.append({"key": "etherscan_upgraded_logs", "msg": res.msg, "ev": res.ev})
        return [], f"upgraded logs unavailable: {res.msg}"
    logs = res.value.get("result") or []
    if not isinstance(logs, list) or not logs:
        return [], "no Upgraded event"
    lg = max(logs, key=lambda z: (int(z["blockNumber"], 16), int(z["logIndex"], 16) if z["logIndex"] not in ("0x", "") else 0))
    txh = lg["transactionHash"]
    out, note = [], f"last Upgraded at block {int(lg['blockNumber'], 16)} tx {txh}"
    tr = env.client.trace_tx(ev, txh)
    if tr.ok:
        callers = []
        _walk_calls(tr.value, token, callers)
        for a in callers:
            out.append((a, f"last_upgrade_caller(trace {txh[:10]})"))
    else:
        note += f"; trace of the upgrade tx unavailable ({(tr.msg or '')[:60]}; optional evidence, row not marked partial)"
    t = env.client.one(ev, "eth_getTransactionByHash", [txh], block=None)
    if t.ok and isinstance(t.value, dict):
        for k in ("from", "to"):
            if t.value.get(k):
                out.append((t.value[k].lower(), f"last_upgrade_tx_{k}({txh[:10]})"))
    return out, note


UPGRADE_TO = sel("upgradeTo(address)")
UPGRADE_TO_HEX = UPGRADE_TO if UPGRADE_TO.startswith("0x") else "0x" + UPGRADE_TO
UPGRADE_TO_AND_CALL_HEX = ("0x" + sel("upgradeToAndCall(address,bytes)")) if not sel("upgradeToAndCall(address,bytes)").startswith("0x") else sel("upgradeToAndCall(address,bytes)")
UPGRADE_TO_AND_CALL = sel("upgradeToAndCall(address,bytes)")
BASELINE_CALLER = "0x1000000000000000000000000000000000000001"


def simulate_upgrades(env, ev, token, impl, roots, errs, kind="uups", _alt=False):
    """Read-only `eth_call` simulation: from each candidate address call upgradeTo(currentImpl) /
    upgradeToAndCall(currentImpl,'') on the proxy. Success => that address passes the proxy's own upgrade authorisation.
    A call from an unrelated address (baseline) must revert, otherwise the test is inconclusive. Nothing is sent on chain.
    Returns (roots with verification labels, summary)."""
    cands = []
    for a, via in roots:
        if a not in cands and nz(a):
            cands.append(a)
    datas = {"upgradeTo": UPGRADE_TO + pad_addr(impl),
             "upgradeToAndCall": UPGRADE_TO_AND_CALL + pad_addr(impl) + (64).to_bytes(32, "big").hex() + "00" * 32}
    if kind == "setTarget":  # Synthetix-style Proxy: onlyOwner setTarget(address)
        datas = {"setTarget": sel("setTarget(address)") + pad_addr(impl)}
    if kind == "deprecate":
        datas = {"deprecate": sel("deprecate(address)") + pad_addr(impl)}
    if kind == "cdelegator":  # Compound v2 CErc20Delegator: onlyAdmin _setImplementation(address,bool,bytes)
        datas = {"_setImplementation": sel("_setImplementation(address,bool,bytes)") + pad_addr(impl) + "00" * 32
                 + (96).to_bytes(32, "big").hex() + "00" * 32}
    q = Q(env, ev)
    for frm in [BASELINE_CALLER] + cands:
        for nm, d in datas.items():
            q._add(("sim", frm, nm), "eth_call", [{"from": frm, "to": token, "data": d}, env.block_hex])
    r = q.run()
    errs.extend(e for e in errors_of(r))
    summ = {"candidates": {}, "baseline": None, "verified_any": False, "implementation_used": impl, "needed_roles": []}
    import re as _re
    for k_, res_ in r.items():
        m_ = _re.search(r"missing role (0x[0-9a-fA-F]{64})", res_.msg or "")
        if m_ and m_.group(1).lower() not in summ["needed_roles"]:
            summ["needed_roles"].append(m_.group(1).lower())
    base_ok = [nm for nm in datas if r[("sim", BASELINE_CALLER, nm)].ok]
    summ["baseline"] = {"from": BASELINE_CALLER, "succeeded": base_ok,
                        "ev": [r[("sim", BASELINE_CALLER, nm)].ev for nm in datas]}
    verdict = {}
    for a in cands:
        oks = [nm for nm in datas if r[("sim", a, nm)].ok]
        res = [r[("sim", a, nm)] for nm in datas]
        if any(x.kind == "error" for x in res):
            verdict[a] = "error"
        elif base_ok or (kind == "deprecate" and any(r[("sim", BASELINE_CALLER, nm)].kind != "revert" for nm in datas)):
            verdict[a] = "inconclusive"
        else:
            verdict[a] = "verified" if oks else "reverts"
        summ["candidates"][a] = {"verdict": verdict[a], "succeeded": oks, "ev": [x.ev for x in res]}
    any_ver = any(v == "verified" for v in verdict.values())
    if (not any_ver and not _alt and kind == "uups" and not base_ok
            and any("same" in (r[("sim", a, nm)].msg or "").lower() for a in cands for nm in datas)):
        # some proxies refuse "upgrade to the current implementation" ("Proxy: Attemps update proxy with the same implementation"):
        # retry with the proxy's own address as the (code-bearing) target; eth_call changes nothing on chain
        roots2, summ2 = simulate_upgrades(env, ev, token, token, roots, errs, kind, _alt=True)
        summ2["implementation_used"] = token
        summ2["note"] = f"current implementation {impl} was refused as a no-op upgrade; simulated with the proxy address as target"
        return roots2, summ2
    summ["verified_any"] = any_ver
    out = []
    for a, via in roots:
        base = via.split(" [")[0]
        v = verdict.get(a)
        if v == "verified":
            out.append((a, base + " [verified: eth_call upgrade from this address succeeds]"))
        elif base.endswith("_slot"):  # admin slot is authoritative for transparent/zos proxies even if not simulatable
            out.append((a, base + f" [admin slot; upgrade simulation: {v}]"))
        elif v == "reverts":
            if not any_ver:
                out.append((a, base + " [unverified: simulated upgrade reverts for this address]"))
        else:
            out.append((a, base + f" [unverified: simulation {v}]"))
    if not any_ver and not out:
        pass
    return out, summ


def upgradeability(row, px, union):
    pt = row["proxy_type"]
    paths = row["upgrade_paths"]
    if row["is_proxy"] is False and row.get("upgrade_path_type") != "legacy_deprecate_forward":
        return "no", "not a proxy: bytecode is fixed (no DELEGATECALL-based upgrade path observed)"
    if row["is_proxy"] is None:
        return "unknown", "DELEGATECALL in bytecode, proxy status could not be determined"
    if pt in ("eip1167", "eip1167_0age", "eip7511_push0", "eip1167_vanity"):
        return "no", "minimal-proxy clone: implementation hard-coded, not upgradeable"
    if not paths:
        return "no_known_path", "proxy, but no upgrade authority was found via the standard slots/getters"
    firm = [p for p in paths if not p.get("assumed_or_unverified")]
    use = firm or paths
    kinds = {p["terminal_kind"] for p in use}
    if kinds <= {"renounced"}:
        return "frozen", "upgrade authority is the zero/dead address"
    if not firm:
        return "yes", ("an upgrade function exists but the authority is NOT verified (candidates tried: "
                       + ", ".join(sorted(kinds)) + ")")
    return "yes", "upgrade authority: " + ", ".join(sorted(kinds)) + (
        "  [verified by eth_call upgrade simulation]" if any(p.get("verified") for p in firm) else "")


def control_summary(row, priv):
    if row["is_proxy"] is False and not row["owner_chain"] and not any(
            k_ for k_ in row["roles"]) and not priv and not row["wards"]["holders"]:
        return "immutable"
    if row["is_proxy"] is False and not row["owner_chain"] and not row["roles"] and not row["wards"]["holders"]:
        return "unattributed_privileges"  # privileged selectors exist but no controller found via known interfaces
    return "controlled"


# =====================================================================================================================
# DAI-style wards
# =====================================================================================================================
REL_SIG = "0x" + k("Rely(address)").hex()
RELY_NOTE = "0x65fae35e" + "00" * 28  # LogNote anonymous event: topic0 = msg.sig (rely)
DENY_NOTE = "0x9c52a7f1" + "00" * 28


def wards_holders(env, ev, addr, errs):
    out = {"holders": [], "status": "n/a"}
    if not env.etherscan_ok:
        out["status"] = "logs_unavailable"
        return out
    cands = {}
    cid = CHAINS[env.chain]["id"]
    for t0, label in ((REL_SIG, "Rely(address)"), (RELY_NOTE, "LogNote(rely)")):
        res = etherscan_get(ev, cid, dict(module="logs", action="getLogs", fromBlock=0, toBlock=env.block_int,
                                          address=addr, topic0=t0, page=1, offset=1000))
        if not res.ok:
            out["status"] = f"logs_unavailable: {res.msg}"
            errs.append({"key": "etherscan_rely_logs", "msg": res.msg, "ev": res.ev})
            return out
        logs = res.value.get("result") or []
        if isinstance(logs, list):
            for lg in logs:
                tp = lg.get("topics", [])
                for tt in tp[1:3]:
                    cands["0x" + tt[-40:].lower()] = res.ev
    cands.pop(ZERO, None)
    q = Q(env, ev)
    for c in cands:
        q.call(addr, SEL["wards"] + pad_addr(c), ("w", c))
    rr = q.run() if cands else {}
    errs.extend(errors_of(rr))
    for c, e in cands.items():
        res = rr.get(("w", c))
        if res is not None and res.ok and dec_uint(res.value) == 1:
            out["holders"].append({"address": c, "ev": res.ev})
    out["status"] = f"rely logs + wards() ({len(cands)} candidates)"
    return out


# =====================================================================================================================
# Base B20 (native precompile tokens: eth_getCode == 0xef, ERC-20 + AccessControl-style roles + policy registry)
# Spec: https://docs.base.org/specifications/b20/reference/constants , base/base-std IB20.sol
# =====================================================================================================================
B20_ROLES = ["DEFAULT_ADMIN_ROLE", "MINT_ROLE", "BURN_ROLE", "BURN_BLOCKED_ROLE", "SEIZE_ROLE", "PAUSE_ROLE", "UNPAUSE_ROLE",
             "METADATA_ROLE", "OPERATOR_ROLE"]
B20_BUCKET = {"DEFAULT_ADMIN_ROLE": "default_admin", "MINT_ROLE": "minter", "BURN_ROLE": "burner",
              "BURN_BLOCKED_ROLE": "seizer", "SEIZE_ROLE": "seizer", "PAUSE_ROLE": "pauser", "UNPAUSE_ROLE": "pauser",
              "METADATA_ROLE": "metadata", "OPERATOR_ROLE": "operator"}
B20_POLICY_SCOPES = ["TRANSFER_SENDER_POLICY", "TRANSFER_RECEIVER_POLICY", "TRANSFER_EXECUTOR_POLICY", "MINT_RECEIVER_POLICY",
                     "SEIZE_EXEMPT_POLICY", "SEIZE_RECEIVER_POLICY"]
B20_ASSET_ROLE = "ASSET_ROLE"  # placeholder; asset-only role names are probed by name below
B20_FACTORY = "0xb20f000000000000000000000000000000000000"
B20_POLICY_REGISTRY = "0x8453000000000000000000000000000000000002"


def _find_creation_block(env, ev, token, hi, errs):
    """Binary search the first block at which the address has code (needs archive state)."""
    lo = 0
    while lo < hi:
        mid = (lo + hi) // 2
        res = env.client.one(ev, "eth_getCode", [token, hex(mid)], block=hex(mid))
        if not res.ok:
            errs.append({"key": f"creation_search eth_getCode@{mid}", "msg": res.msg, "ev": res.ev})
            return None
        if res.value in ("0x", None):
            lo = mid + 1
        else:
            hi = mid
    return lo


def _probe_b20(env, token, ev, row, errs, r1, code_r):
    row["code"] = {"size": 1, "raw": "0xef", "ev": code_r.ev,
                   "note": "1-byte code 0xef (invalid opcode, EIP-3541 prefix): behaviour is implemented by the Base node (B20 precompile), not by EVM bytecode"}
    for n in ("symbol", "decimals"):
        res = r1.get(("v", token, n))
        if res is not None and res.ok:
            row[n] = dec_str(res.value) if n == "symbol" else dec_uint(res.value)
    role_hash = {n: "0x" + k(n).hex() for n in B20_ROLES if n != "DEFAULT_ADMIN_ROLE"}
    role_hash["DEFAULT_ADMIN_ROLE"] = "0x" + EMPTY32
    q = Q(env, ev)
    q.call(token, sel("MINT_ROLE()"), ("mintrole",))
    q.call(token, sel("supplyCap()"), ("supplyCap",))
    q.call(token, sel("pausedFeatures()"), ("paused",))
    q.call(token, sel("uiMultiplier()"), ("uiMult",))
    q.call(token, sel("currency()"), ("currency",))
    for sc in B20_POLICY_SCOPES:
        q.call(token, sel("policyId(bytes32)") + k(sc).hex(), ("pol", sc))
    r = q.run()
    errs.extend(errors_of(r))
    mr = r[("mintrole",)]
    is_b20 = mr.ok and mr.value == role_hash["MINT_ROLE"]
    row["is_proxy"] = False
    row["proxy_type"] = "b20_precompile" if is_b20 else "unknown_0xef_code"
    row["proxy_confidence"] = "getter" if is_b20 else None
    row["proxy_detected_via"] = "code==0xef and MINT_ROLE()==keccak256('MINT_ROLE')" if is_b20 else "code==0xef"
    row["implementation"], row["implementation_chain"], row["impl_is_proxy"] = None, [], False
    row["upgrade_admin_chain"], row["upgrade_paths"], row["upgrade_simulation"] = [], [], None
    row["token_standard"] = "B20" if is_b20 else None
    if not is_b20:
        row["status"] = "unprobeable"
        row["reason"] = "code is a single 0xef byte but MINT_ROLE() did not answer like a B20 token"
        return
    variant = "asset" if r[("uiMult",)].ok else ("stablecoin" if r[("currency",)].ok else "unknown")
    row["b20"] = {"variant": variant, "supply_cap": dec_uint(r[("supplyCap",)].value) if r[("supplyCap",)].ok else None,
                  "paused_features_raw": r[("paused",)].value if r[("paused",)].ok else None,
                  "ui_multiplier": dec_uint(r[("uiMult",)].value) if r[("uiMult",)].ok else None,
                  "policies": {sc: dec_uint(r[("pol", sc)].value) if r[("pol", sc)].ok else None for sc in B20_POLICY_SCOPES},
                  "ev": {"supplyCap": r[("supplyCap",)].ev, "paused": r[("paused",)].ev,
                         "policies": [r[("pol", sc)].ev for sc in B20_POLICY_SCOPES]},
                  "factory": B20_FACTORY, "policy_registry": B20_POLICY_REGISTRY}
    # ---- role holders: creation-window logs + hasRole verification ---------------------------------------------------
    cb = _find_creation_block(env, ev, token, env.block_int, errs)
    holders = {}
    status = "creation block not found (archive state unavailable)"
    if cb is not None:
        row["b20"]["creation_block"] = cb
        res = env.client.one(ev, "eth_getLogs", [{"address": token, "fromBlock": hex(cb), "toBlock": hex(min(cb + 9999, env.block_int)),
                                                  "topics": [ROLE_GRANTED]}], block=hex(env.block_int))
        if res.ok:
            cands = []
            for lg in res.value or []:
                t = lg["topics"]
                pair = (t[1].lower(), "0x" + t[2][-40:].lower())
                if pair not in cands:
                    cands.append(pair)
            q = Q(env, ev)
            for role, acct in cands:
                q.call(token, SEL["hasRole"] + role[2:] + pad_addr(acct), ("hr", role, acct))
            rr = q.run() if cands else {}
            errs.extend(errors_of(rr))
            names = {v: n for n, v in role_hash.items()}
            for role, acct in cands:
                x = rr.get(("hr", role, acct))
                if x is not None and x.ok and dec_bool(x.value):
                    holders.setdefault(names.get(role, role), []).append(
                        {"address": acct, "via": "RoleGranted log in creation window + hasRole() at pinned block", "ev": x.ev})
            status = (f"RoleGranted logs from creation block {cb} to {min(cb + 9999, env.block_int)} (~5.5h window) + hasRole() "
                      f"verification; roles granted after the window are NOT discovered")
        else:
            status = f"getLogs failed: {res.msg}"
            errs.append({"key": "b20_getLogs", "msg": res.msg, "ev": res.ev})
    if cb is None:
        # no archive state: fall back to ACTIVITY candidates from the last ~9000 blocks (event callers), verified by hasRole()
        evs = ["Memo(address,bytes32)", "Seized(address,address,address,uint256)", "BurnedBlocked(address,address,uint256)",
               "Paused(address,uint8[])", "Unpaused(address,uint8[])", "SupplyCapUpdated(address,uint256,uint256)",
               "PolicyUpdated(bytes32,uint64,uint64)", "RoleGranted(bytes32,address,address)", "RoleRevoked(bytes32,address,address)"]
        t0s = ["0x" + k(s).hex() for s in evs]
        lo_b = max(0, env.block_int - 8999)
        res = env.client.one(ev, "eth_getLogs", [{"address": token, "fromBlock": hex(lo_b), "toBlock": hex(env.block_int),
                                                  "topics": [t0s]}], block=hex(env.block_int))
        if res.ok:
            freq = {}
            for lg in res.value or []:
                t = lg["topics"]
                if len(t) > 1 and t[1][2:26] == "0" * 24 and lg["topics"][0] != "0x" + k("PolicyUpdated(bytes32,uint64,uint64)").hex():
                    a_ = "0x" + t[1][-40:]
                    freq[a_] = freq.get(a_, 0) + 1
                if t[0] == ROLE_GRANTED and len(t) > 2:
                    a_ = "0x" + t[2][-40:]
                    freq[a_] = freq.get(a_, 0) + 100
            cands = [a_ for a_, _ in sorted(freq.items(), key=lambda x: -x[1]) if a_ != ZERO][:15]
            q = Q(env, ev)
            for a_ in cands:
                for rn, rh in role_hash.items():
                    q.call(token, SEL["hasRole"] + rh[2:] + pad_addr(a_), ("hr2", rn, a_))
            rr = q.run() if cands else {}
            errs.extend(errors_of(rr))
            for a_ in cands:
                for rn in role_hash:
                    x = rr.get(("hr2", rn, a_))
                    if x is not None and x.ok and dec_bool(x.value):
                        holders.setdefault(rn, []).append({"address": a_, "via": "event-caller in last 9000 blocks + hasRole() at pinned block", "ev": x.ev})
            status = (f"no archive access: candidates = addresses seen as callers in Memo/Seized/Paused/RoleGranted/... events in blocks "
                      f"{lo_b}..{env.block_int} ({len(cands)} tested with hasRole); role holders that were inactive in that window are NOT discovered")
        else:
            status = f"no archive access and recent getLogs failed: {res.msg}"
            errs.append({"key": "b20_recent_getLogs", "msg": res.msg, "ev": res.ev})
    row["access_control"] = {"status": status, "holders": {rn: [h["address"] for h in hs] for rn, hs in holders.items()}}
    roles = {}
    for rn, hs in holders.items():
        for h in hs:
            roles.setdefault(B20_BUCKET.get(rn, "other_role:" + rn), []).append(
                {"address": h["address"], "via": f"B20 {rn} ({h['via']})", "ev": h["ev"]})
    for b, hs in roles.items():
        for h in hs:
            ch, ps = expand(env, [(h["address"], h["via"])])
            h["kind"], h["summary"] = ch[0]["kind"], ch[0]["summary"]
            h["terminal_kinds"] = sorted({p["terminal_kind"] for p in ps})
            h["terminals"] = [{"address": p["terminal_address"], "kind": p["terminal_kind"], "summary": p["terminal_summary"],
                               "timelock_delay_seconds": p["timelock_delay_seconds"]} for p in ps][:6]
    row["roles"] = roles
    roots = [(h["address"], "DEFAULT_ADMIN_ROLE") for h in holders.get("DEFAULT_ADMIN_ROLE", [])]
    ow_chain, ow_paths = expand(env, roots) if roots else ([], [])
    row["owner_chain"], row["owner_paths"] = ow_chain, ow_paths
    row["wards"] = {"status": "n/a", "holders": []}
    row["paused"] = None
    row["pending_owner"] = None
    allc = {}
    for nd in ow_chain:
        allc[nd["address"]] = env.ctrl[nd["address"]]
    for hs in roles.values():
        for h in hs:
            for p in expand(env, [(h["address"], "")])[0]:
                allc[p["address"]] = env.ctrl[p["address"]]
    row["controllers"] = {a: {"kind": n["kind"], "summary": n["summary"], "details": brief_details(n), "next": n["next"],
                              "ev": n["ev"]} for a, n in allc.items()}
    row["capability_hints"] = {"b20_standard": ["mint (MINT_ROLE)", "burn (BURN_ROLE)", "seizeWithMemo (SEIZE_ROLE)", "pause/unpause per feature",
                                                "policy gating (allow/block lists via PolicyRegistry)", "updateSupplyCap"]}
    row["privileged_capabilities"] = ["mint", "burn_from_others", "pause", "blacklist"]
    row["selector_count"] = None
    row["upgradeable"] = "protocol_only"
    row["upgradeable_reason"] = ("not an EVM contract: logic is Base node code (B20 precompile); it can only change through a Base "
                                 "hardfork / node upgrade, not by the token issuer")
    row["upgrade_authority_firm"] = None
    row["upgrade_terminal_kinds"] = []
    row["owner_terminal_kinds"] = sorted({p["terminal_kind"] for p in ow_paths})
    row["control_summary"] = "controlled"
    row["upgrade_authority_basis"] = "n/a (B20 precompile)"
