"""Independent decoders for the saved raw evidence (A1 spot-checks). Deliberately does NOT import scripts/probe/*:
it re-implements the minimum needed (ABI-decode the harness payload, PUSH4 scan) so a classification bug in W1's
classify.py cannot hide behind the same bug in the checker.

Harness payload (from contracts/Probe.sol, as documented in scripts/probe/README.md): eth_call returns abi `bytes[]`,
one entry per script; each entry is abi `(bool ok, uint32 gas, uint32 retlen, bytes ret)[]`, one tuple per op.
Script order and op order are the order of `new(...)` / op calls in scripts/probe/battery.py `build_scripts`.
"""
import gzip
import json
import pathlib

from eth_abi import decode
from eth_utils import keccak

ROOT = pathlib.Path(__file__).resolve().parents[2]

SCRIPTS = ("views transfer transfer_over transfer_zero transfer_self transfer_zero_addr transfer_dead transfer_token big_frac big_96 "
           "big_max big_all transfer_twice to_sink to_reject hooks approve_basic approve_nznz approve_max approve_zero_addr approve_zero "
           "inc_dec tf_noallow tf_allow tf_max tf_exceed tf_self tf_zero tf_to_self_spender sanctioned_0 sanctioned_1").split()

# op index of the labelled ops inside each script
OPS = {
    "transfer": ["bal_h0", "bal_r0", "bal_t0", "ts0", "tr", "bal_h1", "bal_r1", "bal_t1", "ts1"],
    "transfer_over": ["bal_h0", "tr", "bal_h1", "bal_r1"],
    "transfer_zero": ["bal_h0", "tr", "bal_h1"],
    "transfer_self": ["bal_h0", "ts0", "tr", "bal_h1", "ts1"],
    "transfer_zero_addr": ["bal_h0", "ts0", "tr", "bal_h1", "ts1"],
    "transfer_dead": ["bal_h0", "ts0", "tr", "bal_h1", "ts1", "bal_d1"],
    "transfer_token": ["bal_h0", "bal_t0", "tr", "bal_h1", "bal_t1"],
    "big_96": ["bal_h0", "tr", "bal_h1"],
    "big_max": ["bal_h0", "tr", "bal_h1", "bal_r1"],
    "approve_basic": ["alw0", "ap", "alw1"],
    "approve_nznz": ["ap1", "alw1", "ap2", "alw2", "ap3", "alw3"],
    "approve_max": ["ap", "alw1"],
    "approve_zero_addr": ["ap", "alw1"],
    "approve_zero": ["ap", "alw1"],
    "tf_noallow": ["bal_h0", "bal_r0", "tf", "bal_h1", "bal_r1"],
    "tf_allow": ["bal_h0", "bal_r0", "ap", "tf", "alw1", "bal_h1", "bal_r1"],
    "tf_max": ["bal_h0", "bal_r0", "ap", "tf", "alw1", "bal_h1", "bal_r1"],
    "tf_exceed": ["ap", "tf", "alw1", "bal_r1"],
    "tf_self": ["bal_h0", "bal_r0", "tf", "bal_h1", "bal_r1"],
    "tf_zero": ["tf"],
    "sanctioned_0": ["bal_h0", "tr", "bal_h1"],
    "sanctioned_1": ["bal_h0", "tr", "bal_h1"],
    "hooks": ["settoken", "reg_sink_recipient", "reg_h_sender", "reg_h_recipient", "bal_h0", "bal_s0", "tr", "bal_h1", "bal_s1",
              "sink_calls", "sink_sels", "sink_bal_at_call", "h_calls", "h_sels", "h_bal_at_call", "reg_impl_sender"],
}
PAUSE_ACC = ["paused()", "isPaused()", "stopped()", "isStopped()", "pausedPublic()", "pausedOwnerAdmin()", "transfersPaused()",
             "transferPaused()", "tradingPaused()", "isTransferPaused()", "pausedTransfers()", "emergencyStop()", "isLocked()", "locked()"]
VIEW_OTHER = ["DOMAIN_SEPARATOR()", "nonces(address)", "PERMIT_TYPEHASH()", "version()", "eip712Domain()", "granularity()",
              "defaultOperators()", "supportsInterface(bytes4)", "delegate()", "target()", "implementation()"]
BL_FUNCS = ["isBlacklisted(address)", "isBlackListed(address)", "getBlackListStatus(address)", "isBlocked(address)", "_isBlacklisted(address)",
            "blacklisted(address)", "isBlocklisted(address)", "blocklisted(address)", "blacklist(address)", "blacklists(address)",
            "blackList(address)", "isBlackList(address)", "_blacklist(address)", "isFrozen(address)", "frozen(address)", "isBanned(address)",
            "isBot(address)", "bots(address)", "_isBot(address)", "isSanctioned(address)", "isRestricted(address)", "blocked(address)",
            "isDenied(address)", "isBlacklist(address)", "isInBlacklist(address)"]
VIEW_IDX = {"decimals": 0, "name": 1, "symbol": 2, "totalSupply": 3}
for i, s in enumerate(VIEW_OTHER):
    VIEW_IDX["v:" + s] = 9 + i
_b = 9 + len(VIEW_OTHER)
for i, s in enumerate(PAUSE_ACC):
    VIEW_IDX["pause:" + s] = _b + i
_b += len(PAUSE_ACC)
for i, s in enumerate(BL_FUNCS):
    VIEW_IDX["bl_H:" + s] = _b + 2 * i
    VIEW_IDX["bl_S:" + s] = _b + 2 * i + 1


R_ADDR = keccak(text="weird-erc20-live/recipient-eoa")[12:].hex()  # fake recipient address (derivation documented in scripts/probe/common.py)


def load_raw(chain, token):
    return json.load(gzip.open(ROOT / "data" / "probe" / chain / "raw" / f"{token}.json.gz"))


def battery_bytes(raw):
    for c in raw["calls"]:
        if c["tag"].startswith("battery"):
            res = c["response"].get("result")
            if isinstance(res, str):
                return bytes.fromhex(res[2:]), c["cid"]
            if isinstance(res, list):  # eth_simulateV1 (real-holder mode): second call is the harness
                try:
                    rd = res[0]["calls"][-1]["returnData"]
                    return bytes.fromhex(rd[2:]), c["cid"]
                except Exception:
                    return None, c["cid"]
    return None, None


def scripts(raw):
    """-> (dict script -> list[(ok, gas, retlen, ret)], battery cid) or (None, None)"""
    b, cid = battery_bytes(raw)
    if not b:
        return None, cid
    (outs,) = decode(["bytes[]"], b)
    if len(outs) != len(SCRIPTS):
        return None, cid
    res = {}
    for n, o in zip(SCRIPTS, outs):
        if not o:
            res[n] = None
            continue
        (arr,) = decode(["(bool,uint32,uint32,bytes)[]"], o)
        res[n] = list(arr)
    return res, cid


class S:
    """accessor: S(scripts)['transfer','tr'] -> (ok, gas, retlen, ret)"""

    def __init__(self, sc):
        self.sc = sc

    def __getitem__(self, k):
        script, op = k
        steps = self.sc.get(script)
        if steps is None:
            return None
        idx = OPS[script].index(op) if script in OPS else VIEW_IDX[op]
        return steps[idx] if idx < len(steps) else None


def u(step):
    return int.from_bytes(step[3][:32], "big") if step and step[0] and step[2] >= 32 else None


def shape(step):
    """-> 'reverts' | 'no_return' | 'true' | 'false' | 'other'"""
    if step is None:
        return "missing"
    ok, gas, rl, ret = step
    if not ok:
        return "reverts"
    if rl == 0:
        return "no_return"
    if rl == 32:
        v = int.from_bytes(ret[:32], "big")
        return "true" if v == 1 else "false" if v == 0 else "other"
    return "other"


def revert_text(step):
    ok, gas, rl, ret = step
    if ok:
        return ""
    if ret[:4].hex() == "08c379a0":
        try:
            return "Error(" + decode(["string"], ret[4:])[0] + ")"
        except Exception:
            return "Error(?)"
    if ret[:4].hex() == "4e487b71":
        return "Panic(0x%x)" % int.from_bytes(ret[4:36], "big")
    return ret[:4].hex() or "empty"


def call_by_tag(raw, tag):
    return [c for c in raw["calls"] if c["tag"] == tag]


def hexint(res):
    return int(res, 16) if isinstance(res, str) and len(res) > 2 else None


# ---------------------------------------------------------------- bytecode
def push4_set(code_hex):
    code = bytes.fromhex(code_hex[2:] if code_hex.startswith("0x") else code_hex)
    out, i, n = set(), 0, len(code)
    while i < n:
        op = code[i]
        if 0x60 <= op <= 0x7F:
            ln = op - 0x5F
            if ln == 4 and i + 5 <= n:
                out.add(code[i + 1:i + 5].hex())
            i += 1 + ln
        else:
            i += 1
    return out


def sel(sig):
    return keccak(text=sig)[:4].hex()


def code_selectors(raw):
    sels = set()
    for tag in ("code", "impl_code"):
        for c in call_by_tag(raw, tag):
            r = c["response"].get("result")
            if isinstance(r, str) and len(r) > 4:
                sels |= push4_set(r)
    return sels
