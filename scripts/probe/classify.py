"""Turn the raw harness output of one token (Ctx from probe.py) into one result row per test.

Row schema (see README.md):
  chain, token, test, status (pass|quirk|fail|unprobeable|error), value, detail, block,
  evidence (path of the per-token raw file), evidence_calls ([cid..] inside that file), script (harness script name or
  null), evidence_sha256 ([sha256 of each cited response])

Status semantics
  pass        behaves like the plain OpenZeppelin-style baseline (or: optional feature absent/present without anything an
              integrator must special-case)
  quirk       works, but deviates from the baseline in a way integrators must handle (no return value, fee, callback,
              pause/blocklist machinery, revert on zero value ...). `value` says how.
  fail        a core ERC-20 operation does not do what it must in the happy path from a freshly funded contract holder
              (normal transfer / approve / transferFrom reverts or lies), or an operation that must revert succeeds.
  unprobeable the test could not be run for a stated reason (no balance slot, normal transfer reverts so a derived
              measurement is meaningless, no archive state, no ERC-1820 registry ...). Never guessed.
  error       infrastructure problem (RPC failure, harness ran out of gas, decode problem). Retried on resume.
"""
import re

from common import *
import battery as B

TWO96 = 1 << 96
JS_SAFE = 2 ** 53
ANTIBOT_PATS = [
    ("trading_not_enabled", r"trading.*(not|isn.?t|closed|disabled|enabled|open)|not (yet )?(open|launched|live|enabled)|"
                            r"launch|not started|swap.*(not|disabled)|tradingOpen|trade.*disabled"),
    ("max_tx", r"max.?(tx|transaction|transfer)|exceeds? the (max|limit)|transfer amount exceeds the max|too (large|much)|"
               r"over.?limit|anti.?whale|max.?sell|max.?buy"),
    ("max_wallet", r"max.?wallet|wallet (limit|balance)|exceeds? (the )?max.?wallet|max.?hold"),
    ("cooldown", r"cool.?down|delay|too (soon|early|fast|frequent)|one .*per block|same block|wait|blocks? between|"
                 r"transfer.?delay"),
    ("bot", r"\bbot\b|sniper|anti.?bot|bots"),
    ("blacklist", r"black.?list|block.?list|blocked|banned|frozen|restricted|denied|sanction"),
    ("paused", r"paus|stopped|halt"),
]


# Why does a normal transfer from a freshly funded contract holder revert?  permissioned = the token only lets allow-listed /
# KYC'd / role-holding parties move it (RWA and stable-coin issuers); funding_artifact_suspect = accounting errors that are
# more likely caused by our slot override than by the token (vote checkpoints, SafeMath underflow, separate balance books).
PERMISSIONED_PAT = (r"kyc|regist(ry|ered)|white.?list|allow.?list|not allowed|not authori[sz]ed|unauthori[sz]ed|not eligible|"
                    r"not approved|compliance|permission|has.?n.?t role|missing role|accesscontrol|accredited|investor|"
                    r"validation module|not (a )?(member|participant|whitelisted)|only (owner|admin|minter)|restricted")
ACCOUNTING_PAT = r"underflow|subtraction overflow|exceeds balance|insufficient (balance|funds)|vote amount|arithmetic"
# custom-error names resolved once through the public 4byte signature database (api.4byte.sourcify.dev) for the selectors seen in
# the Ethereum run, plus common OpenZeppelin / issuer names. class -> signatures
ERROR_CLASSES = {
    "permissioned": [
        "Unauthorized()", "NotAllowed()", "NotAuthorized()", "AccessDenied()", "Restricted()", "NotWhitelisted()",
        "SenderNotAllowed()", "ReceiverNotAllowed()", "NotKYCed()", "UnauthorizedAccount(address)",
        "AccessControlUnauthorizedAccount(address,bytes32)", "NotPermissioned()", "UnauthorizedFrom(address,address)",
        "InsufficientPermissions()", "ERC20RevocableComplianceAddressNotWhitelisted()", "NotKyced(address)",
        "NotTokenUser(address)", "TransferNotAllowed()", "TransferRestricted()", "TransferNotCompliant()",
        "AccountNotAllowed()", "ERC20Restricted(address)", "SenderNotWhitelisted()", "InvalidAccount()"],
    "transfer_rejected_by_policy": ["InvalidTransfer()", "TransferBlocked()", "InvalidReceiver()", "InvalidSender()",
                                    "InvalidRecipient(address)", "InvalidAddress()", "ERC20InvalidReceiver(address)"],
    "blocklisted_or_frozen": ["AccountIsFrozen()", "AccountFrozen()", "Blacklisted()", "Blocked()", "BlockedAddress()",
                              "SenderBlacklisted()", "RecipientBlacklisted()", "Frozen()", "AddressFrozen(address)",
                              "AddressFrozen()", "BlacklistedAddress(address)", "AccountBlocked(address)"],
    "paused": ["EnforcedPause()", "Paused()", "TransfersPaused()", "TokenPaused()", "ContractPaused()"],
    "market_closed": ["MarketNotOpen()"],
    "router_or_venue_restricted": ["NotValidRouter()"],
    "non_transferable": ["Aera__VaultUnitsNotTransferable(address)", "TransferToContract()", "TransferToContractNotAllowed(address)",
                         "ERC20TokenIsReceiver()", "TransferToTokenContract()"],
    "limit_or_zero_amount": ["TransferAmountExceedsMaxTx()", "TransferTooLarge()", "AmountIsZero()", "NullTransfer()",
                             "AmountMustBeGreaterThanZero()", "ZeroTransferNotAllowed()", "AmountLargerThanUnlockedAmount()"],
    "funding_artifact_suspect": ["ERC20InsufficientBalance(address,uint256,uint256)", "InsufficientBalance()",
                                 "AmountExceedsBalance()", "ERC20InsufficientAllowance(address,uint256,uint256)"],
}


def _err_sel(sig):
    return hx(sel(sig))[2:10]


_ERR_CLASS = {}
for _cls, _lst in ERROR_CLASSES.items():
    for _sig in _lst:
        _ERR_CLASS[_err_sel(_sig)] = (_cls, _sig)


def revert_class(reason):
    """reason = 'error_string:<text>' | 'custom:0x<selector>...' | 'empty' | ... -> (class, detail)"""
    r = reason or ""
    m = re.match(r"custom:0x([0-9a-fA-F]{8})", r)
    if m:
        c = _ERR_CLASS.get(m.group(1).lower())
        return (c[0], c[1]) if c else ("unknown_custom_error", "0x" + m.group(1))
    if re.search(r"paus", r, re.I):
        return "paused", ""
    if re.search(r"black.?list|block.?list|blocked|frozen|freez|banned|sanction", r, re.I):
        return "blocklisted_or_frozen", ""
    if re.search(PERMISSIONED_PAT, r, re.I):
        return "permissioned", ""
    if re.search(ACCOUNTING_PAT, r, re.I):
        return "funding_artifact_suspect", "accounting error after storage override (vote checkpoints / SafeMath / separate books)"
    if r in ("empty", "reverts(empty)") or r.startswith("empty"):
        return "empty_revert", ""
    return "other", ""


def big(v):
    if isinstance(v, int) and abs(v) > JS_SAFE:
        return str(v)
    if isinstance(v, dict):
        return {k: big(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [big(x) for x in v]
    return v


def reason_of(step):
    k, t = revert_info(step.ret)
    return f"{k}:{t}" if t else k


def antibot_class(text):
    out = []
    for name, pat in ANTIBOT_PATS:
        if re.search(pat, text or "", re.I):
            out.append(name)
    return out


class Rows:
    def __init__(self, ctx, evidence_path, rec):
        self.ctx = ctx
        self.rows = []
        self.evidence_path = evidence_path
        self.rec = rec

    def add(self, test, status, value=None, detail=None, script=None, calls=None, shas=None):
        ctx = self.ctx
        sc = getattr(ctx, "script_calls", {}).get(script) if script else None
        cl = list(calls) if calls is not None else ([sc] if sc is not None and sc >= 0 else [])
        sh = list(shas) if shas is not None else ([self.rec.calls[sc]["response_sha256"]] if sc is not None and sc >= 0 else [])
        self.rows.append({
            "chain": ctx.chain, "token": ctx.token, "test": test, "status": status, "value": big(value),
            "detail": big(detail or {}), "block": ctx.block, "script": script,
            "evidence": self.evidence_path, "evidence_calls": cl, "evidence_sha256": sh,
        })


BLOCK_CODE_SIGS = [
    "addBlackList(address)", "addBlacklist(address)", "removeBlackList(address)", "removeBlacklist(address)",
    "blacklist(address)", "blacklist(address,bool)", "unBlacklist(address)", "unblacklist(address)", "blackList(address)",
    "isBlacklisted(address)", "isBlackListed(address)", "getBlackListStatus(address)", "destroyBlackFunds(address)",
    "setBlacklisted(address,bool)", "setBlacklist(address,bool)", "setBlackList(address,bool)", "blockAccount(address)",
    "unBlockAccount(address)", "addToBlocklist(address)", "addToBlacklist(address)", "removeFromBlacklist(address)",
    "removeFromBlocklist(address)", "blockUser(address)", "freeze(address)", "freezeAccount(address,bool)",
    "freezeAccount(address)", "unfreeze(address)", "setBots(address[])", "addBots(address[])", "delBot(address)",
    "blockAddress(address)", "setBot(address,bool)", "setBlocked(address,bool)", "isBlocked(address)", "isFrozen(address)",
    "frozen(address)", "isBlocklisted(address)", "blocklist(address)", "addBlocklist(address)", "blockedList(address)",
    "wipeFrozenAddress(address)", "setFrozen(address,bool)", "updateBlacklist(address,bool)", "denylist(address)",
    "addToDenylist(address)", "ban(address)", "unban(address)", "banAddress(address)", "lockAccount(address)",
    "setRestrictedAddress(address,bool)",
]


def fund_cids(ctx):
    fi = ctx.fund_info
    ids = set()
    for x in fi.get("candidates_tried", []):
        ids.update(c for c in (x.get("cids") or []) if c is not None)
    ids.update(c for c in fi.get("width_cids", []) if c is not None)
    for cal in fi.get("calibration", []):
        ids.update(r["cid"] for r in cal["rounds"] if r.get("cid") is not None)
    ids.update(c for c in (fi.get("native_probe") or {}).get("cids", []) if c is not None)
    ids.update(g["cid"] for g in fi.get("brute_groups", []) if g.get("cid") is not None)
    hf = fi.get("holder_fallback") or {}
    ids.update(w["cid"] for w in hf.get("log_windows", []) if w.get("cid") is not None)
    for cnd in hf.get("candidates", []):
        ids.update(c for c in cnd.get("cids", []) if c is not None)
    ids.update(x["cid"] for x in hf.get("attempts", []) if x.get("cid") is not None)
    bl = getattr(ctx, "baseline", None)
    if bl is not None and bl.get("_cid", -1) is not None and bl.get("_cid", -1) >= 0 and "baseline_error" in fi:
        ids.add(bl["_cid"])
    return sorted(ids)


def u(ctx, script, label):
    st = ctx.results.get(script, {}).get(label)
    return st.u256() if st is not None else None


def S(ctx, script, label):
    return ctx.results.get(script, {}).get(label)


def classify(ctx, rec, evidence_path):
    """-> list of rows"""
    R_ = Rows(ctx, evidence_path, rec)
    br = getattr(ctx, "battery_resp", None)

    def error_all(msg, tests=None):
        R_.add("probe", "error", msg)
        return R_.rows

    if getattr(ctx, "transport_error", None):
        return error_all("transport: " + ctx.transport_error)
    if getattr(ctx, "no_code", False):
        R_.add("probe", "unprobeable", "no_code_at_block" if (ctx.code in (None, "0x")) else "code_not_a_normal_contract",
               {"reason": getattr(ctx, "no_code_reason", "")},
               calls=[ctx.p1[0]["_cid"]], shas=[rec.calls[ctx.p1[0]["_cid"]]["response_sha256"]])
        return R_.rows

    # ---- code / selectors (always possible once we have code) -------------------------------------------------------
    _code_rows(ctx, rec, R_)
    # ---- funding -----------------------------------------------------------------------------------------------------
    fnd = ctx.funding
    fcalls = fund_cids(ctx)
    fshas = [rec.calls[c]["response_sha256"] for c in fcalls]
    if fnd is not None and fnd.method == "real_holder":
        hfi = ctx.fund_info.get("holder_fallback", {})
        R_.add("funding", "pass", "real_holder_simulateV1",
               {"method": "real_holder", "holder": fnd.holder, "holder_is_eoa": fnd.holder_is_eoa,
                "holder_balance": fnd.holder_balance, "requested": fnd.requested, "balance": fnd.balance,
                "target_balance": ctx.target, "amount_used": ctx.a, "why_slot_funding_failed": ctx.fund_info.get("holder_fallback", {}).get("why"),
                "method_note": "no overridable balance slot: a real holder transfers to the harness inside an eth_simulateV1 block "
                               "(nothing is sent on chain); slot-dependent measurements (balance width, slot ratio, drift, "
                               "EOA mode) are not available",
                "log_windows": hfi.get("log_windows"), "candidates": hfi.get("candidates"), "attempts": hfi.get("attempts")},
               calls=fcalls, shas=fshas)
        R_.add("balance_storage", "unprobeable", "real_holder_funding(slot_unknown)",
               {"native_protocol_token": bool(getattr(ctx, "native_code", False))}, calls=fcalls[:1], shas=fshas[:1])
    elif fnd is not None:
        fund_detail = {
            "method": fnd.method, "slot_address": fnd.addr, "slot_key": fnd.key, "slot_value": fnd.value,
            "balance": fnd.balance, "balance_over_slot": fnd.ratio, "identity_map": fnd.identity,
            "balance_bits_upper_bound": fnd.balance_bits, "balance_bits_lower_bound": getattr(fnd, "balance_bits_lo", None), "target_balance": ctx.target, "amount_used": ctx.a,
            "candidates": [{k: x[k] for k in ("addr", "key", "b_SA", "b_SB") if k in x}
                           for x in ctx.fund_info.get("candidates_tried", [])],
            "width_probes": ctx.fund_info.get("width_probes"),
            "brute_label": getattr(fnd, "brute_label", None),
        }
        R_.add("funding", "pass", fnd.method, fund_detail, calls=fcalls, shas=fshas)
        own = fnd.addr == ctx.token.lower() or fnd.method == "native_balance"
        R_.add("balance_storage", "pass" if own else "quirk",
               "native_balance_of_address" if fnd.method == "native_balance" else
               ("token_own_storage" if own else f"external_storage:{fnd.addr}"),
               {"slot_address": fnd.addr, "note": "balances read from another contract's storage (double-entry / "
                "forwarding / storage-contract token) when external"} if not own else {},
               calls=fcalls[:4], shas=fshas[:4])
    else:
        R_.add("funding", "unprobeable", ctx.fund_reason,
               {"acl_error": ctx.acl_err if hasattr(ctx, "acl_err") else None,
                "candidates": ctx.fund_info.get("candidates_tried"),
                "brute_groups": ctx.fund_info.get("brute_groups"), "native_probe": ctx.fund_info.get("native_probe"),
                "holder_fallback": ctx.fund_info.get("holder_fallback")},
               calls=fcalls, shas=fshas)

    # ---- battery availability ----------------------------------------------------------------------------------------
    if br is None:
        return R_.rows
    if getattr(ctx, "battery_decode_error", None):
        R_.add("battery", "error", "decode: " + ctx.battery_decode_error, script=None)
        return R_.rows
    for be in getattr(ctx, "battery_errors", []):
        cid = be.get("cid")
        R_.add("battery", "error", f"harness eth_call failed for scripts {be['scripts']}: " + str((be.get("error") or {}).get("message", ""))[:300],
               {"error": be.get("error")}, calls=[cid] if cid is not None and cid >= 0 else [],
               shas=[rec.calls[cid]["response_sha256"]] if cid is not None and cid >= 0 else [])
    if not ctx.results:
        return R_.rows
    funded = fnd is not None
    _views_rows(ctx, rec, R_)
    if funded:
        _funded_rows(ctx, rec, R_)
    else:
        for t in FUNDED_TESTS:
            R_.add(t, "unprobeable", "no_balance_slot", {"reason": ctx.fund_reason})
    _drift_and_rebasing(ctx, rec, R_)
    return R_.rows


FUNDED_TESTS = [
    "transfer_return", "transfer_insufficient", "fee_on_transfer", "transfer_zero_value", "self_transfer",
    "transfer_to_zero_address", "transfer_to_dead_address", "transfer_to_token_contract", "large_uint96",
    "max_uint_transfer", "full_balance_transfer", "max_tx_limit", "approve_return", "approve_nonzero_to_nonzero",
    "approve_max", "approve_zero_address", "approve_zero_value", "allowance_helpers", "transferFrom_return",
    "transferFrom_allowance_decrement", "transferFrom_infinite_allowance", "transferFrom_no_allowance",
    "transferFrom_exceed_allowance", "transferFrom_by_owner", "transferFrom_zero_value", "erc777_hooks",
    "contract_recipient", "gas_transfer", "same_block_repeat", "eoa_vs_contract", "anti_bot_limits",
]


# ------------------------------------------------------------------------------------------------------------------------
def _code_rows(ctx, rec, R_):
    sels = set(selectors_from_code(ctx.code))
    impl_sels = set()
    if getattr(ctx, "impl_code", None) and ctx.impl_code != "0x":
        impl_sels = set(selectors_from_code(ctx.impl_code))
    allsels = sorted(sels | impl_sels)
    ctx.selectors = set(allsels)
    code_b = unhex(ctx.code)
    detail = {
        "code_len": len(code_b), "code_keccak": hx(keccak(code_b)),
        "own_selectors": sorted(sels), "implementation": getattr(ctx, "impl", None),
        "impl_code_keccak": hx(keccak(unhex(ctx.impl_code))) if getattr(ctx, "impl_code", None) not in (None, "0x") else None,
        "impl_selectors": sorted(impl_sels), "selectors": allsels, "selector_note":
            "PUSH4 scan: over-approximate (includes non-selector constants); implementation = EIP-1967/OZ-legacy slot or "
            "EIP-1167 target only (beacon / diamond / custom proxies not resolved: that is W3's job)",
    }
    if getattr(ctx, "native_code", False):
        detail["native_protocol_token"] = ("eth_getCode is the single byte 0xef but the address answers ERC-20 calls: the token "
                                           "logic is native to the chain client (no EVM bytecode/storage; state and code "
                                           "overrides on this address are ignored; eth_createAccessList is empty)")
        R_.add("bytecode_selectors", "quirk", "native_protocol_token(code=0xef)", detail,
               calls=[ctx.p1[0]["_cid"]], shas=[rec.calls[ctx.p1[0]["_cid"]]["response_sha256"]])
        return
    R_.add("bytecode_selectors", "pass", {"n_selectors": len(allsels), "proxy_implementation": getattr(ctx, "impl", None)},
           detail, calls=[ctx.p1[0]["_cid"]], shas=[rec.calls[ctx.p1[0]["_cid"]]["response_sha256"]])


def _have(ctx, sig):
    return hx(sel(sig))[2:] in ctx.selectors


def _views_rows(ctx, rec, R_):
    ctx.rb_acc, ctx.fl_acc, ctx.erc777_static = {}, {}, {}
    ctx.erc1820_registry_present = ctx.erc777_registered = False
    ctx.ts_views = None
    v = ctx.results.get("views")
    if not v:
        R_.add("views", "error", "views script missing: " + str(ctx.script_errors.get("views")), script="views")
        return
    g = lambda lab: v.get(lab)
    # decimals
    st = g("decimals")
    if st is None:
        pass
    elif not st.ok:
        R_.add("decimals", "quirk", "reverts", {"reason": reason_of(st)}, script="views")
    elif st.retlen == 0:
        R_.add("decimals", "quirk", "missing_or_empty_return", {}, script="views")
    elif st.retlen != 32:
        R_.add("decimals", "quirk", f"returns_{st.retlen}_bytes", {"raw": hx(st.ret[:96])}, script="views")
    else:
        d = int.from_bytes(st.ret[:32], "big")
        if d > 255:
            R_.add("decimals", "quirk", "returns_non_uint8", {"raw": d}, script="views")
        elif d <= 5 or d > 18:
            R_.add("decimals", "quirk", d, {"note": "unusual: <=5 decimals (precision loss) or >18 (overflow risk)"}, script="views")
        else:
            R_.add("decimals", "pass", d, script="views")
    # name / symbol
    ns = {}
    for lab in ("name", "symbol"):
        st = g(lab)
        if st is None:
            continue
        if not st.ok:
            ns[lab] = ("reverts", reason_of(st))
        else:
            ns[lab] = decode_string(st.ret, st.retlen)
    if ns:
        kinds = {k: x[0] for k, x in ns.items()}
        bad = [k for k, x in kinds.items() if x != "string"]
        R_.add("name_symbol", "pass" if not bad else "quirk",
               {"name": ns.get("name", (None, None))[1], "symbol": ns.get("symbol", (None, None))[1],
                "name_kind": kinds.get("name"), "symbol_kind": kinds.get("symbol")},
               {"name_kind": kinds.get("name"), "symbol_kind": kinds.get("symbol")}, script="views")
    # permit
    ds, nn, pt = g("v:DOMAIN_SEPARATOR()"), g("v:nonces(address)"), g("v:PERMIT_TYPEHASH()")
    ed = g("v:eip712Domain()")
    present = lambda s: bool(s and s.ok and s.retlen >= 32)
    dai_hash = "0xea2aa0a1be11a07ed86d755c93467f4f82362b452371d1ba94d1715123511acb"
    pth = hx(pt.ret[:32]) if present(pt) else None
    code_permit = {"eip2612_selector_in_code": _have(ctx, "permit(address,address,uint256,uint256,uint8,bytes32,bytes32)"),
                   "dai_selector_in_code": _have(ctx, "permit(address,address,uint256,uint256,bool,uint8,bytes32,bytes32)")}
    if pth == dai_hash or code_permit["dai_selector_in_code"]:
        style = "dai_permit"
    elif code_permit["eip2612_selector_in_code"]:
        style = "eip2612"
    elif present(ds) and present(nn):
        style = "eip2612_inferred(DOMAIN_SEPARATOR+nonces; permit selector not seen in resolved code)"
    else:
        style = "none"
    R_.add("permit", "pass", style,
           {"DOMAIN_SEPARATOR": present(ds), "nonces": present(nn), "PERMIT_TYPEHASH": pth, "eip5267": present(ed),
            **code_permit}, script="views")
    # forwarding / delegating tokens (legacy TUSD-style `delegate()`, Synthetix `target()`), raw proxy accessors
    fw = {}
    for sig in ("delegate()", "target()", "implementation()"):
        s = g("v:" + sig)
        if present(s):
            v_ = int.from_bytes(s.ret[:32], "big")
            fw[sig] = ("0x" + s.ret[12:32].hex()) if (v_ and v_ < (1 << 160) and v_ > 0xFF) else v_
    fwd = {k_: x for k_, x in fw.items() if k_ in ("delegate()", "target()") and isinstance(x, str)}
    R_.add("forwarding", "quirk" if fwd else "pass", {"accessors": fw}, {
        "note": "delegate()/target() = every ERC-20 call is forwarded to another contract (legacy TUSD, Synthetix proxies); "
                "implementation() is recorded raw (proxy classification is W3's job)"}, script="views")
    # pause
    acc = {}
    for sig in B.PAUSE_FUNCS:
        s = g("pause:" + sig)
        if present(s):
            acc[sig] = int.from_bytes(s.ret[:32], "big")
    pause_sels = {n: _have(ctx, sg) for n, sg in (
        ("pause()", "pause()"), ("unpause()", "unpause()"), ("paused()", "paused()"), ("stop()", "stop()"),
        ("pause(bool)", "pause(bool)"), ("pause(bool,bool)", "pause(bool,bool)"), ("setPaused(bool)", "setPaused(bool)"),
        ("pauseTransfers()", "pauseTransfers()"), ("unpauseTransfers()", "unpauseTransfers()"),
        ("pausedPublic()", "pausedPublic()"), ("togglePause()", "togglePause()"), ("emergencyStop()", "emergencyStop()"),
        ("setTransfersEnabled(bool)", "setTransfersEnabled(bool)"), ("enableTransfers(bool)", "enableTransfers(bool)"),
        ("setTradingEnabled(bool)", "setTradingEnabled(bool)"), ("enableTrading()", "enableTrading()"),
        ("openTrading()", "openTrading()"))}
    pause_sels = {n: v for n, v in pause_sels.items() if v}
    paused_now = any(x == 1 for x in acc.values())
    tr_rev = None
    if "transfer" in ctx.results:
        trs = S(ctx, "transfer", "tr")
        if trs is not None and not trs.ok:
            tr_rev = reason_of(trs)
    if paused_now:
        status = "fail"
    elif acc or pause_sels:
        status = "quirk"
    else:
        status = "pass"
    R_.add("pause", status, {"paused": (bool(max(acc.values())) if acc else None), "accessors": acc},
           {"pause_selectors_in_code": pause_sels, "normal_transfer_reverts_with": tr_rev}, script="views")
    # blocklist
    dyn = {}
    for sig in B.BLOCKLIST_FUNCS:
        sh, ss = g("bl_H:" + sig), g("bl_S:" + sig)
        if present(sh):
            name = sig.split("(")[0]
            dyn[name] = {"H": int.from_bytes(sh.ret[:32], "big"),
                         "sanctioned_tornado_router": int.from_bytes(ss.ret[:32], "big") if present(ss) else None}
    code_bl = [sg.split("(")[0] if sg.split("(")[0] not in ("blacklist", "freeze") else sg for sg in BLOCK_CODE_SIGS if _have(ctx, sg)]
    sanc_blocked = {}
    base_ok = (S(ctx, "transfer", "tr") is not None and S(ctx, "transfer", "tr").ok) if "transfer" in ctx.results else None
    for i, tgt in enumerate(SANCTIONED):
        s = S(ctx, f"sanctioned_{i}", "tr")
        if s is not None:
            sanc_blocked[tgt] = (not s.ok) if base_ok else None
    sanc_any = any(x is True for x in sanc_blocked.values())
    sanc_true = any(x["sanctioned_tornado_router"] == 1 for x in dyn.values())
    if dyn or code_bl or sanc_any:
        st_ = "quirk"
    else:
        st_ = "pass"
    R_.add("blocklist", st_, {"view_functions": sorted(dyn), "selectors_in_code": code_bl,
                              "sanctioned_recipient_transfer_reverts": sanc_any,
                              "tornado_router_flagged_by_view": sanc_true},
           {"view_results": dyn, "sanctioned_transfer_reverts": sanc_blocked,
            "note": "dynamic probes use the Tornado Cash router / Ronin-hack address as known-sanctioned recipients; "
                    "'selectors_in_code' is a PUSH4 scan (over-approximate)"},
           script="views")
    # rebasing accessors / fee-limit accessors are consumed by _drift_and_rebasing and anti_bot_limits
    ctx.rb_acc = {}
    for sig, a in B.REBASE_FUNCS:
        s = g("rb:" + sig)
        if present(s):
            ctx.rb_acc[sig.split("(")[0]] = int.from_bytes(s.ret[:32], "big")
    ctx.fl_acc = {}
    for sig, a in B.FEE_LIMIT_FUNCS:
        s = g("fl:" + sig)
        if present(s):
            ctx.fl_acc[sig.split("(")[0]] = int.from_bytes(s.ret[:32], "big")
    lim_words = ("maxtx", "maxtrans", "maxwallet", "trading", "launched", "cooldown", "antibot", "limits", "delay", "swapenabled")
    ADDR_FLAG_FUNCS = {"isExcludedFromFees", "isExcludedFromFee", "_isExcludedFromFee", "isTxLimitExempt"}
    fee_acc = {k: x for k, x in ctx.fl_acc.items() if not any(w in k.lower() for w in lim_words) and k not in ADDR_FLAG_FUNCS}
    nz = {k: x for k, x in fee_acc.items() if x}
    if fee_acc:
        R_.add("fee_parameters", "quirk", {"accessors": fee_acc, "nonzero": sorted(nz)},
               {"note": "fee / tax accessors exist (a fee switch or tax may be active now or later); nonzero => active at this block"},
               script="views")
    else:
        R_.add("fee_parameters", "pass", "no_fee_accessors_found", {}, script="views")
    ctx.ts_views = u(ctx, "views", "totalSupply")
    # ERC777/1363 static evidence
    gran, defop = g("v:granularity()"), g("v:defaultOperators()")
    ctx.erc777_static = {"granularity": present(gran), "defaultOperators": present(defop)}
    e1820 = g("erc1820_token")
    ctx.erc1820_registry_present = bool(e1820 and e1820.ok and e1820.retlen == 32)
    ctx.erc777_registered = bool(e1820 and e1820.ok and e1820.retlen == 32 and int.from_bytes(e1820.ret[:32], "big") != 0)


def _need(ctx, name):
    """returns None if script ok, else an unprobeable/error description"""
    if name in ctx.results:
        return None
    return ctx.script_errors.get(name, "script missing")


def _funded_rows(ctx, rec, R_):
    a, Bal, T = ctx.a, ctx.B, ctx.token
    res = ctx.results
    add = R_.add

    def have(name):
        if name in res:
            return True
        add_missing(name)
        return False

    missing_reported = set()

    def add_missing(name):
        if name in missing_reported:
            return
        missing_reported.add(name)
        msg = str(ctx.script_errors.get(name, "missing"))
        if msg.startswith("script produced no payload"):
            add(f"script:{name}", "unprobeable", "script_exceeds_gas_budget",
                {"reason": msg, "note": "the script used more than the harness budget (30M gas / 1.5M per call): gas-hungry token"})
        elif re.search(r"abort|timeout|timed out|deadline", msg, re.I):
            add(f"script:{name}", "unprobeable", "node_execution_timeout", {"reason": msg})
        else:
            add(f"script:{name}", "error", msg)

    # ---- transfer family -------------------------------------------------------------------------------------------
    tr_ok = False
    tr_reason = None
    if have("transfer"):
        tr = S(ctx, "transfer", "tr")
        sh, dt = shape(tr)
        h0, h1, r0, r1 = (u(ctx, "transfer", x) for x in ("bal_h0", "bal_h1", "bal_r0", "bal_r1"))
        t0, t1, s0, s1 = (u(ctx, "transfer", x) for x in ("bal_t0", "bal_t1", "ts0", "ts1"))
        moved = (r1 is not None and r0 is not None and r1 > r0)
        d = {"amount": a, "return_shape": sh, "detail": dt, "gas": tr.gas, "sender_before": h0, "sender_after": h1,
             "recipient_before": r0, "recipient_after": r1}
        tr_ok = tr.ok
        if not tr.ok:
            tr_reason = reason_of(tr)
            d["revert_class"], d["revert_class_detail"] = revert_class(tr_reason)
            add("transfer_return", "fail", f"reverts ({tr_reason})", d, script="transfer")
        elif sh == "returns_true" and moved:
            add("transfer_return", "pass", "returns_true", d, script="transfer")
        elif sh == "no_return" and moved:
            add("transfer_return", "quirk", "no_return_value", d, script="transfer")
        elif sh == "returns_false":
            add("transfer_return", "fail", "returns_false" + ("" if moved else "_and_moved_nothing"), d, script="transfer")
        elif not moved:
            add("transfer_return", "fail", f"{sh}_but_recipient_balance_unchanged", d, script="transfer")
        else:
            add("transfer_return", "quirk", sh + (":" + dt if dt else ""), d, script="transfer")
        # fee on transfer
        if not tr.ok:
            add("fee_on_transfer", "unprobeable", "normal_transfer_reverts", {"reason": tr_reason}, script="transfer")
        elif None in (h0, h1, r0, r1):
            add("fee_on_transfer", "error", "balance_reads_failed", d, script="transfer")
        else:
            sent, recv = h0 - h1, r1 - r0
            sdelta = (s1 - s0) if (s0 is not None and s1 is not None) else None
            tdelta = (t1 - t0) if (t0 is not None and t1 is not None) else None
            fd = {"amount": a, "sent": sent, "received": recv, "supply_delta": sdelta, "token_contract_delta": tdelta,
                  "fee_bps": (a - recv) * 10000 / a if a else None, "sender_extra": sent - a}
            RT = 2   # tolerance (base units) for share/index rounding
            if sent == a and recv == a and not sdelta:
                add("fee_on_transfer", "pass", "exact", fd, script="transfer")
            elif (abs(sent - a) <= RT and abs(recv - a) <= RT and abs(sdelta or 0) <= RT and abs(tdelta or 0) <= RT
                  and a > 1000):
                add("fee_on_transfer", "quirk", "rounding_up_to_2_base_units", fd, script="transfer")
            else:
                kinds = []
                if recv < a:
                    kinds.append("recipient_receives_less")
                if recv > a:
                    kinds.append("recipient_receives_more")
                if sent > a:
                    kinds.append("sender_debited_more_than_amount")
                if sent < a:
                    kinds.append("sender_debited_less_than_amount")
                if sdelta and abs(sdelta) > RT:
                    kinds.append("totalSupply_changed" + ("(burn)" if sdelta < 0 else "(mint)"))
                if tdelta and abs(tdelta) > RT:
                    kinds.append("token_contract_balance_increased(fee_collection)" if tdelta > 0 else "token_contract_balance_decreased")
                add("fee_on_transfer", "quirk", "+".join(kinds), fd, script="transfer")
    # insufficient balance
    if have("transfer_over"):
        s = S(ctx, "transfer_over", "tr")
        sh, dt = shape(s)
        h0, h1 = u(ctx, "transfer_over", "bal_h0"), u(ctx, "transfer_over", "bal_h1")
        d = {"amount": Bal + max(Bal // 100, 1) + 1, "balance": h0, "return_shape": sh, "detail": dt, "balance_after": h1}
        if sh == "reverts":
            add("transfer_insufficient", "pass", "reverts", {**d, "reason": dt}, script="transfer_over")
        elif sh == "returns_false":
            add("transfer_insufficient", "quirk", "returns_false_no_revert", d, script="transfer_over")
        else:
            add("transfer_insufficient", "fail", f"succeeds({sh})", d, script="transfer_over")
    if have("transfer_zero"):
        s = S(ctx, "transfer_zero", "tr")
        sh, dt = shape(s)
        if sh == "reverts":
            add("transfer_zero_value", "quirk" if tr_ok else "unprobeable", "reverts", {"reason": dt}, script="transfer_zero")
        else:
            add("transfer_zero_value", "quirk" if sh == "returns_false" else "pass", sh, {"return_shape": sh}, script="transfer_zero")
    if have("transfer_self"):
        s = S(ctx, "transfer_self", "tr")
        sh, dt = shape(s)
        h0, h1 = u(ctx, "transfer_self", "bal_h0"), u(ctx, "transfer_self", "bal_h1")
        s0, s1 = u(ctx, "transfer_self", "ts0"), u(ctx, "transfer_self", "ts1")
        d = {"amount": a, "balance_before": h0, "balance_after": h1, "supply_before": s0, "supply_after": s1, "return_shape": sh}
        if sh == "reverts":
            add("self_transfer", "quirk", "reverts", {**d, "reason": dt}, script="transfer_self")
        elif h0 is None or h1 is None:
            add("self_transfer", "error", "balance_reads_failed", d, script="transfer_self")
        elif h1 == h0 and s0 == s1:
            add("self_transfer", "pass", "balance_unchanged", d, script="transfer_self")
        else:
            add("self_transfer", "quirk", f"balance_delta={h1 - h0}" + ("" if s0 == s1 else f",supply_delta={s1 - s0}"),
                d, script="transfer_self")
    for name, test in (("transfer_zero_addr", "transfer_to_zero_address"), ("transfer_dead", "transfer_to_dead_address")):
        if have(name):
            s = S(ctx, name, "tr")
            sh, dt = shape(s)
            h0, h1 = u(ctx, name, "bal_h0"), u(ctx, name, "bal_h1")
            s0, s1 = u(ctx, name, "ts0"), u(ctx, name, "ts1")
            d = {"amount": a, "balance_before": h0, "balance_after": h1, "supply_before": s0, "supply_after": s1,
                 "return_shape": sh}
            if sh == "reverts":
                add(test, "pass" if name == "transfer_zero_addr" else "quirk", "reverts", {**d, "reason": dt}, script=name)
            else:
                burned = (s1 - s0) if (s0 is not None and s1 is not None) else None
                if name == "transfer_zero_addr":
                    add(test, "quirk", "allowed" + ("(burns:supply_decreased)" if burned and burned < -2 else "(tokens_lost:supply_unchanged)"),
                        d, script=name)
                else:
                    add(test, "pass", "allowed", d, script=name)
    if have("transfer_token"):
        s = S(ctx, "transfer_token", "tr")
        sh, dt = shape(s)
        d = {"amount": a, "return_shape": sh}
        if sh == "reverts":
            add("transfer_to_token_contract", "quirk", "reverts", {**d, "reason": dt}, script="transfer_token")
        else:
            add("transfer_to_token_contract", "pass", "allowed", d, script="transfer_token")
    # big amounts
    bits = ctx.funding.balance_bits
    if have("big_96"):
        s = S(ctx, "big_96", "tr")
        sh, dt = shape(s)
        h0, h1 = u(ctx, "big_96", "bal_h0"), u(ctx, "big_96", "bal_h1")
        d = {"funded_balance": Bal, "balance_bits_upper_bound": bits, "balance_bits_lower_bound": getattr(ctx.funding, "balance_bits_lo", None),
             "return_shape": sh, "detail": dt, "width_probes": ctx.fund_info.get("width_probes")}
        txt = (dt or "")
        by_reason = bool(re.search(r"96", txt))
        refused = sh == "reverts" or (sh == "returns_false" and h1 == h0)
        if not refused:
            add("large_uint96", "fail", f"transfer_of_2^96_succeeds_with_smaller_balance({sh})", d, script="big_96")
        elif by_reason or (bits is not None and bits <= 96):
            add("large_uint96", "quirk", "uint96_balances" + ("(reason mentions 96 bits)" if by_reason else ""), d, script="big_96")
        elif bits is not None and bits <= 128:
            add("large_uint96", "quirk", f"balance_field_at_most_{bits}_bits", d, script="big_96")
        else:
            add("large_uint96", "pass", "no_96_bit_limit_detected", d, script="big_96")
    if have("big_max"):
        s = S(ctx, "big_max", "tr")
        sh, dt = shape(s)
        h0, h1, r1 = u(ctx, "big_max", "bal_h0"), u(ctx, "big_max", "bal_h1"), u(ctx, "big_max", "bal_r1")
        d = {"funded_balance": h0, "balance_after": h1, "recipient_after": r1, "return_shape": sh}
        if sh == "reverts":
            add("max_uint_transfer", "pass", "reverts", {**d, "reason": dt}, script="big_max")
        elif sh == "returns_false" and h1 == h0:
            add("max_uint_transfer", "pass", "returns_false(no_revert)", d, script="big_max")
        elif h0 is not None and h1 == 0:
            add("max_uint_transfer", "quirk", "type(uint256).max_moves_entire_balance", d, script="big_max")
        else:
            add("max_uint_transfer", "fail", f"succeeds({sh})_balance_after={h1}", d, script="big_max")
    if have("big_all"):
        s = S(ctx, "big_all", "tr")
        sh, dt = shape(s)
        h0, h1 = u(ctx, "big_all", "bal_h0"), u(ctx, "big_all", "bal_h1")
        r0, r1 = u(ctx, "big_all", "bal_r0"), u(ctx, "big_all", "bal_r1")
        d = {"amount": Bal, "balance_before": h0, "balance_after": h1, "recipient_before": r0, "recipient_after": r1,
             "return_shape": sh, "detail": dt}
        if not tr_ok:
            add("full_balance_transfer", "unprobeable", "normal_transfer_reverts", {"reason": tr_reason}, script="big_all")
        elif sh == "reverts":
            add("full_balance_transfer", "quirk", "reverts(cannot_empty_account)", {**d, "reason": dt}, script="big_all")
        elif sh == "returns_false" and h1 == h0:
            add("full_balance_transfer", "quirk", "returns_false(no_revert)", d, script="big_all")
        elif h0 is None or h1 is None:
            add("full_balance_transfer", "error", "balance_reads_failed", d, script="big_all")
        elif h1 <= 1:
            add("full_balance_transfer", "pass", "empties_balance" + ("" if h1 == 0 else "(1_wei_dust)"), d, script="big_all")
        else:
            add("full_balance_transfer", "quirk", f"leaves_remainder({h1})", d, script="big_all")
    if have("big_frac"):
        s = S(ctx, "big_frac", "tr")
        sh, dt = shape(s)
        d = {"return_shape": sh, "detail": dt}
        if not tr_ok:
            add("max_tx_limit", "unprobeable", "normal_transfer_reverts", d, script="big_frac")
        elif sh == "reverts":
            add("max_tx_limit", "quirk", "large_transfer_reverts(5%_of_supply)", {**d, "reason": dt,
                "antibot_classes": antibot_class(dt)}, script="big_frac")
        else:
            add("max_tx_limit", "pass", "5%_of_supply_ok", d, script="big_frac")
    # ---- approve family --------------------------------------------------------------------------------------------
    ap_ok = False
    if have("approve_basic"):
        s = S(ctx, "approve_basic", "ap")
        sh, dt = shape(s)
        al = u(ctx, "approve_basic", "alw1")
        d = {"amount": a, "return_shape": sh, "allowance_after": al}
        ap_ok = s.ok
        if sh == "reverts":
            d["revert_class"], d["revert_class_detail"] = revert_class(dt)
            add("approve_return", "fail", f"reverts({dt})", d, script="approve_basic")
        elif sh == "returns_false":
            add("approve_return", "fail", "returns_false", d, script="approve_basic")
        elif al != a:
            add("approve_return", "quirk", f"{sh}_but_allowance_{al}_not_{a}", d, script="approve_basic")
        elif sh == "no_return":
            add("approve_return", "quirk", "no_return_value", d, script="approve_basic")
        elif sh == "returns_true":
            add("approve_return", "pass", "returns_true", d, script="approve_basic")
        else:
            add("approve_return", "quirk", sh, d, script="approve_basic")
    if have("approve_nznz"):
        s1, s2, s3 = (S(ctx, "approve_nznz", x) for x in ("ap1", "ap2", "ap3"))
        sh2, dt2 = shape(s2)
        d = {"first": shape(s1)[0], "second": sh2, "second_detail": dt2, "zero_reset": shape(s3)[0],
             "allowance_after_first": u(ctx, "approve_nznz", "alw1"), "allowance_after_second": u(ctx, "approve_nznz", "alw2")}
        if not s1.ok:
            add("approve_nonzero_to_nonzero", "unprobeable", "first_approve_reverts", d, script="approve_nznz")
        elif not s2.ok:
            add("approve_nonzero_to_nonzero", "quirk", "reverts(approve_race_protection:must_reset_to_0_first)", d, script="approve_nznz")
        else:
            add("approve_nonzero_to_nonzero", "pass", "allowed", d, script="approve_nznz")
    if have("approve_max"):
        s = S(ctx, "approve_max", "ap")
        sh, dt = shape(s)
        al = u(ctx, "approve_max", "alw1")
        d = {"return_shape": sh, "allowance_after": al, "detail": dt}
        if sh == "reverts":
            add("approve_max", "quirk", f"reverts({dt})", d, script="approve_max")
        elif al == UINT_MAX:
            add("approve_max", "pass", "uint256_max_ok", d, script="approve_max")
        elif al == TWO96 - 1:
            add("approve_max", "quirk", "capped_to_uint96_max", d, script="approve_max")
        else:
            add("approve_max", "quirk", f"allowance_after_max_approve={al}", d, script="approve_max")
    if have("approve_zero_addr"):
        s = S(ctx, "approve_zero_addr", "ap")
        sh, dt = shape(s)
        if sh == "reverts":
            add("approve_zero_address", "pass", "reverts", {"reason": dt}, script="approve_zero_addr")
        else:
            add("approve_zero_address", "quirk", "allowed", {"return_shape": sh, "allowance_after": u(ctx, "approve_zero_addr", "alw1")},
                script="approve_zero_addr")
    if have("approve_zero"):
        s = S(ctx, "approve_zero", "ap")
        sh, dt = shape(s)
        if sh == "reverts":
            add("approve_zero_value", "quirk", "reverts", {"reason": dt}, script="approve_zero")
        else:
            add("approve_zero_value", "pass", sh, {"return_shape": sh}, script="approve_zero")
    if have("inc_dec"):
        f = {}
        for lab, al_lab, exp in (("inc", "alw1", a), ("dec", "alw2", a - a // 2), ("inc_old", "alw3", a - a // 2 + a),
                                 ("dec_old", "alw4", (a - a // 2 + a) - a // 2)):
            s = S(ctx, "inc_dec", lab)
            al = u(ctx, "inc_dec", al_lab)
            f[lab] = {"ok": s.ok, "retlen": s.retlen, "allowance_after": al, "expected": exp}
        names = {"inc": "increaseAllowance", "dec": "decreaseAllowance", "inc_old": "increaseApproval", "dec_old": "decreaseApproval"}
        present_ = {names[k]: bool(x["ok"] and x["retlen"] in (0, 32) and x["allowance_after"] is not None) for k, x in f.items()}
        # a fallback-accepting contract returns ok with no effect; require the allowance to move
        eff = {}
        prev = 0
        for lab, nm in (("inc", "increaseAllowance"), ("dec", "decreaseAllowance"), ("inc_old", "increaseApproval"),
                        ("dec_old", "decreaseApproval")):
            al = f[lab]["allowance_after"]
            eff[nm] = bool(f[lab]["ok"] and al is not None and al != prev)
            prev = al if al is not None else prev
        bad = [n for n in eff if f[{v: k for k, v in names.items()}[n]]["ok"] and not eff[n]]
        add("allowance_helpers", "pass", {k: v for k, v in eff.items()}, {"calls": f, "inert_but_not_reverting": bad}, script="inc_dec")
    # ---- transferFrom family -----------------------------------------------------------------------------------------
    if have("tf_allow"):
        ap, tf = S(ctx, "tf_allow", "ap"), S(ctx, "tf_allow", "tf")
        sh, dt = shape(tf)
        al = u(ctx, "tf_allow", "alw1")
        r0, r1 = u(ctx, "tf_allow", "bal_r0"), u(ctx, "tf_allow", "bal_r1")
        moved = (r1 is not None and r0 is not None and r1 > r0)
        d = {"amount": a, "return_shape": sh, "detail": dt, "allowance_after": al, "approve_shape": shape(ap)[0]}
        if not ap.ok:
            add("transferFrom_return", "unprobeable", "approve_reverts", d, script="tf_allow")
            add("transferFrom_allowance_decrement", "unprobeable", "approve_reverts", d, script="tf_allow")
        else:
            if sh == "reverts":
                d["revert_class"], d["revert_class_detail"] = revert_class(dt)
                add("transferFrom_return", "fail", f"reverts({dt})", d, script="tf_allow")
            elif sh == "returns_false":
                add("transferFrom_return", "fail", "returns_false", d, script="tf_allow")
            elif not moved:
                add("transferFrom_return", "fail", f"{sh}_but_recipient_unchanged", d, script="tf_allow")
            elif sh == "returns_true":
                add("transferFrom_return", "pass", "returns_true", d, script="tf_allow")
            elif sh == "no_return":
                add("transferFrom_return", "quirk", "no_return_value", d, script="tf_allow")
            else:
                add("transferFrom_return", "quirk", sh, d, script="tf_allow")
            if not tf.ok:
                add("transferFrom_allowance_decrement", "unprobeable", "transferFrom_reverts", d, script="tf_allow")
            elif al == a:
                add("transferFrom_allowance_decrement", "pass", "decremented", d, script="tf_allow")
            elif al == 2 * a:
                add("transferFrom_allowance_decrement", "quirk", "not_decremented", d, script="tf_allow")
            else:
                add("transferFrom_allowance_decrement", "quirk", f"allowance_after={al}", d, script="tf_allow")
    if have("tf_max"):
        ap, tf = S(ctx, "tf_max", "ap"), S(ctx, "tf_max", "tf")
        sh, dt = shape(tf)
        al = u(ctx, "tf_max", "alw1")
        d = {"return_shape": sh, "detail": dt, "allowance_after": al, "approve_shape": shape(ap)[0],
             "allowance_after_approve_max_expected": UINT_MAX}
        if not ap.ok:
            add("transferFrom_infinite_allowance", "unprobeable", "approve_max_reverts", d, script="tf_max")
        elif not tf.ok:
            add("transferFrom_infinite_allowance", "fail", f"transferFrom_reverts_with_max_allowance({dt})", d, script="tf_max")
        elif al == UINT_MAX:
            add("transferFrom_infinite_allowance", "pass", "infinite_not_decremented", d, script="tf_max")
        elif al == UINT_MAX - a:
            add("transferFrom_infinite_allowance", "pass", "decremented_even_when_max", d, script="tf_max")
        elif al == TWO96 - 1:
            add("transferFrom_infinite_allowance", "pass", "infinite(uint96_max)_not_decremented", d, script="tf_max")
        else:
            add("transferFrom_infinite_allowance", "quirk", f"allowance_after={al}", d, script="tf_max")
    if have("tf_noallow"):
        tf = S(ctx, "tf_noallow", "tf")
        sh, dt = shape(tf)
        r0, r1 = u(ctx, "tf_noallow", "bal_r0"), u(ctx, "tf_noallow", "bal_r1")
        d = {"return_shape": sh, "detail": dt, "recipient_before": r0, "recipient_after": r1}
        if sh == "reverts":
            add("transferFrom_no_allowance", "pass", "reverts", {**d, "reason": dt}, script="tf_noallow")
        elif sh == "returns_false" and (r1 == r0):
            add("transferFrom_no_allowance", "quirk", "returns_false_no_revert", d, script="tf_noallow")
        else:
            add("transferFrom_no_allowance", "fail", f"spender_without_allowance_succeeds({sh})", d, script="tf_noallow")
    if have("tf_exceed"):
        tf = S(ctx, "tf_exceed", "tf")
        sh, dt = shape(tf)
        d = {"return_shape": sh, "detail": dt, "allowance_after": u(ctx, "tf_exceed", "alw1")}
        if sh == "reverts":
            add("transferFrom_exceed_allowance", "pass", "reverts", {**d, "reason": dt}, script="tf_exceed")
        elif sh == "returns_false":
            add("transferFrom_exceed_allowance", "quirk", "returns_false_no_revert", d, script="tf_exceed")
        else:
            add("transferFrom_exceed_allowance", "fail", f"over_allowance_succeeds({sh})", d, script="tf_exceed")
    if have("tf_self"):
        tf = S(ctx, "tf_self", "tf")
        sh, dt = shape(tf)
        r0, r1 = u(ctx, "tf_self", "bal_r0"), u(ctx, "tf_self", "bal_r1")
        d = {"return_shape": sh, "detail": dt, "recipient_before": r0, "recipient_after": r1}
        if sh == "reverts":
            add("transferFrom_by_owner", "pass", "owner_needs_allowance(reverts)", {**d, "reason": dt}, script="tf_self")
        elif r1 is not None and r0 is not None and r1 > r0:
            add("transferFrom_by_owner", "quirk", "owner_can_transferFrom_own_tokens_without_allowance", d, script="tf_self")
        else:
            add("transferFrom_by_owner", "quirk", f"{sh}_no_movement", d, script="tf_self")
    if have("tf_zero"):
        tf = S(ctx, "tf_zero", "tf")
        sh, dt = shape(tf)
        if sh == "reverts":
            add("transferFrom_zero_value", "quirk", "reverts", {"reason": dt}, script="tf_zero")
        else:
            add("transferFrom_zero_value", "quirk" if sh == "returns_false" else "pass", sh, {"return_shape": sh}, script="tf_zero")
    # ---- hooks / contract recipients ---------------------------------------------------------------------------------
    if have("hooks"):
        sc_ = u(ctx, "hooks", "sink_calls")
        hc = u(ctx, "hooks", "h_calls")
        tr = S(ctx, "hooks", "tr")
        sh, dt = shape(tr)
        sels = u(ctx, "hooks", "sink_sels")
        hsels = u(ctx, "hooks", "h_sels")
        dec_sels = lambda x: [hex((x >> (32 * i)) & 0xFFFFFFFF) for i in range(8) if (x >> (32 * i)) & 0xFFFFFFFF] if x else []
        bal_at = u(ctx, "hooks", "sink_bal_at_call")
        hbal = u(ctx, "hooks", "h_bal_at_call")
        h0 = u(ctx, "hooks", "bal_h0")
        s0, s1 = u(ctx, "hooks", "bal_s0"), u(ctx, "hooks", "bal_s1")
        d = {"transfer_shape": sh, "recipient_hook_calls": sc_, "recipient_hook_selectors": dec_sels(sels),
             "sender_hook_calls": hc, "sender_hook_selectors": dec_sels(hsels),
             "recipient_balance_seen_in_callback": bal_at, "recipient_balance_before": s0, "recipient_balance_after": s1,
             "sender_balance_seen_in_callback": hbal, "sender_balance_before": h0,
             "erc777_static": ctx.erc777_static, "registered_as_erc777_in_1820": ctx.erc777_registered,
             "registry_present": ctx.erc1820_registry_present}
        if not ctx.erc1820_registry_present:
            add("erc777_hooks", "unprobeable", "erc1820_registry_absent_on_this_chain", d, script="hooks")
        elif (sc_ or 0) > 0 or (hc or 0) > 0 or ctx.erc777_registered or any(ctx.erc777_static.values()):
            how = []
            if (sc_ or 0) > 0:
                how.append("recipient_hook_called")
            if (hc or 0) > 0:
                how.append("sender_hook_called")
            if ctx.erc777_registered:
                how.append("token_registered_ERC777Token_in_1820")
            if any(ctx.erc777_static.values()):
                how.append("erc777_accessors")
            if hc and hbal is not None and h0 is not None and hbal == h0:
                how.append("sender_hook_runs_before_balance_update(reentrancy_window)")
            add("erc777_hooks", "quirk", "+".join(how), d, script="hooks")
        elif not tr.ok:
            add("erc777_hooks", "unprobeable", "transfer_reverts_in_hook_script", d, script="hooks")
        else:
            add("erc777_hooks", "pass", "no_hook_calls_observed", d, script="hooks")
    if have("to_sink") and have("to_reject"):
        ts_ = S(ctx, "to_sink", "tr")
        tr_ = S(ctx, "to_reject", "tr")
        sh1, dt1 = shape(ts_)
        sh2, dt2 = shape(tr_)
        calls = u(ctx, "to_sink", "sink_calls")
        sels = u(ctx, "to_sink", "sink_sels")
        d = {"to_accepting_contract": sh1, "to_accepting_detail": dt1, "to_rejecting_contract": sh2, "to_rejecting_detail": dt2,
             "callbacks_on_recipient": calls,
             "callback_selectors": [hex((sels >> (32 * i)) & 0xFFFFFFFF) for i in range(8) if (sels >> (32 * i)) & 0xFFFFFFFF] if sels else [],
             "recipient_balance_seen_in_callback": u(ctx, "to_sink", "sink_bal_at_call")}
        notes = []
        if not ts_.ok:
            notes.append("reverts_to_plain_contract_recipient")
        if (calls or 0) > 0:
            notes.append("calls_back_into_recipient_contract")
        if not tr_.ok:
            notes.append("reverts_when_recipient_contract_rejects_calls")
        if notes:
            add("contract_recipient", "quirk" if tr_ok or not ts_.ok else "quirk", "+".join(notes), d, script="to_sink")
        else:
            add("contract_recipient", "pass", "contract_recipients_ok", d, script="to_sink")
    # ---- gas, same-block repeats, EOA vs contract, anti-bot ------------------------------------------------------------
    if "transfer" in res:
        g1 = S(ctx, "transfer", "tr").gas
        add("gas_transfer", "quirk" if g1 > 120_000 else "pass", g1,
            {"gas": g1, "threshold_for_quirk": 120_000, "note": "first transfer: cold storage slots (recipient new), measured "
             "inside the harness call incl. ~2.6k call overhead"}, script="transfer")
    if have("transfer_twice"):
        t1, t2, t3 = (S(ctx, "transfer_twice", x) for x in ("tr1", "tr2", "tr3"))
        d = {"first": shape(t1), "second_same_recipient": shape(t2), "third_new_recipient": shape(t3)}
        if t1.ok and not t2.ok:
            add("same_block_repeat", "quirk", "second_transfer_in_same_block_reverts(cooldown/limit)", d, script="transfer_twice")
        elif t1.ok and not t3.ok:
            add("same_block_repeat", "quirk", "third_transfer_to_new_recipient_reverts", d, script="transfer_twice")
        elif not t1.ok:
            add("same_block_repeat", "unprobeable", "first_transfer_reverts", d, script="transfer_twice")
        else:
            add("same_block_repeat", "pass", "repeat_transfers_ok", d, script="transfer_twice")
    # EOA mode
    eoa = {}
    for lab, r in getattr(ctx, "eoa_resp", {}).items():
        eoa[lab] = _eoa_shape(r)
    if eoa and "transfer" in res:
        c_shape = shape(S(ctx, "transfer", "tr"))[0]
        e_shape = eoa.get("eoa_transfer", ("error", ""))
        cids = [r["_cid"] for r in getattr(ctx, "eoa_resp", {}).values() if r.get("_cid", -1) >= 0 and "eoa" in rec.calls[r["_cid"]]["tag"]]
        shas = [rec.calls[c]["response_sha256"] for c in cids]
        d = {"eoa": {k: list(v) for k, v in eoa.items()}, "contract_transfer": c_shape}
        if e_shape[0] == "error":
            add("eoa_vs_contract", "error", e_shape[1], d, calls=cids, shas=shas)
        else:
            e_ok = e_shape[0] != "reverts"
            c_ok = c_shape != "reverts"
            if e_ok == c_ok:
                bf = shape(S(ctx, "big_frac", "tr"))[0] if "big_frac" in res else None
                ebf = eoa.get("eoa_big_frac", ("error", ""))[0]
                if bf is not None and ebf != "error" and (bf != "reverts") != (ebf != "reverts"):
                    add("eoa_vs_contract", "quirk", "large_transfer_differs_between_EOA_and_contract_sender", d, calls=cids, shas=shas)
                else:
                    add("eoa_vs_contract", "pass", "same_outcome", d, calls=cids, shas=shas)
            else:
                add("eoa_vs_contract", "quirk", "EOA_sender_" + ("ok" if e_ok else "reverts") + "_but_contract_sender_" +
                    ("ok" if c_ok else "reverts"), d, calls=cids, shas=shas)
    # anti-bot / limits: reasons from every normal-path script + accessors
    reasons = {}
    for name in ("transfer", "big_frac", "transfer_twice", "to_sink", "approve_basic", "tf_allow", "transfer_zero"):
        for lab, st in res.get(name, {}).items():
            if lab in ("tr", "tr1", "tr2", "tr3", "ap", "tf") and st is not None and not st.ok:
                reasons[f"{name}.{lab}"] = reason_of(st)
    classes = {}
    for k, t in reasons.items():
        c = antibot_class(t)
        if c:
            classes[k] = c
    lim_words = ("maxtx", "maxtrans", "maxwallet", "trading", "launched", "cooldown", "antibot", "limits", "delay", "swapenabled")
    lim_acc = {k: v for k, v in ctx.fl_acc.items() if any(w in k.lower() for w in lim_words)}
    fee_acc = {k: v for k, v in ctx.fl_acc.items() if k not in lim_acc}
    d = {"revert_reasons": reasons, "reason_classes": classes, "limit_accessors": lim_acc}
    strong_lim = {k: v for k, v in lim_acc.items() if k != "swapEnabled"}
    if classes or strong_lim:
        kinds = sorted({c for v in classes.values() for c in v})
        add("anti_bot_limits", "quirk", {"classes": kinds, "accessors": sorted(strong_lim)}, d, script="views")
    elif reasons:
        add("anti_bot_limits", "pass", "reverts_present_but_no_antibot_keywords", d, script="views")
    else:
        add("anti_bot_limits", "pass", "none_observed", d, script="views")


def _eoa_shape(r):
    if "result" in r:
        raw = r["result"]
        if raw in ("0x", None):
            return ("no_return", "")
        b = unhex(raw)
        if len(b) == 32:
            v = int.from_bytes(b, "big")
            return ("returns_true" if v == 1 else "returns_false" if v == 0 else "returns_other", "")
        return ("returns_other", f"len={len(b)}")
    err = r.get("error", {})
    if r.get("_transport_error"):
        return ("error", err.get("message", ""))
    data = err.get("data")
    msg = err.get("message", "")
    if isinstance(data, str) and data.startswith("0x"):
        k, t = revert_info(unhex(data))
        return ("reverts", f"{k}:{t}" if t else k)
    if "revert" in msg.lower() or "execution" in msg.lower():
        return ("reverts", msg[:160])
    return ("error", msg[:160])


def _drift_and_rebasing(ctx, rec, R_):
    fnd = ctx.funding
    if fnd is not None and fnd.method == "real_holder":
        fnd = None     # no slot -> no calibration / drift evidence
    strong = {"sharesOf", "scaledBalanceOf", "gonsPerFragment", "_gonsPerFragment", "getSharesByPooledEth",
              "getPooledEthByShares", "_rTotal", "tokenFromReflection", "TOTAL_GONS", "_totalGons", "scaledTotalSupply",
              "getScaledUserBalanceAndSupply"}
    acc_strong = {k: v for k, v in ctx.rb_acc.items() if k in strong}
    acc_weak = {k: v for k, v in ctx.rb_acc.items() if k not in strong}
    ev = []
    calls = []
    detail = {"accessors_strong": acc_strong, "accessors_weak": acc_weak}
    drift_status = "not_run"
    if fnd is not None:
        detail["slot_to_balance_ratio"] = fnd.ratio
        detail["identity_map"] = fnd.identity
        if not fnd.identity:
            ev.append(f"balance_is_not_the_raw_slot_value(balance/slot={fnd.ratio:.6g}: shares, gons, reflection or index-scaled)")
    dr = getattr(ctx, "drift_resp", {})
    if dr and fnd is not None:
        old = dr.get("drift_balanceOf_old")
        ots = dr.get("drift_totalSupply_old")
        if old is None or old.get("_transport_error") or "error" in old:
            drift_status = "unprobeable:" + ((old or {}).get("error", {}).get("message", "no response")[:120] if old else "no response")
        else:
            bo = parse_u256(old)
            to_ = parse_u256(ots) if ots else None
            calls = [c["_cid"] for c in dr.values() if c.get("_cid", -1) >= 0]
            detail["old_block"] = ctx.drift_block
            detail["balance_at_old_block_same_slot_value"] = bo
            detail["balance_at_pinned_block_same_slot_value"] = fnd.balance
            detail["totalSupply_old"] = to_
            detail["totalSupply_now"] = ctx.ts_views
            if bo is None or bo == 0:
                drift_status = "unprobeable:token_not_deployed_or_balanceOf_failed_at_old_block"
            elif bo != fnd.balance:
                drift_status = "drift"
                ev.append(f"same_raw_slot_value_gives_different_balance_7d_apart({bo}->{fnd.balance})")
            else:
                drift_status = "no_drift"
    elif fnd is not None and not dr:
        drift_status = "not_run(no_archive_endpoint_or_disabled)"
    detail["drift_status"] = drift_status
    if acc_strong:
        ev.append("share/gons/reflection/scaled accessors present: " + ",".join(sorted(acc_strong)))
    if fnd is None:
        if acc_strong:
            R_.add("rebasing", "quirk", "accessor_evidence_only(" + ",".join(sorted(acc_strong)) + ")", detail, script="views")
        else:
            R_.add("rebasing", "unprobeable", "no_balance_slot(cannot_calibrate_or_measure_drift)", detail, script="views")
        return
    cids = [c for c in calls] + [x for x in (fnd.cids or []) if x is not None]
    shas = [rec.calls[c]["response_sha256"] for c in cids]
    if ev:
        R_.add("rebasing", "quirk", {"evidence": ev, "drift": drift_status}, detail, calls=cids, shas=shas)
    elif drift_status == "no_drift" or drift_status.startswith("not_run") or drift_status.startswith("unprobeable"):
        R_.add("rebasing", "pass", {"evidence": "slot_identity(balance==raw slot value)" + ("; no 7d drift" if drift_status == "no_drift" else ""),
                                    "drift": drift_status}, detail, calls=cids, shas=shas)

