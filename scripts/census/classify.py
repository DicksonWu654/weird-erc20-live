"""Static bytecode classifier for 'ERC-20-like' contracts (identical logic to the DuckDB flags in paradigm_ingest.py).

Definition (see data/census/README.md): runtime code DISPATCHES (implements) totalSupply(), balanceOf(address),
transfer(address,uint256) AND contains the 32-byte ERC-20 Transfer topic.  (c20_loose = selector bytes merely present; it has ~2.5%
precision for 2026 deployments because MEV/router contracts encode calls to those selectors.)  EIP-1167-style minimal proxies are resolved to their
implementation's flags.  `kind` then separates LP / vault / receipt / wrapped tokens.
"""
from eth_utils import keccak

SEL = dict(totalSupply="18160ddd", balanceOf="70a08231", transfer="a9059cbb", approve="095ea7b3",
           allowance="dd62ed3e", transferFrom="23b872dd", getReserves="0902f1ac", token0="0dfe1681", token1="d21220a7",
           v1_tokenAddress="9d76ea58", v1_addLiquidity="422f1043", minter="07546172", bind="e4e1e538",
           getCurrentTokens="cc77828d", getPoolId="38fff2d0", aave_underlying="b16a19de", ctoken_rate="182df0f5",
           asset="38d52e0f", convertToShares="c6e6f592", pricePerShare="99530b06", deposit="d0e30db0",
           withdraw="2e1a7d4d", decimals="313ce567")
TOPIC = bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
SLOTS = [bytes.fromhex(h) for h in (
    "360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc",
    "7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3",
    "a3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50",
    "c5f16f0fcc639fa48a6947836d9850f504798523bf8c9a3a87d5876cf622bcf7")]
_SELB = {k: bytes.fromhex(v) for k, v in SEL.items()}


import re

# Dispatcher comparison following `PUSH4 <selector>` (Solidity: `PUSH4 sel EQ` / `PUSH4 sel DUP1..5 EQ`; Vyper 0.2: `PUSH4 sel PUSH1 0 MLOAD EQ`;
# Vyper >=0.3.7: `PUSH4 sel DUP1..5 XOR`).  A bare `PUSH4 sel` followed by SHL/MUL/MSTORE is an external CALL encoding (the callee's ABI), not an
# implemented function -- routers, MEV bots and sniper contracts contain many of those.
DISPATCH_TAILS = [b"\x14"] + [bytes([d, 0x14]) for d in range(0x80, 0x85)] + [b"\x60\x00\x51\x14"] + [bytes([d, 0x18]) for d in range(0x80, 0x85)]
_DRE = {k: re.compile(b"\x63" + re.escape(bytes.fromhex(SEL[k])) + b"(?:" + b"|".join(re.escape(t) for t in DISPATCH_TAILS) + b")", re.S)
        for k in ("totalSupply", "balanceOf", "transfer")}


def clone_impl(code: bytes):
    n = len(code)
    if n == 45 and code[0:10] == bytes.fromhex("363d3d373d3d3d363d73"):
        return code[10:30]
    if n == 45 and code[0:9] == bytes.fromhex("365f5f375f5f365f73"):
        return code[9:29]
    if n == 44 and code[0:11] == bytes.fromhex("3d3d3d3d363d3d37363d73"):
        return code[11:31]
    return None


def flags(code: bytes) -> dict:
    f = {f"s_{k}": (v in code) for k, v in _SELB.items()}
    f["t_transfer"] = TOPIC in code
    f["proxy_slot"] = any(s in code for s in SLOTS)
    ci = clone_impl(code)
    f["clone_impl"] = ("0x" + ci.hex()) if ci else None
    f["code_len"] = len(code)
    f["code_hash"] = keccak(code).hex()
    for k, rx in _DRE.items():
        f[f"d_{k}"] = bool(rx.search(code)) if f[f"s_{k}"] else False
    f["c20_loose"] = f["s_totalSupply"] and f["s_balanceOf"] and f["s_transfer"] and f["t_transfer"]  # selector bytes anywhere
    # strict (the census definition): the three functions are DISPATCHED (implemented) and the Transfer topic is present
    f["c20_core"] = f["d_totalSupply"] and f["d_balanceOf"] and f["d_transfer"] and f["t_transfer"]
    return f


def kind(f: dict) -> str:
    """Only meaningful when f['c20_core'] (or resolved clone impl flags)."""
    if f["s_getReserves"] and f["s_token0"] and f["s_token1"]:
        return "lp_v2"
    if f["s_v1_tokenAddress"] and f["s_v1_addLiquidity"]:
        return "lp_v1"
    if f["s_bind"] or f["s_getCurrentTokens"] or f["s_getPoolId"]:
        return "lp_balancer"
    if (f["s_asset"] and f["s_convertToShares"]) or f["s_pricePerShare"]:
        return "vault"
    if f["s_aave_underlying"] or f["s_ctoken_rate"]:
        return "receipt"
    if f["s_deposit"] and f["s_withdraw"]:
        return "wrapped_native"
    return "plain"
