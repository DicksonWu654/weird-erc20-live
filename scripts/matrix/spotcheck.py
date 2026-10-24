#!/usr/bin/env python3
"""Hand-style spot-checks of matrix cells against the raw evidence (A1, "verify before believing").

For each listed feature: sample cells (all 'yes' cells if few, else a chain-stratified sample, plus 'no' cells), re-derive the answer from
the saved raw JSON-RPC responses using the independent decoder in rawdec.py (NOT W1's classifier), compare with the matrix cell and
write research/findings/tier1/spotchecks.csv (one row per checked cell, with the decoded numbers).

    .venv/bin/python scripts/matrix/spotcheck.py [--n-yes 10] [--n-no 4] [--features a,b,c] [--seed 7]
"""
import argparse
import csv
import json
import pathlib
import random
import sys

import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import rawdec as R  # noqa: E402

OUT = ROOT / "research" / "findings" / "tier1" / "spotchecks.csv"
MAXU = (1 << 256) - 1
DAI_PERMIT_TYPEHASH = 0xea2aa0a1be11a07ed86d755c93467f4f82362b452371d1ba94d1715123511acb


# ----------------------------------------------------------------------------------------------- checkers
# each returns (hand: 'yes'|'no'|'na', note, where)
def get(chain, token):
    raw = R.load_raw(chain, token)
    sc, cid = R.scripts(raw)
    return raw, (R.S(sc) if sc else None), cid


def need(s):
    if s is None:
        raise LookupError("battery_not_decodable(holder-mode or missing)")


def ck_ret_missing_transfer(chain, token):
    raw, s, cid = get(chain, token); need(s)
    st = s["transfer", "tr"]
    return ("yes" if R.shape(st) == "no_return" else "no"), f"transfer shape={R.shape(st)} ok={st[0]} retlen={st[2]}", f"battery cid{cid} script=transfer op=tr"


def ck_ret_missing_approve(chain, token):
    raw, s, cid = get(chain, token); need(s)
    st = s["approve_basic", "ap"]
    return ("yes" if R.shape(st) == "no_return" else "no"), f"approve shape={R.shape(st)}", f"cid{cid} approve_basic.ap"


def ck_false_on_failure(chain, token):
    raw, s, cid = get(chain, token); need(s)
    a = R.shape(s["transfer_over", "tr"]); b = R.shape(s["tf_noallow", "tf"]); c = R.shape(s["tf_exceed", "tf"])
    hit = any(x == "false" for x in (a, b, c))
    return ("yes" if hit else "no"), f"transfer_over={a} tf_noallow={b} tf_exceed={c}", f"cid{cid}"


def ck_not_credited(chain, token):
    raw, s, cid = get(chain, token); need(s)
    tr = s["transfer", "tr"]
    r0, r1 = R.u(s["transfer", "bal_r0"]), R.u(s["transfer", "bal_r1"])
    h0, h1 = R.u(s["transfer", "bal_h0"]), R.u(s["transfer", "bal_h1"])
    if not tr[0]:
        return "na", f"baseline transfer reverts {R.revert_text(tr)}", f"cid{cid}"
    return ("yes" if r1 <= r0 else "no"), f"shape={R.shape(tr)} sender {h0}->{h1} recipient {r0}->{r1}", f"cid{cid} transfer"


def ck_fresh_nonzero(chain, token):
    raw, s, cid = get(chain, token); need(s)
    r0 = R.u(s["transfer", "bal_r0"]); r00 = R.u(s["transfer_over", "bal_r1"])
    return ("yes" if (r0 or 0) > 0 else "no"), f"balanceOf(R) before transfer = {r0}", f"cid{cid} transfer.bal_r0 (R = fake address, never used)"


def ck_permissioned(chain, token):
    raw, s, cid = get(chain, token); need(s)
    tr = s["transfer", "tr"]
    txt = R.revert_text(tr) if not tr[0] else "ok"
    return ("yes" if not tr[0] else "no"), f"transfer reverts: {txt}", f"cid{cid} transfer.tr"


def ck_fee(chain, token):
    raw, s, cid = get(chain, token); need(s)
    h0, h1, r0, r1 = (R.u(s["transfer", k]) for k in ("bal_h0", "bal_h1", "bal_r0", "bal_r1"))
    if None in (h0, h1, r0, r1):
        return "na", "balances not readable", f"cid{cid}"
    debit, credit = h0 - h1, r1 - r0
    hit = credit < debit - 2 or credit < 0 or debit < credit - 2
    return ("yes" if hit else "no"), f"sender debit={debit} recipient credit={credit} diff={debit - credit}", f"cid{cid} transfer"


def request_amount(raw):
    """amount of the first transfer(R, amount) in the battery request calldata (independent of W1's rows)"""
    for c in raw["calls"]:
        if c["tag"].startswith("battery"):
            try:
                data = c["request"]["params"][0]["data"] if c["request"]["method"] == "eth_call" else c["request"]["params"][0]["blockStateCalls"][0]["calls"][-1]["input"]
            except Exception:
                return None
            data = data.lower()
            key = "a9059cbb" + "0" * 24 + R.R_ADDR
            k = data.find(key)
            if k < 0:
                return None
            return int(data[k + len(key):k + len(key) + 64], 16)
    return None


def ck_rounding(chain, token):
    raw, s, cid = get(chain, token); need(s)
    h0, h1, r0, r1 = (R.u(s["transfer", k]) for k in ("bal_h0", "bal_h1", "bal_r0", "bal_r1"))
    amt = request_amount(raw)
    debit, credit = h0 - h1, r1 - r0
    hit = amt is not None and (0 < abs(debit - amt) <= 2 or 0 < abs(credit - amt) <= 2) and abs(debit - credit) <= 2
    return ("yes" if hit else "no"), f"requested={amt} debit={debit} credit={credit}", f"cid{cid} transfer; amount from request calldata"


def ck_race(chain, token):
    raw, s, cid = get(chain, token); need(s)
    a1, a2 = R.shape(s["approve_nznz", "ap1"]), R.shape(s["approve_nznz", "ap2"])
    return ("yes" if a1 != "reverts" and a2 == "reverts" else "no"), f"approve#1={a1} approve(nonzero->nonzero)={a2}", f"cid{cid} approve_nznz"


def ck_approve_zero_addr(chain, token):
    raw, s, cid = get(chain, token); need(s)
    a = s["approve_zero_addr", "ap"]
    return ("yes" if not a[0] else "no"), f"approve(0x0) shape={R.shape(a)} {R.revert_text(a)}", f"cid{cid} approve_zero_addr"


def ck_approve_zero_value(chain, token):
    raw, s, cid = get(chain, token); need(s)
    a = s["approve_zero", "ap"]
    return ("yes" if not a[0] else "no"), f"approve(0) shape={R.shape(a)} {R.revert_text(a)}", f"cid{cid} approve_zero"


def ck_transfer_zero(chain, token):
    raw, s, cid = get(chain, token); need(s)
    a = s["transfer_zero", "tr"]
    return ("yes" if not a[0] else "no"), f"transfer(0) shape={R.shape(a)} {R.revert_text(a)}", f"cid{cid} transfer_zero"


def ck_to_zero(chain, token):
    raw, s, cid = get(chain, token); need(s)
    a = s["transfer_zero_addr", "tr"]
    return ("yes" if not a[0] else "no"), f"transfer(0x0) shape={R.shape(a)} {R.revert_text(a)}", f"cid{cid} transfer_zero_addr"


def ck_tf_owner(chain, token):
    raw, s, cid = get(chain, token); need(s)
    a = s["tf_self", "tf"]
    r0, r1 = R.u(s["tf_self", "bal_r0"]), R.u(s["tf_self", "bal_r1"])
    moved = a[0] and r1 is not None and r0 is not None and r1 > r0
    return ("yes" if moved else "no"), f"owner transferFrom(self,..) shape={R.shape(a)} recipient {r0}->{r1}", f"cid{cid} tf_self"


def ck_max_decrements(chain, token):
    raw, s, cid = get(chain, token); need(s)
    tf = s["tf_max", "tf"]; alw = R.u(s["tf_max", "alw1"])
    if not tf[0]:
        return "na", f"transferFrom reverts {R.revert_text(tf)}", f"cid{cid} tf_max"
    dec = alw is not None and alw < MAXU and alw != (1 << 96) - 1
    return ("yes" if dec else "no"), f"allowance after spending from max approval = {alw} (max={MAXU})", f"cid{cid} tf_max"


def ck_uint96(chain, token):
    raw, s, cid = get(chain, token)
    bad, good = [], []
    for c in raw["calls"]:
        if c["tag"] in ("fund_probe", "fund_verify") and isinstance(c["response"].get("result"), str):
            pr = _override_pair(c)
            if not pr:
                continue
            slot, v = pr
            b = R.hexint(c["response"]["result"])
            if v >= (1 << 96) and bin(v).count("1") == 1:
                (good if b == v else bad).append((v.bit_length() - 1, c["cid"]))
    text = ""
    if s:
        st = s["big_96", "tr"]
        text = R.revert_text(st)
    text_hit = text.startswith("Error(") and "96" in text
    narrow = any(k <= 96 for k, _ in bad) if bad else False
    if not bad and not good and not text:
        return "na", "no evidence", ""
    return ("yes" if text_hit or narrow else "no"), f"big_96 revert='{text}'; width probes >=2^96: exact={sorted(good)[:3]} changed={sorted(bad)[:3]}", f"cid{cid} big_96 + raw fund probes"


def raw_decimals(raw):
    cs = R.call_by_tag(raw, "decimals")
    for c in cs:
        v = R.hexint(c["response"].get("result"))
        if v is not None:
            return v
    return None


def ck_uint128(chain, token):
    raw, s, cid = get(chain, token)
    bad, good = [], []
    for c in raw["calls"]:
        if c["tag"] in ("fund_probe", "fund_verify") and isinstance(c["response"].get("result"), str):
            pr = _override_pair(c)
            if pr and pr[1] >= (1 << 96) and bin(pr[1]).count("1") == 1:
                b = R.hexint(c["response"]["result"])
                (good if b == pr[1] else bad).append((pr[1].bit_length() - 1, c["cid"]))
    narrow = bool(bad) and min(k for k, _ in bad) <= 128 and not any(k <= 96 for k, _ in bad)
    return ("yes" if narrow else "no"), f"width probes exact={sorted(good)[:3]} changed={sorted(bad)[:3]}", "raw fund probes (value 2^k override vs readback)"


def ck_decimals_low(chain, token):
    raw = R.load_raw(chain, token); d = raw_decimals(raw)
    return ("yes" if d is not None and d <= 5 else "no"), f"decimals()={d}", "raw call tag=decimals"


def ck_decimals_high(chain, token):
    raw = R.load_raw(chain, token); d = raw_decimals(raw)
    return ("yes" if d is not None and d > 18 else "no"), f"decimals()={d}", "raw call tag=decimals"


def ck_dai_permit(chain, token):
    raw, s, cid = get(chain, token); need(s)
    v = R.u(s["views", "v:PERMIT_TYPEHASH()"])
    return ("yes" if v == DAI_PERMIT_TYPEHASH else "no"), f"PERMIT_TYPEHASH()={hex(v) if v else v}", f"cid{cid} views v:PERMIT_TYPEHASH()"


def ck_pause_cap(chain, token):
    raw, s, cid = get(chain, token)
    sels = R.code_selectors(raw)
    names = ["pause()", "unpause()", "paused()", "setPaused(bool)", "pauseTransfers()", "togglePause()", "emergencyStop()", "stop()"]
    found = [n for n in names if R.sel(n) in sels]
    acc = []
    if s:
        for a in ("paused()", "isPaused()", "stopped()", "isStopped()", "isTransferPaused()", "transfersPaused()", "transferPaused()", "tradingPaused()", "pausedTransfers()"):
            v = R.u(s["views", "pause:" + a])
            if v is not None:
                acc.append(f"{a}={v}")
    return ("yes" if found or acc else "no"), f"PUSH4 in code/impl: {found}; answering accessors: {acc}", "raw code+impl_code PUSH4 scan; views script"


def ck_paused_now(chain, token):
    raw, s, cid = get(chain, token); need(s)
    acc = {}
    for a in ("paused()", "isPaused()", "stopped()", "isStopped()", "isTransferPaused()", "transfersPaused()", "transferPaused()", "tradingPaused()", "pausedTransfers()", "pausedPublic()", "pausedOwnerAdmin()", "emergencyStop()"):
        v = R.u(s["views", "pause:" + a])
        if v is not None:
            acc[a] = v
    tr = s["transfer", "tr"]
    return ("yes" if any(v == 1 for v in acc.values()) else "no"), f"accessors={acc}; plain transfer {R.shape(tr)} {R.revert_text(tr)}", f"cid{cid} views"


BL_NAMES = ["blacklist(address)", "unBlacklist(address)", "isBlacklisted(address)", "isBlackListed(address)", "addBlackList(address)", "removeBlackList(address)",
            "addToBlacklist(address)", "removeFromBlacklist(address)", "freeze(address)", "unfreeze(address)", "isFrozen(address)", "freezeAccount(address,bool)",
            "isBlocked(address)", "blockAddress(address)", "blockUser(address)", "blockAccount(address)", "denylist(address)", "blocklist(address)", "ban(address)",
            "isBanned(address)", "destroyBlackFunds(address)", "wipeFrozenAddress(address)", "getBlackListStatus(address)", "isBlocklisted(address)", "setBlacklisted(address,bool)",
            "setBlacklist(address,bool)", "addBlacklist(address)", "removeBlacklist(address)", "blackList(address)", "unblacklist(address)", "updateBlacklist(address,bool)", "addToDenylist(address)", "frozen(address)"]


def ck_blocklist_cap(chain, token):
    raw, s, cid = get(chain, token)
    sels = R.code_selectors(raw)
    found = [n for n in BL_NAMES if R.sel(n) in sels]
    ans = []
    if s:
        for f in R.BL_FUNCS:
            if f in ("isBot(address)", "bots(address)", "_isBot(address)"):
                continue
            v = R.u(s["views", "bl_H:" + f])
            if v is not None:
                ans.append(f)
    return ("yes" if found or ans else "no"), f"blocklist selectors in code: {found[:5]}; answering views: {ans}", "raw code PUSH4 + views"


def ck_sanction_ronin(chain, token):
    raw, s, cid = get(chain, token); need(s)
    tr = s["transfer", "tr"]; a = s["sanctioned_1", "tr"]
    if not tr[0]:
        return "na", "baseline transfer reverts", f"cid{cid}"
    return ("yes" if not a[0] else "no"), f"to 0x098B..2f96: {R.shape(a)} {R.revert_text(a)}", f"cid{cid} sanctioned_1.tr"


def ck_sanction_tornado(chain, token):
    raw, s, cid = get(chain, token); need(s)
    tr = s["transfer", "tr"]; a = s["sanctioned_0", "tr"]
    if not tr[0]:
        return "na", "baseline transfer reverts", f"cid{cid}"
    return ("yes" if not a[0] else "no"), f"to Tornado router: {R.shape(a)} {R.revert_text(a)}", f"cid{cid} sanctioned_0.tr"


def ck_blocklist_any(chain, token):
    h1, n1, w1 = ck_blocklist_cap(chain, token)
    try:
        h2, n2, _ = ck_sanction_ronin(chain, token)
        h3, n3, _ = ck_sanction_tornado(chain, token)
    except LookupError:
        h2 = h3 = "na"; n2 = n3 = ""
    yes = "yes" in (h1, h2, h3)
    return ("yes" if yes else "no"), f"cap:{h1} ronin:{h2} tornado:{h3} | {n2}", w1


def _override_pair(c):
    try:
        p = c["request"]["params"]
        ov = p[2] if len(p) > 2 else p[-1]
        (addr, o), = ov.items()
        (slot, val), = o["stateDiff"].items()
        return slot, int(val, 16)
    except Exception:
        return None


def ck_share_ratio(chain, token):
    raw = R.load_raw(chain, token)
    ratios = []
    for c in raw["calls"]:
        if c["tag"] in ("fund_probe", "fund_verify") and isinstance(c["response"].get("result"), str):
            pr = _override_pair(c)
            if not pr:
                continue
            slot, v = pr
            b = R.hexint(c["response"]["result"])
            if b and v:
                ratios.append((b / v, c["cid"]))
    if not ratios:
        return "na", "no usable override/response pairs", "raw fund_probe/fund_verify"
    ok = [r for r in ratios if r[0] > 0]
    rs = sorted(r[0] for r in ok)
    med = rs[len(rs) // 2] if rs else None
    if med is None:
        return "na", "no nonzero readback", "raw"
    hit = med is not None and abs(med - 1) > 1e-4
    return ("yes" if hit else "no"), f"median balance/slot ratio over {len(rs)} override probes = {med:.6g}", "raw fund_probe/fund_verify (cids " + ",".join(str(c) for _, c in ok[:3]) + ")"


def ck_drift(chain, token):
    raw = R.load_raw(chain, token)
    old = [c for c in raw["calls"] if c["tag"] == "drift_balanceOf_old"]
    if not old:
        return "na", "no drift call saved", "raw"
    pr = _override_pair(old[0])
    if not pr:
        return "na", "cannot parse override", "raw"
    slot, v = pr
    b_old = R.hexint(old[0]["response"].get("result"))
    b_now = None
    for c in raw["calls"]:
        if c["tag"] in ("fund_probe", "fund_verify") and isinstance(c["response"].get("result"), str):
            p2 = _override_pair(c)
            if p2 and p2 == (slot, v):
                b_now = R.hexint(c["response"]["result"]); break
    if b_old is None or b_now is None:
        return "na", f"old balance {b_old}; no 'now' call with same slot value", "raw"
    return ("yes" if b_old != b_now else "no"), f"same slot value {v}: balance old-block={b_old} pinned-block={b_now}", f"drift_balanceOf_old cid{old[0]['cid']}"


def ck_hooks(chain, token):
    raw, s, cid = get(chain, token); need(s)
    sc = R.u(s["hooks", "sink_calls"]); hc = R.u(s["hooks", "h_calls"])
    return ("yes" if (sc or 0) > 0 or (hc or 0) > 0 else "no"), f"callbacks on contract recipient={sc}, on registered hook account={hc}", f"cid{cid} hooks"


def ck_flash(chain, token):
    raw = R.load_raw(chain, token)
    sels = R.code_selectors(raw)
    hit = [n for n in ("flashLoan(address,address,uint256,bytes)", "maxFlashLoan(address)", "flashFee(address,uint256)") if R.sel(n) in sels]
    return ("yes" if hit[:2] and any(x.startswith(("flashLoan", "maxFlash")) for x in hit) else "no"), f"selectors in code/impl: {hit}", "raw code PUSH4"


def ck_nonstring(chain, token):
    raw, s, cid = get(chain, token)
    out = []
    for k in ("name", "symbol"):
        st = s["views", k] if s else None
        if st and st[0]:
            ln = st[2]
            out.append(f"{k}: retlen={ln}")
        else:
            out.append(f"{k}: reverts")
    return ("yes" if any("retlen=32" in o or "reverts" in o for o in out) else "no"), "; ".join(out), f"cid{cid} views"


def ck_tf_missing(chain, token):
    raw, s, cid = get(chain, token); need(s)
    st = s["tf_allow", "tf"]
    return ("yes" if R.shape(st) == "no_return" else "no"), f"transferFrom shape={R.shape(st)}", f"cid{cid} tf_allow"


# ------------------------------------------------------------------ control (W3) checkers: verify every hop in raw evidence
SLOT_IMPL = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
SLOT_ADMIN = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
SLOT_BEACON = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
SLOT_ZOS_IMPL = "0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3"


def ctl_row(chain, token):
    for l in open(ROOT / "data" / "control" / chain / "control.jsonl"):
        if token in l[:200]:
            r = json.loads(l)
            if r["token"].lower() == token:
                return r
    return None


def ev_lines(chain, name):
    p = ROOT / "data" / "control" / chain / "evidence" / name
    if not p.exists():
        return []
    return [json.loads(l) for l in open(p)]


def words_addr(resp):
    r = resp.get("result") if isinstance(resp, dict) else None
    if isinstance(r, str) and len(r) >= 66:
        return "0x" + r[-40:]
    return None


def ck_is_proxy(chain, token):
    raw = R.load_raw(chain, token)
    hits = []
    for c in raw["calls"]:
        if c["tag"] in ("slot_eip1967_impl", "slot_eip1967_beacon", "slot_zos_impl"):
            v = R.hexint(c["response"].get("result"))
            if v:
                hits.append(f"{c['tag']}=0x{v:040x}"[:60])
    for c in R.call_by_tag(raw, "implementation()"):
        v = R.hexint(c["response"].get("result"))
        if v and v > 0x100 and v < (1 << 160):
            hits.append(f"implementation()=0x{v:040x}"[:48])
    code = ""
    for c in R.call_by_tag(raw, "code"):
        code = c["response"].get("result") or ""
    if code.startswith("0x363d3d373d3d3d363d73") or code.startswith("0x3d602d80600a3d3981f3363d3d373d3d3d363d73"):
        hits.append("eip1167_minimal_proxy_prefix")
    delegate = "f4" in code  # crude: 0xf4 byte anywhere (over-approx.)
    if not hits and delegate:
        return "na", "no standard slot/1167 prefix, but DELEGATECALL byte present: pattern (diamond/custom) not hand-verifiable here", "probe raw slot_* calls + code"
    return ("yes" if hits else "no"), f"probe raw: {hits or 'no implementation/beacon slot set, no 1167 prefix'}; DELEGATECALL byte present={delegate}", "probe raw slot_* calls + code"


def _paths(row, key):
    return [p for p in (row.get(key) or []) if not p.get("assumed_or_unverified")]


def verify_path(chain, token, p, need_eoa=True):
    """Verify every hop of a W3 path against the raw evidence. returns (ok, note)."""
    addrs = [a.lower() for a in p["addresses"]]
    notes = []
    prev_file = f"{token}.jsonl"
    hop_src = token
    for i, a in enumerate(addrs):
        files = [f"{hop_src}.jsonl"] + ([f"_ctrl/{hop_src}.jsonl"] if i else [])
        found = False
        for f in files:
            for e in ev_lines(chain, f):
                w = words_addr(e.get("response", {}))
                if w == a and e["request"].get("method") in ("eth_getStorageAt", "eth_call"):
                    found = True; notes.append(f"hop{i + 1} {a[:10]} from {f}#{e['id']}"); break
            if found:
                break
        if not found and a not in ("0x" + "00" * 20,):
            # logs-based (RoleGranted etc.) or getOwners paths: accept if the address appears anywhere in that file
            txt = " ".join(open(ROOT / "data" / "control" / chain / "evidence" / f).read() for f in files if (ROOT / "data" / "control" / chain / "evidence" / f).exists())
            if a[2:] in txt.lower():
                found = True; notes.append(f"hop{i + 1} {a[:10]} appears in {files[-1]} (log/array evidence)")
        if not found:
            return False, f"hop{i + 1} {a} not found in raw evidence"
        hop_src = a
    term = addrs[-1]
    code_ev = None
    for e in ev_lines(chain, f"_ctrl/{term}.jsonl") + ev_lines(chain, f"{token}.jsonl"):
        rq = e["request"]
        if rq.get("method") == "eth_getCode" and rq["params"][0].lower() == term:
            code_ev = e; break
    if code_ev is None:
        return (not need_eoa), "; ".join(notes) + f"; no eth_getCode evidence for terminal {term}"
    res = code_ev["response"].get("result")
    omitted = code_ev["response"].get("result_omitted")
    is_eoa = res == "0x"
    is7702 = isinstance(res, str) and res.startswith("0xef0100")
    notes.append(f"terminal {term[:10]} eth_getCode={'0x (no code)' if is_eoa else ('7702 ' + res[:12]) if is7702 else 'contract(len=%s)' % (code_ev['response'].get('result_len') or len(res or ''))}")
    return ((is_eoa or is7702) if need_eoa else True), "; ".join(notes)


def ck_upgrade_single_eoa(chain, token):
    row = ctl_row(chain, token)
    if row is None:
        return "na", "no control row", ""
    cands = [p for p in _paths(row, "upgrade_paths") if p["terminal_kind"] in ("eoa", "eoa_7702") and "timelock" not in p["kinds"] and not p.get("access_manager_delay_seconds")]
    if not cands:
        return "no", f"no firm EOA path without timelock (verdict upgradeable={row.get('upgradeable')})", "control row"
    notes = []
    for p in cands:
        ok, n = verify_path(chain, token, p)
        notes.append(("VERIFIED " if ok else "NOT VERIFIED ") + n)
        if ok:
            return "yes", "; ".join(notes), "control evidence jsonl"
    return "no", "; ".join(notes), "control evidence jsonl"


def ck_owner_single_eoa(chain, token):
    row = ctl_row(chain, token)
    if row is None:
        return "na", "no control row", ""
    cands = [p for p in _paths(row, "owner_paths") if p["terminal_kind"] in ("eoa", "eoa_7702") and "timelock" not in p["kinds"] and not p.get("access_manager_delay_seconds")]
    if not cands:
        return "no", "no firm EOA owner path", "control row"
    notes = []
    for p in cands:
        ok, n = verify_path(chain, token, p)
        notes.append(("VERIFIED " if ok else "NOT VERIFIED ") + n)
        if ok:
            return "yes", "; ".join(notes), "control evidence jsonl"
    return "no", "; ".join(notes), "control evidence jsonl"


def ck_upgradeable(chain, token):
    row = ctl_row(chain, token)
    if row is None:
        return "na", "no control row", ""
    h, n, _ = ck_is_proxy(chain, token)
    if row.get("upgradeable") != "yes":
        return ("no" if h == "no" or row.get("upgradeable") in ("no", "frozen", "protocol_only") else "na"), f"W3 verdict {row.get('upgradeable')}; proxy evidence: {n}", "control row + probe raw"
    paths = [p for p in (row.get("upgrade_paths") or [])]
    for p in paths[:3]:
        ok, nn = verify_path(chain, token, p, need_eoa=False)
        if ok and h == "yes":
            return "yes", f"proxy evidence: {n}; authority hops verified: {nn}", "control + probe raw"
    return "na", f"upgradeable=yes; proxy evidence {n}; no authority path hop-verifiable ({row.get('upgradeable_reason', '')[:60]})", "control + probe raw"


CHECKERS = {
    "ret_missing_transfer": ck_ret_missing_transfer, "ret_missing_approve": ck_ret_missing_approve, "ret_missing_transferFrom": ck_tf_missing,
    "false_on_failure": ck_false_on_failure, "recipient_not_credited": ck_not_credited, "fresh_address_nonzero_balance": ck_fresh_nonzero, "permissioned_transfer": ck_permissioned,
    "transfer_reverts_fresh_contract": ck_permissioned,
    "fee_on_transfer_observed": ck_fee, "transfer_rounding": ck_rounding, "approve_race_guard": ck_race,
    "approve_zero_addr_reverts": ck_approve_zero_addr, "approve_zero_value_reverts": ck_approve_zero_value, "transfer_zero_reverts": ck_transfer_zero,
    "to_zero_reverts": ck_to_zero, "tf_owner_no_allowance": ck_tf_owner, "allowance_max_decrements": ck_max_decrements, "uint96_limit": ck_uint96,
    "balance_field_le128": ck_uint128, "decimals_low": ck_decimals_low, "decimals_high": ck_decimals_high, "permit_dai_style": ck_dai_permit, "pause_capability": ck_pause_cap,
    "paused_now": ck_paused_now, "blocklist_capability": ck_blocklist_cap, "sanction_block_ronin": ck_sanction_ronin, "sanction_block_tornado": ck_sanction_tornado,
    "blocklist_any": ck_blocklist_any, "share_accounting_ratio": ck_share_ratio, "balance_drift_7d": ck_drift, "hook_callbacks_observed": ck_hooks,
    "flash_mint_capability": ck_flash, "metadata_nonstring": ck_nonstring,
    "is_proxy": ck_is_proxy, "upgradeable": ck_upgradeable, "upgrade_single_eoa": ck_upgrade_single_eoa, "owner_single_eoa": ck_owner_single_eoa,
}


def sample(df, fid, n_yes, n_no, rng):
    st = df[f"{fid}__status"]
    yes = df[st == "yes"]; no = df[st == "no"]
    out = []
    for sub, n in ((yes, n_yes), (no, n_no)):
        if len(sub) == 0:
            continue
        # chain-stratified round robin
        groups = {c: g.sample(frac=1, random_state=rng.randint(0, 10 ** 6)) for c, g in sub.groupby("chain")}
        order = sorted(groups, key=lambda c: -len(groups[c]))
        picked = []
        i = 0
        while len(picked) < min(n, len(sub)):
            for c in order:
                if i < len(groups[c]) and len(picked) < n:
                    picked.append(groups[c].iloc[i])
            i += 1
            if i > len(sub):
                break
        out += picked
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-yes", type=int, default=10)
    ap.add_argument("--n-no", type=int, default=4)
    ap.add_argument("--features")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--append", action="store_true", help="keep existing rows (rows of the same feature+subset are replaced)")
    ap.add_argument("--subset", default="", help="restrict the sample to deployments whose categories contain this tag (e.g. tokenized_security)")
    ap.add_argument("--band", default="", help="restrict the sample to a rank band (e.g. 1-100)")
    ap.add_argument("--cells", help="csv with columns feature,chain,token: check exactly these cells (cells quoted in findings)")
    a = ap.parse_args()
    df = pd.read_parquet(ROOT / "data" / "matrix" / "matrix.parquet")
    rng = random.Random(a.seed)
    feats = a.features.split(",") if a.features else list(CHECKERS)
    rows = []
    sub_df = df[df.categories.fillna("").str.split(";").apply(lambda x: a.subset in x)] if a.subset else df
    if a.band:
        sub_df = sub_df[sub_df.rank_band == a.band]
    tag = (a.subset or "") + (("+" if a.subset else "") + "band" + a.band if a.band else "")
    plan = []
    if a.cells:
        for c in csv.DictReader(open(a.cells)):
            if c["feature"] in CHECKERS:
                plan.append((c["feature"], df[(df.chain == c["chain"]) & (df.token == c["token"])].iloc[0]))
        feats = sorted({p[0] for p in plan})
    else:
        for fid in feats:
            for r in sample(sub_df, fid, a.n_yes, a.n_no, rng):
                plan.append((fid, r))
    for fid, r in plan:
        if True:
            try:
                hand, note, where = CHECKERS[fid](r["chain"], r["token"])
            except LookupError as e:
                hand, note, where = "na", str(e), ""
            except Exception as e:  # noqa: BLE001
                hand, note, where = "na", f"checker_error {type(e).__name__}: {e}", ""
            m = r[f"{fid}__status"]
            rows.append({"feature": fid, "subset": tag or ("named_cells" if a.cells else "random"), "chain": r["chain"], "token": r["token"], "symbol": r["symbol"], "matrix_status": m, "matrix_value": r[f"{fid}__value"],
                         "hand_decoded": hand, "agree": (hand == m) if hand != "na" else "", "decoded_evidence": note, "where": where})
    old = []
    if a.append and OUT.exists():
        sub_tag = tag or ("named_cells" if a.cells else "random")
        old = [x for x in csv.DictReader(open(OUT)) if not (x["feature"] in feats and x.get("subset", "random") == sub_tag)]
        for x in old:
            x.setdefault("subset", "random")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    allrows = old + rows
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys() if rows else old[0].keys()))
        w.writeheader(); w.writerows(allrows)
    sm = {}
    for r in rows:
        d = sm.setdefault(r["feature"] + "@" + r["subset"], [0, 0, 0, 0])
        d[0] += 1
        if r["agree"] is True:
            d[1] += 1
        elif r["agree"] is False:
            d[2] += 1
        else:
            d[3] += 1
    print(f"{'feature':34s} checked agree DISAGREE na")
    for f, (n, ag, dis, na) in sm.items():
        print(f"{f:34s} {n:7d} {ag:5d} {dis:8d} {na:3d}")


if __name__ == "__main__":
    main()
