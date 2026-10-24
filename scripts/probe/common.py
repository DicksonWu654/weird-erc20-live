"""Shared constants and ABI helpers for the probe harness."""
import json
import pathlib
import re

from eth_abi import decode as abi_decode, encode as abi_encode
from eth_utils import keccak

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def hx(b: bytes) -> str:
    return "0x" + b.hex()


def unhex(s: str) -> bytes:
    s = s[2:] if s.startswith("0x") else s
    return bytes.fromhex(s) if s else b""


def fake_addr(name: str) -> str:
    return "0x" + keccak(b"weird-erc20-live/" + name.encode())[12:].hex()


def addr_int(a: str) -> int:
    return int(a, 16)


# Fixed fake accounts (derived from a label, so nothing about them is special on any chain)
H = fake_addr("harness")      # the harness (state-override code) AND, in EOA mode, the plain sender
SINK = fake_addr("sink")      # accepting actor: spender + contract recipient + callback recorder
REJ = fake_addr("rejecter")   # contract that reverts on every call
R = fake_addr("recipient-eoa")
E = fake_addr("caller-eoa")   # `from` of the outer eth_call
DEAD = "0x000000000000000000000000000000000000dEaD"
ZERO = "0x" + "00" * 20
# OFAC-sanctioned addresses used for the blocklist differential (Tornado Cash router; Ronin-hack Lazarus address)
SANCTIONED = ["0xd90e2f925DA726b50C4Ed8D0Fb90Ad053324F31b", "0x098B716B8Aaf21512996dC57EB0615e2383E2f96"]
ERC1820 = "0x1820a4B7618BdE71Dce8cdc73aAB6C95905faD24"
UINT_MAX = (1 << 256) - 1

BYTECODE = json.loads((HERE / "bytecode.json").read_text())
PROBE_CODE = "0x" + BYTECODE["Probe"]["runtime"].removeprefix("0x")
SINK_CODE = "0x" + BYTECODE["Sink"]["runtime"].removeprefix("0x")
REJ_CODE = "0x" + BYTECODE["Rejecter"]["runtime"].removeprefix("0x")


def _split_types(sig: str):
    inner = sig[sig.index("(") + 1: sig.rindex(")")]
    return [t for t in inner.split(",") if t]


def sel(sig: str) -> bytes:
    return keccak(sig.encode())[:4]


def cd(sig: str, *args) -> bytes:
    """calldata for a signature like 'transfer(address,uint256)'"""
    types = _split_types(sig)
    return sel(sig) + (abi_encode(types, args) if types else b"")


def word(v: int) -> str:
    return "0x" + v.to_bytes(32, "big").hex()


def pad_addr(a: str) -> bytes:
    return bytes(12) + unhex(a)


# ---- step decoding -------------------------------------------------------------------------------------------
class Step:
    """One op result from the harness: ok flag, gas, full return length, first <=320 return bytes."""
    __slots__ = ("ok", "gas", "retlen", "ret")

    def __init__(self, ok, gas, retlen, ret):
        self.ok, self.gas, self.retlen, self.ret = ok, gas, retlen, ret

    def as_dict(self):
        return {"ok": self.ok, "gas": self.gas, "retlen": self.retlen, "ret": hx(self.ret)}

    def u256(self):
        """first return word as int if the call succeeded and returned >=32 bytes, else None"""
        if self.ok and self.retlen >= 32 and len(self.ret) >= 32:
            return int.from_bytes(self.ret[:32], "big")
        return None


def decode_steps(payload: bytes):
    if not payload:
        return None
    (arr,) = abi_decode(["(bool,uint32,uint32,bytes)[]"], payload)
    return [Step(*x) for x in arr]


def decode_run(ret: bytes):
    (outs,) = abi_decode(["bytes[]"], ret)
    return [decode_steps(o) for o in outs]


def revert_info(ret: bytes, retlen=None):
    """-> (kind, text). kind in error_string | panic | custom | empty"""
    if not ret:
        return ("empty", "")
    if ret[:4] == bytes.fromhex("08c379a0") and len(ret) >= 68:
        try:
            (s,) = abi_decode(["string"], ret[4:])
            return ("error_string", s)
        except Exception:
            return ("error_string", ret[4:].decode("utf8", "replace"))
    if ret[:4] == bytes.fromhex("4e487b71") and len(ret) >= 36:
        return ("panic", hex(int.from_bytes(ret[4:36], "big")))
    return ("custom", hx(ret[:68]))


def shape(step: Step):
    """Classify a state-changing call's outcome. Returns (kind, detail):
    reverts(kind:text) | no_return | returns_true | returns_false | returns_other"""
    if step is None:
        return ("missing", "")
    if not step.ok:
        k, t = revert_info(step.ret)
        return ("reverts", f"{k}:{t}" if t else k)
    if step.retlen == 0:
        return ("no_return", "")
    if step.retlen == 32:
        v = int.from_bytes(step.ret[:32], "big")
        if v == 1:
            return ("returns_true", "")
        if v == 0:
            return ("returns_false", "")
        return ("returns_other", hex(v))
    return ("returns_other", f"len={step.retlen}:{hx(step.ret[:64])}")


def decode_string(ret: bytes, retlen: int):
    """-> (kind, value): string | bytes32 | invalid | empty"""
    if not ret or retlen == 0:
        return ("empty", None)
    if retlen == 32:
        raw = ret[:32].rstrip(b"\x00")
        try:
            return ("bytes32", raw.decode("utf8"))
        except UnicodeDecodeError:
            return ("bytes32", raw.decode("latin1"))
    try:
        (s,) = abi_decode(["string"], ret) if retlen <= len(ret) else (None,)
        if s is None:
            return ("string_truncated", None)
        return ("string", s)
    except Exception:
        return ("invalid", hx(ret[:96]))


class Script:
    """A harness script: ordered ops + label -> index map."""

    def __init__(self):
        self.ops = []
        self.labels = {}

    def _add(self, label, kind, target, data):
        if label in self.labels:
            raise ValueError(f"duplicate label {label}")
        self.labels[label] = len(self.ops)
        self.ops.append((kind, target, data))

    def c(self, label, target, data):    # harness calls
        self._add(label, 0, target, data)

    def v(self, label, target, data):    # harness staticcalls
        self._add(label, 1, target, data)

    def sc(self, label, target, data):   # Sink calls
        self._add(label, 2, target, data)

    def sv(self, label, target, data):   # Sink staticcalls
        self._add(label, 3, target, data)


RUN_SIG = "run(address,uint256,uint256,uint256,address,(uint8,address,bytes)[][])"


def encode_run(token, gas_cap, view_cap, script_gas, scripts):
    args = [token, gas_cap, view_cap, script_gas, SINK, [[(k, t, d) for (k, t, d) in s.ops] for s in scripts]]
    return sel(RUN_SIG) + abi_encode(["address", "uint256", "uint256", "uint256", "address", "(uint8,address,bytes)[][]"], args)


def selectors_from_code(code_hex):
    """PUSH4 <sel> scan (cheap, over-approximate: includes jump-table constants). -> sorted list of 8-hex strings"""
    b = unhex(code_hex)
    sels = set()
    i, n = 0, len(b)
    while i < n:
        op = b[i]
        if 0x60 <= op <= 0x7f:
            ln = op - 0x5f
            if op == 0x63 and i + 5 <= n:
                sels.add(b[i + 1:i + 5].hex())
            i += 1 + ln
        else:
            i += 1
    return sorted(sels)

def minimal_proxy_target(code_hex):
    h = code_hex[2:].lower()
    m = re.fullmatch(r"363d3d373d3d3d363d73([0-9a-f]{40})5af43d82803e903d91602b57fd5bf3", h)
    return "0x" + m.group(1) if m else None

def parse_u256(r):
    if r and "result" in r and isinstance(r["result"], str) and len(r["result"]) >= 66:
        return int(r["result"][:66], 16)
    return None



def merge_overrides(*ovs):
    """merge state-override dicts per address (code / balance / stateDiff keys combine)"""
    out = {}
    for ov in ovs:
        for a, d in ov.items():
            cur = out.setdefault(a, {})
            for k, v in d.items():
                if k == "stateDiff":
                    cur.setdefault("stateDiff", {}).update(v)
                else:
                    cur[k] = v
    return out
