"""Uniswap V4 single-pool plans and result helpers.

V4 pools are entries in the PoolManager singleton, so ``pool`` is the 32-byte
PoolId (the Initialize event id), rather than a pool contract address.  The
PoolKey is carried in the census/pairs row.  Swaps use the mainnet Universal
Router V2 (V4_SWAP command, deployed at block 21,689,092), V4 Quoter and
Permit2.  The V2.0 router's ExactInputSingleParams has no minHopPriceX36;
that field was added in Universal Router 2.1.1.
"""
from eth_abi import encode, decode
from eth_utils import keccak

from abi import (
    BUYER, H_BAL, PERMIT2, RECV, ZERO, WETH,
    UNIVERSAL_ROUTER_V4, V4_QUOTER, V4_STATE_VIEW, enc, sel,
)
import engine as E

V4_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"
V4_ROUTER = UNIVERSAL_ROUTER_V4
V4_QUOTE = V4_QUOTER
V4_STATE = V4_STATE_VIEW


def sqrt_ratio_at_tick(tick):
    """Exact TickMath Q64.96 boundary, including Solidity's final round-up."""
    tick = int(tick)
    if not -887272 <= tick <= 887272:
        raise ValueError('tick outside TickMath domain')
    constants = (
        0xfffcb933bd6fad37aa2d162d1a594001, 0xfff97272373d413259a46990580e213a,
        0xfff2e50f5f656932ef12357cf3c7fdcc, 0xffe5caca7e10e4e61c3624eaa0941cd0,
        0xffcb9843d60f6159c9db58835c926644, 0xff973b41fa98c081472e6896dfb254c0,
        0xff2ea16466c96a3843ec78b326b52861, 0xfe5dee046a99a2a811c461f1969c3053,
        0xfcbe86c7900a88aedcffc83b479aa3a4, 0xf987a7253ac413176f2b074cf7815e54,
        0xf3392b0822b70005940c7a398e4b70f3, 0xe7159475a2c29b7443b29c7fa6e889d9,
        0xd097f3bdfd2022b8845ad8f792aa5825, 0xa9f746462d870fdf8a65dc1f90e061e5,
        0x70d869a156d2a1b890bb3df62baf32f7, 0x31be135f97d08fd981231505542fcfa6,
        0x9aa508b5b7a84e1c677de54f3e99bc9, 0x5d6af8dedb81196699c329225ee604,
        0x2216e584f5fa1ea926041bedfe98, 0x48a170391f7dc42444e8fa2,
    )
    absolute = abs(tick)
    ratio = 1 << 128
    for bit, factor in enumerate(constants):
        if absolute & (1 << bit):
            ratio = ratio * factor >> 128
    if tick > 0:
        ratio = ((1 << 256)-1) // ratio
    return (ratio >> 32) + bool(ratio & ((1 << 32)-1))


def position_amounts(liquidity, tick_lower, tick_upper, sqrt_price_x96):
    """Withdrawable position principal at price; includes out-of-range amounts.

    Fees are excluded. Round down separately per position, as SqrtPriceMath
    does on removal. The manager's shared currency balance is never a pool
    reserve. This formula also applies to V3 concentrated positions.
    """
    liquidity, price = int(liquidity), int(sqrt_price_x96)
    if liquidity < 0 or price <= 0 or int(tick_lower) >= int(tick_upper):
        raise ValueError('invalid liquidity, price or tick range')
    lower, upper = sqrt_ratio_at_tick(tick_lower), sqrt_ratio_at_tick(tick_upper)
    clipped = max(lower, min(price, upper))
    amount0 = ((liquidity << 96) * (upper-clipped) // upper) // clipped
    amount1 = liquidity * (clipped-lower) // (1 << 96)
    return amount0, amount1

# Universal Router command and v4-periphery Actions values.
CMD_V4_SWAP = 0x10
CMD_SWEEP = 0x04
CMD_PERMIT2_TRANSFER_FROM = 0x02
CMD_WRAP_ETH = 0x0B
CMD_UNWRAP_WETH = 0x0C
ACT_SWAP_EXACT_IN_SINGLE = 0x06
ACT_SETTLE = 0x0B
ACT_SETTLE_ALL = 0x0C
ACT_TAKE = 0x0E
ACT_TAKE_ALL = 0x0F
ADDRESS_THIS = "0x0000000000000000000000000000000000000002"
MSG_SENDER = "0x0000000000000000000000000000000000000001"

# V4 hook permissions are the low 14 address bits.  Keep the names in the
# protocol's bit order so output is useful when auditing a hook address.
HOOK_BITS = {
    13: "before_initialize", 12: "after_initialize",
    11: "before_add_liquidity", 10: "after_add_liquidity",
    9: "before_remove_liquidity", 8: "after_remove_liquidity",
    7: "before_swap", 6: "after_swap", 5: "before_donate",
    4: "after_donate", 3: "before_swap_return_delta",
    2: "after_swap_return_delta", 1: "after_add_liquidity_return_delta",
    0: "after_remove_liquidity_return_delta",
}


def hook_permissions(hooks):
    """Return low-bit mask and named permissions encoded by a hook address."""
    try:
        h = int((hooks or ZERO), 16)
    except (TypeError, ValueError):
        h = 0
    mask = h & ((1 << 14) - 1)
    return mask, [name for bit, name in HOOK_BITS.items() if mask & (1 << bit)]


def _addr(x):
    x = str(x or ZERO).strip()
    if x.lower() in ("", "nan", "none", "null"):
        return ZERO
    if not x.startswith("0x"):
        x = "0x" + x
    return "0x" + x[2:].lower().rjust(40, "0")[-40:]


def _int(x, default=0):
    try:
        if x is None or str(x).lower() in ("", "nan", "none", "null"):
            return default
        return int(float(x))
    except (TypeError, ValueError):
        return default


def pair_key(row, token):
    """Build a normalized PoolKey and swap direction from a pairs row."""
    # run.py leaves all non-standard columns in ``extra``; accepting both
    # locations keeps this helper useful for direct callers and JSONL inputs.
    d = dict(row or {})
    d.update(d.get("extra") or {})
    c0 = _addr(d.get("token0") or d.get("currency0"))
    c1 = _addr(d.get("token1") or d.get("currency1"))
    fee = _int(d.get("fee"))
    spacing = _int(d.get("tick_spacing") or d.get("tickSpacing"))
    hooks = _addr(d.get("hooks") or d.get("hook"))
    tok = _addr(token)
    if tok not in (c0, c1):
        return None
    # PoolKey currencies are sorted by address, and a zero address is native
    # ETH.  zeroForOne means currency0 is the input side.
    return {
        "currency0": c0, "currency1": c1, "fee": fee,
        "tick_spacing": spacing, "hooks": hooks,
        "zero_for_one_buy": tok == c1,
        "token": tok,
        "pool_id": str(row.get("pool") or d.get("pool") or "").lower(),
    }


def _pk_type():
    return "(address,address,uint24,int24,address)"


def _pk_tuple(k):
    return (k["currency0"], k["currency1"], k["fee"], k["tick_spacing"], k["hooks"])


def _single(k, zero_for_one, amount):
    # Universal Router V2.0 / v4-periphery pre-2.1.1:
    # (PoolKey,bool,uint128,uint128,bytes)
    return encode([f"({_pk_type()},bool,uint128,uint128,bytes)"],
                  [(_pk_tuple(k), bool(zero_for_one), int(amount), 0, b"")])


def _v4_input(k, zero_for_one, amount, router_custody=False):
    input_currency = k["currency0"] if zero_for_one else k["currency1"]
    params = [
        _single(k, zero_for_one, amount),
        (encode(["address", "uint256", "bool"], [input_currency, int(amount), False])
         if router_custody else encode(["address", "uint256"], [input_currency, int(amount)])),
        encode(["address", "uint256"], [k["currency1"] if zero_for_one else k["currency0"], 0]),
    ]
    settle_action = ACT_SETTLE if router_custody else ACT_SETTLE_ALL
    return encode(["bytes", "bytes[]"], [bytes([ACT_SWAP_EXACT_IN_SINGLE, settle_action, ACT_TAKE_ALL]), params])


def _v4_input_take(k, zero_for_one, amount, out_to_router=False, payer_is_user=True):
    # Sell uses TAKE for WETH so the outer UNWRAP_WETH command can unwrap it;
    # native ETH is taken straight to msg.sender.
    out_currency = k["currency1"] if zero_for_one else k["currency0"]
    take_action = ACT_TAKE if out_to_router else ACT_TAKE_ALL
    if take_action == ACT_TAKE:
        take_params = encode(["address", "address", "uint256"], [out_currency, ADDRESS_THIS, 0])
    else:
        take_params = encode(["address", "uint256"], [out_currency, 0])
    params = [
        _single(k, zero_for_one, amount),
        # SETTLE_ALL checks the ceiling against the PoolManager debt.  A
        # wallet token can debit slightly more than the requested amount, so
        # use the canonical MAX_UINT ceiling and let the measured balance
        # delta expose that behaviour.
        encode(["address", "uint256"], [k["currency0"] if zero_for_one else k["currency1"], E.MAXU]),
        take_params,
    ]
    # For the canonical UR path, PERMIT2_TRANSFER_FROM first moves a user's
    # input token into the router.  SETTLE then pays from router custody,
    # avoiding a nested Permit2 transfer during PoolManager.unlock.
    params[1] = encode(["address", "uint256", "bool"],
                       [k["currency0"] if zero_for_one else k["currency1"], int(amount), payer_is_user])
    return encode(["bytes", "bytes[]"], [bytes([ACT_SWAP_EXACT_IN_SINGLE, ACT_SETTLE, take_action]), params])


def _execute(commands, inputs):
    return "0x" + (sel("execute(bytes,bytes[])") + encode(["bytes", "bytes[]"], [commands, inputs])).hex()


def is_native(c):
    return _addr(c) == ZERO


def buy_data(k, amount):
    """Universal Router calldata and msg.value for a native-ETH buy."""
    input_currency = k["currency0"] if k["zero_for_one_buy"] else k["currency1"]
    inp = _v4_input(k, k["zero_for_one_buy"], amount, router_custody=(input_currency.lower() == WETH.lower()))
    if is_native(input_currency):
        sweep = encode(["address", "address", "uint256"], [ZERO, BUYER, 0])
        return _execute(bytes([CMD_V4_SWAP, CMD_SWEEP]), [inp, sweep]), amount
    if input_currency.lower() == WETH.lower():
        wrap = encode(["address", "uint256"], [ADDRESS_THIS, amount])
        return _execute(bytes([CMD_WRAP_ETH, CMD_V4_SWAP]), [wrap, inp]), amount
    return None, amount


def sell_data(k, amount):
    """Universal Router calldata for a token-to-ETH sell using Permit2."""
    zero_for_one = not k["zero_for_one_buy"]
    output_currency = k["currency1"] if zero_for_one else k["currency0"]
    # The canonical V4 action sequence lets SETTLE_ALL pull the input from
    # the EOA through Permit2 while the PoolManager is unlocked.  A separate
    # pull into router custody breaks taxed tokens: the router receives less
    # than the requested amount and its subsequent exact settlement reverts.
    inp = _v4_input(k, zero_for_one, amount)
    if is_native(output_currency):
        return _execute(bytes([CMD_V4_SWAP]), [inp])
    if output_currency.lower() == WETH.lower():
        # TAKE_ALL sends WETH to the EOA.  Observe the WETH balance delta.
        return _execute(bytes([CMD_V4_SWAP]), [inp])
    return None


def permit2_pull_data(k, amount):
    """Pull the sell amount into the router before the V4 unlock callback."""
    zero_for_one = not k["zero_for_one_buy"]
    input_currency = k["currency0"] if zero_for_one else k["currency1"]
    pull = encode(["address", "address", "uint160"], [input_currency, V4_ROUTER, int(amount)])
    return _execute(bytes([CMD_PERMIT2_TRANSFER_FROM]), [pull])


def quote_data(k, zero_for_one, amount):
    # V4Quoter.QuoteExactSingleParams (PoolKey,bool,uint128,bytes)
    return enc("quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))",
               (_pk_tuple(k), bool(zero_for_one), int(amount), b""))


SWAP_TOPIC = "0x" + keccak(text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)").hex()


def core_tax(logs, pool_id, zero_for_one, wallet_in, wallet_out):
    """Tax excluding V4 core LP fee, from the user-initiated Swap event."""
    matches = []
    for log in logs or []:
        topics = log.get("topics") or []
        if (log.get("address", "").lower() != V4_MANAGER or len(topics) < 3
                or topics[0].lower() != SWAP_TOPIC or topics[1].lower() != pool_id.lower()
                or topics[2][-40:].lower() != V4_ROUTER[2:].lower()):
            continue
        a0, a1 = decode(["int128", "int128"], bytes.fromhex(log["data"][2:130]))
        matches.append((abs(a0), abs(a1)))
    if len(matches) != 1 or not wallet_in or wallet_out is None:
        return None, None
    core_in, core_out = matches[0] if zero_for_one else matches[0][::-1]
    if not core_out:
        return None, {"in": core_in, "out": core_out}
    tax = max(0.0, 1 - core_in * wallet_out / (wallet_in * core_out))
    return round(tax, 6), {"in": core_in, "out": core_out}


def phase1(token, pool, row, amount, meta=True):
    k = pair_key(row, token)
    P = E.Plan().block(E.first_state())
    if k is None:
        return P, None
    if meta:
        E.token_meta_calls(P, token)
    P.call("v4_slot0", V4_STATE, enc("getSlot0(bytes32)", bytes.fromhex(str(pool)[2:].zfill(64))))
    P.call("v4_liquidity", V4_STATE, enc("getLiquidity(bytes32)", bytes.fromhex(str(pool)[2:].zfill(64))))
    q = quote_data(k, k["zero_for_one_buy"], amount)
    P.call("buy_quote", V4_QUOTE, q, gas=E.GAS)
    P.call("buy_quote_bal_pre", H_BAL, E.a32(BUYER))
    data, value = buy_data(k, amount)
    if data is None:
        P.call("buy", V4_ROUTER, "0x", value=0, gas=E.GAS)
    else:
        P.call("buy", V4_ROUTER, data, value=value, gas=E.GAS)
    P.call("buy_quote_bal_post", H_BAL, E.a32(BUYER))
    P.call("buy_bal", token, enc("balanceOf(address)", BUYER))
    return P, k


def _after_buy(P, token, k, received):
    # Preserve the entire integer lot for the full sell probe.  Transfer
    # behaviour at this tiny balance is tested independently by the verifier.
    sent = 0 if received < 1000 else received * E.XFER_FR[0] // E.XFER_FR[1]
    P.call("xfer_bal_b0", token, enc("balanceOf(address)", RECV))
    P.call("xfer", token, enc("transfer(address,uint256)", RECV, sent))
    P.call("xfer_bal_a", token, enc("balanceOf(address)", BUYER))
    P.call("xfer_bal_b", token, enc("balanceOf(address)", RECV))
    # Universal Router V2 settles ERC-20s through Permit2.  Both approvals
    # are explicit calls from the plain EOA: ERC20 -> Permit2 and Permit2 -> UR.
    P.call("approve", token, enc("approve(address,uint256)", PERMIT2, E.MAXU))
    P.call("permit2_approve", PERMIT2, enc("approve(address,address,uint160,uint48)", token, V4_ROUTER, E.MAXU & ((1 << 160) - 1), (1 << 48) - 1))
    P.call("allowance", token, enc("allowance(address,address)", BUYER, PERMIT2))
    P.call("permit2_allowance", PERMIT2, enc("allowance(address,address,address)", BUYER, token, V4_ROUTER))


def phase2(token, pool, row, amount, received):
    k = pair_key(row, token)
    P = E.Plan().block(E.first_state())
    if k is None:
        return P, None
    buy, value = buy_data(k, amount)
    if buy is not None:
        P.call("buy_quote", V4_QUOTE, quote_data(k, k["zero_for_one_buy"], amount), gas=E.GAS)
        P.call("buy_quote_bal_pre", H_BAL, E.a32(BUYER))
        P.call("buy", V4_ROUTER, buy, value=value, gas=E.GAS)
        P.call("buy_quote_bal_post", H_BAL, E.a32(BUYER))
        P.call("buy_bal", token, enc("balanceOf(address)", BUYER))
    _after_buy(P, token, k, received)
    sell_zero_for_one = not k["zero_for_one_buy"]
    for tag, bo, ladder in (("s1", None, E.SELL1_FR), ("s2", {"number": "+%d" % E.DELAY_BLOCKS}, E.SELL2_FR)):
        P.block(None, bo)
        for i, (n, d) in enumerate(ladder):
            pre = f"{tag}{i}"
            # An integer-lot pool can return fewer than 1,000 token units.
            # Test one executable full-lot sale before fractional ladder dust.
            a = received if received < 1000 and pre == "s10" else received * n // d
            P.call(pre + "_bal_pre", token, enc("balanceOf(address)", BUYER))
            P.call(pre + "_quote", V4_QUOTE, quote_data(k, sell_zero_for_one, a), gas=E.GAS)
            quote = k["currency1"] if sell_zero_for_one else k["currency0"]
            if is_native(quote):
                P.call(pre + "_eth_pre", H_BAL, E.a32(BUYER))
            else:
                P.call(pre + "_eth_pre", WETH, enc("balanceOf(address)", BUYER))
            data = sell_data(k, a)
            if data is None:
                P.call(pre, V4_ROUTER, "0x", gas=E.GAS)
            else:
                P.call(pre, V4_ROUTER, data, gas=E.GAS)
            if is_native(quote):
                P.call(pre + "_eth_post", H_BAL, E.a32(BUYER))
            else:
                P.call(pre + "_eth_post", WETH, enc("balanceOf(address)", BUYER))
            P.call(pre + "_bal_post", token, enc("balanceOf(address)", BUYER))
    return P, k


def classify_revert(reason, hooks):
    """Conservative origin hint; absence of a trace is recorded as unknown."""
    s = (reason or "").lower()
    if any(x in s for x in ("hook", "before_swap", "after_swap", "ihooks", "hookcallback")):
        return "hook"
    if any(x in s for x in ("transfer", "stf", "insufficient_input", "blacklist", "sell", "tax")):
        return "token"
    return "unknown" if hooks and hooks.lower() != ZERO else "pool_manager"

