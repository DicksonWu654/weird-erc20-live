"""Tiny ABI helpers + constants (mainnet) for the buy/sell simulator."""
from eth_abi import encode, decode
from eth_utils import keccak, to_checksum_address

_sel_cache = {}


def sel(sig):
    s = _sel_cache.get(sig)
    if s is None:
        s = _sel_cache[sig] = keccak(text=sig)[:4]
    return s


def enc(sig, *args):
    """enc("transfer(address,uint256)", to, amt) -> 0x calldata hex string"""
    types = sig[sig.index("(") + 1:sig.rindex(")")]
    from eth_abi.grammar import parse
    tl = _split_types(types)
    return "0x" + (sel(sig) + (encode(tl, args) if tl else b"")).hex()


def _split_types(t):
    out, depth, cur = [], 0, ""
    for ch in t:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def dec(types, hexdata):
    b = bytes.fromhex(hexdata[2:] if hexdata.startswith("0x") else hexdata)
    return decode(types, b)


def addr_from_key(key):
    return to_checksum_address("0x" + keccak(text=key)[12:].hex())


def decode_revert(ret_hex, err=None):
    """Human readable revert reason from returnData / error dict."""
    data = None
    if err and isinstance(err, dict):
        data = err.get("data")
    data = data or ret_hex
    if data and data.startswith("0x08c379a0") and len(data) >= 10 + 128:
        try:
            return decode(["string"], bytes.fromhex(data[10:]))[0]
        except Exception:
            pass
    if data and data.startswith("0x4e487b71") and len(data) >= 10 + 64:
        code = int(data[10:74], 16)
        return f"Panic(0x{code:02x})"
    msg = (err or {}).get("message") if isinstance(err, dict) else None
    if msg and msg.startswith("execution reverted: "):
        return msg[len("execution reverted: "):]
    if data and len(data) >= 10:
        return "custom:" + data[:10] + (f"(+{(len(data) - 10) // 2}b)" if len(data) > 10 else "")
    return msg or "revert(no data)"


WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dEaD"

V2_ROUTERS = {  # factory (lower) -> (name, router)
    "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f": ("uniswap_v2", "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"),
    "0xc0aee478e3658e2610c5f7a4a2e1777ce9e4f2ac": ("sushiswap", "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F"),
}
V3_FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"   # original SwapRouter (May 2021)
V3_QUOTER = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"   # Quoter v1
V1_FACTORY = "0xc0a47dfe034b400b47bdad5fecda2621de6c4d95"
# Uniswap V4 mainnet deployments (docs.uniswap.org/contracts/v4/deployments,
# checked 2026-10-21).  V4 pool ids are bytes32 Initialize ids, not addresses.
V4_POOL_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"
UNIVERSAL_ROUTER_V4 = "0x66a9893cc07d91d95644aedd05d03f95e1dba8af"  # Universal Router V2.0
V4_QUOTER = "0x99bd42b2b22e531f283b7f8a86dff631eb16a9d1"
V4_STATE_VIEW = "0x7ffe42c4a5deea5b0fec41c94c136cf115597227"
PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"

# EOAs used by the simulator (derived, no code, no history)
BUYER = addr_from_key("weird-erc20-sim:buyer")
RECV = addr_from_key("weird-erc20-sim:receiver")
# helper contracts injected with eth_simulateV1 state overrides (never deployed)
H_BAL = "0x" + "5a" * 19 + "01"    # returns ETH balance of address in calldata[0:32]
H_CODE = "0x" + "5a" * 19 + "02"   # returns the runtime code of address in calldata[0:32]
H_SIZE = "0x" + "5a" * 19 + "03"   # returns extcodesize of address in calldata[0:32]
CODE_BAL = "0x600035316000526020" + "6000f3"
CODE_CODE = "0x600035803b80600060008" + "43c90506000f3"
CODE_SIZE = "0x6000353b6000526020" + "6000f3"
