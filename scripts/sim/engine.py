"""Buy -> transfer -> sell simulation of one (token, pool) at one block, from a plain EOA via the canonical router,
using eth_simulateV1 (multi-call, state carried across calls and blocks, balance/code state overrides).

Two phases per pair (both at the same pinned block, deterministic):
  phase 1 ("probe+buy"): pool/token metadata + bytecode + owner() + quote for the buy + the buy itself.
  phase 2 ("full"):      the same buy, then transfer 10% to a 2nd EOA, approve, sell ladder in the next block
                         (40% / 5% / 0.5% of the received tokens), then a delayed block (+25 blocks ~ 5 min) with a 2nd
                         ladder (20% / 1%).  Exact amounts are derived from phase 1 (`received`).
"""
import asyncio, json
from abi import *

DEADLINE = 2 ** 40
MAXU = 2 ** 256 - 1
GAS = 2_500_000        # swaps
META_GAS = 400_000     # views/transfers/approves (INVALID opcodes burn all gas)
BLOCK_GAS_LIMIT = 200_000_000
DELAY_BLOCKS = 25
SELL1_FR = [(40, 100), (5, 100), (5, 1000)]
SELL2_FR = [(20, 100), (1, 100)]
XFER_FR = (10, 100)


class Plan:
    def __init__(self):
        self.blocks = []  # list of dict(overrides, block_overrides, calls=[(name, call)])

    def block(self, state=None, block_overrides=None):
        self.blocks.append({"state": state or {}, "bo": block_overrides, "calls": []})
        return self

    def call(self, name, to, data, frm=BUYER, value=0, gas=None):
        c = {"from": frm, "to": to, "data": data}
        if value:
            c["value"] = hex(value)
        c["gas"] = hex(gas or META_GAS)
        c["gasPrice"] = "0x0"  # legacy-type call: works on every historical fork (pre-London rejects default type-2)
        self.blocks[-1]["calls"].append((name, c))
        return self

    def params(self, block):
        """block: int (pinned block number). Relative '+N' block overrides are resolved against it."""
        bsc = []
        for b in self.blocks:
            e = {"calls": [c for _, c in b["calls"]]}
            if b["state"]:
                e["stateOverrides"] = b["state"]
            bo = dict(b["bo"] or {})
            bo["gasLimit"] = hex(BLOCK_GAS_LIMIT)
            if isinstance(bo.get("number"), str) and bo["number"].startswith("+"):
                bo["number"] = hex(block + 1 + int(bo["number"][1:]))
            e["blockOverrides"] = bo
            bsc.append(e)
        return [{"blockStateCalls": bsc, "validation": False, "traceTransfers": False}, hex(block)]

    def names(self):
        return [[n for n, _ in b["calls"]] for b in self.blocks]


def first_state():
    return {BUYER: {"balance": hex(10 ** 20)},
            H_BAL: {"code": CODE_BAL}, H_CODE: {"code": CODE_CODE}, H_SIZE: {"code": CODE_SIZE}}


def a32(a):
    return "0x" + "00" * 12 + a[2:].lower()


class R:
    """Parsed result of a single call."""
    __slots__ = ("ok", "ret", "gas", "err", "logs", "reason", "present")

    def __init__(self, d=None):
        self.present = d is not None
        if d is None:
            self.ok, self.ret, self.gas, self.err, self.logs, self.reason = False, "0x", 0, None, [], "not_executed"
            return
        self.ok = d.get("status") == "0x1"
        self.ret = d.get("returnData", "0x")
        self.gas = int(d.get("gasUsed", "0x0"), 16)
        self.err = d.get("error")
        self.logs = d.get("logs", [])
        self.reason = None if self.ok else decode_revert(self.ret, self.err)

    def u(self, i=0, default=None):
        """i-th uint256 word of return data."""
        if not self.ok or len(self.ret) < 2 + 64 * (i + 1):
            return default
        return int(self.ret[2 + 64 * i: 2 + 64 * (i + 1)], 16)

    def addr(self, i=0):
        v = self.u(i)
        return None if v is None else "0x%040x" % (v & ((1 << 160) - 1))

    def arr_last(self):
        """last element of returned uint[] (getAmountsOut)."""
        try:
            n = int(self.ret[2 + 64:2 + 128], 16)
            return int(self.ret[2 + 128 + 64 * (n - 1): 2 + 128 + 64 * n], 16)
        except Exception:
            return None

    def string(self):
        try:
            return dec(["string"], self.ret)[0]
        except Exception:
            try:  # bytes32 symbols (MKR style)
                return bytes.fromhex(self.ret[2:66]).rstrip(b"\x00").decode("utf8", "replace")
            except Exception:
                return None

    def summary(self):
        return {"ok": self.ok, "gas": self.gas, "reason": self.reason}


def parse_sim(plan, resp_result):
    """-> dict name -> R (and block meta list)."""
    out, metas = {}, []
    resp_result = [b for b in resp_result if b.get("calls")]  # drop auto-filled gap blocks
    for bi, (names, blk) in enumerate(zip(plan.names(), resp_result)):
        metas.append({"number": int(blk["number"], 16), "timestamp": int(blk["timestamp"], 16)})
        calls = blk.get("calls", [])
        for i, n in enumerate(names):
            out[n] = R(calls[i] if i < len(calls) else None)
    return out, metas


# --------------------------------------------------------------------------------------------- V2
def v2_paths(token, quote):
    p = [WETH] + ([] if quote.lower() == WETH.lower() else [quote]) + [token]
    return p, list(reversed(p))


def v2_meta_calls(P, token, pool, factory, meta=True):
    P.call("pool_factory", pool, enc("factory()"))
    P.call("pool_token0", pool, enc("token0()"))
    P.call("pool_token1", pool, enc("token1()"))
    P.call("pool_reserves", pool, enc("getReserves()"))
    if meta:
        token_meta_calls(P, token)


def token_meta_calls(P, token):
    P.call("t_decimals", token, enc("decimals()"))
    P.call("t_symbol", token, enc("symbol()"))
    P.call("t_name", token, enc("name()"))
    P.call("t_supply", token, enc("totalSupply()"))
    P.call("t_owner", token, enc("owner()"))
    P.call("t_getOwner", token, enc("getOwner()"))
    P.call("t_code", H_CODE, a32(token))
    P.call("t_bal_dead", token, enc("balanceOf(address)", DEAD))


def v2_phase1(token, pool, router, quote, amt, meta=True):
    P = Plan().block(first_state())
    fac = None
    v2_meta_calls(P, token, pool, fac, meta)
    buy_p, sell_p = v2_paths(token, quote)
    P.call("buy_quote", router, enc("getAmountsOut(uint256,address[])", amt, buy_p))
    P.call("buy", router, enc("swapExactETHForTokensSupportingFeeOnTransferTokens(uint256,address[],address,uint256)",
                              0, buy_p, BUYER, DEADLINE), value=amt, gas=GAS)
    P.call("buy_bal", token, enc("balanceOf(address)", BUYER))
    return P


def v2_buy_block(P, token, router, quote, amt):
    buy_p, _ = v2_paths(token, quote)
    P.call("buy_quote", router, enc("getAmountsOut(uint256,address[])", amt, buy_p))
    P.call("buy", router, enc("swapExactETHForTokensSupportingFeeOnTransferTokens(uint256,address[],address,uint256)",
                              0, buy_p, BUYER, DEADLINE), value=amt, gas=GAS)
    P.call("buy_bal", token, enc("balanceOf(address)", BUYER))


def common_after_buy(P, token, spender, received):
    """transfer 10% to 2nd EOA + approve (block 1)."""
    x = received * XFER_FR[0] // XFER_FR[1]
    P.call("xfer_bal_b0", token, enc("balanceOf(address)", RECV))
    P.call("xfer", token, enc("transfer(address,uint256)", RECV, x))
    P.call("xfer_bal_a", token, enc("balanceOf(address)", BUYER))
    P.call("xfer_bal_b", token, enc("balanceOf(address)", RECV))
    P.call("approve", token, enc("approve(address,uint256)", spender, MAXU))
    P.call("allowance", token, enc("allowance(address,address)", BUYER, spender))
    return x


def v2_phase2(token, pool, router, quote, amt, received, owner=None):
    P = Plan().block(first_state())
    v2_buy_block(P, token, router, quote, amt)
    common_after_buy(P, token, router, received)
    _, sell_p = v2_paths(token, quote)
    for tag, blk_over, ladder in (("s1", None, SELL1_FR), ("s2", {"number": "+%d" % DELAY_BLOCKS}, SELL2_FR)):
        P.block(None, blk_over)
        for k, (n, d) in enumerate(ladder):
            a = received * n // d
            pre = f"{tag}{k}"
            P.call(pre + "_bal_pre", token, enc("balanceOf(address)", BUYER))
            P.call(pre + "_quote", router, enc("getAmountsOut(uint256,address[])", a, sell_p))
            P.call(pre + "_eth_pre", H_BAL, a32(BUYER))
            P.call(pre, router, enc("swapExactTokensForETHSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)",
                                    a, 0, sell_p, BUYER, DEADLINE), gas=GAS)
            P.call(pre + "_eth_post", H_BAL, a32(BUYER))
            P.call(pre + "_bal_post", token, enc("balanceOf(address)", BUYER))
    return P


# --------------------------------------------------------------------------------------------- V3
def v3_swap_data(tin, tout, fee, recip, amt):
    return enc("exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))",
               (tin, tout, fee, recip, DEADLINE, amt, 0, 0))


def v3_meta_calls(P, token, pool, meta=True):
    P.call("pool_factory", pool, enc("factory()"))
    P.call("pool_token0", pool, enc("token0()"))
    P.call("pool_token1", pool, enc("token1()"))
    P.call("pool_fee", pool, enc("fee()"))
    P.call("pool_liquidity", pool, enc("liquidity()"))
    P.call("pool_slot0", pool, enc("slot0()"))
    P.call("pool_weth_bal", WETH, enc("balanceOf(address)", pool))
    if meta:
        token_meta_calls(P, token)


def v3_buy_block(P, token, fee, amt):
    P.call("buy_quote", V3_QUOTER, enc("quoteExactInputSingle(address,address,uint24,uint256,uint160)", WETH, token, fee, amt, 0))
    P.call("buy", V3_ROUTER, v3_swap_data(WETH, token, fee, BUYER, amt), value=amt, gas=GAS)
    P.call("buy_bal", token, enc("balanceOf(address)", BUYER))


def v3_phase1(token, pool, fee, amt, meta=True):
    P = Plan().block(first_state())
    v3_meta_calls(P, token, pool, meta)
    v3_buy_block(P, token, fee, amt)
    return P


def v3_phase2(token, pool, fee, amt, received):
    P = Plan().block(first_state())
    v3_buy_block(P, token, fee, amt)
    common_after_buy(P, token, V3_ROUTER, received)
    for tag, blk_over, ladder in (("s1", None, SELL1_FR), ("s2", {"number": "+%d" % DELAY_BLOCKS}, SELL2_FR)):
        P.block(None, blk_over)
        for k, (n, d) in enumerate(ladder):
            a = received * n // d
            pre = f"{tag}{k}"
            P.call(pre + "_bal_pre", token, enc("balanceOf(address)", BUYER))
            P.call(pre + "_quote", V3_QUOTER, enc("quoteExactInputSingle(address,address,uint24,uint256,uint160)", token, WETH, fee, a, 0))
            P.call(pre + "_eth_pre", WETH, enc("balanceOf(address)", BUYER))
            P.call(pre, V3_ROUTER, v3_swap_data(token, WETH, fee, BUYER, a), gas=GAS)
            P.call(pre + "_eth_post", WETH, enc("balanceOf(address)", BUYER))
            P.call(pre + "_bal_post", token, enc("balanceOf(address)", BUYER))
    return P


# --------------------------------------------------------------------------------------------- V1
def v1_meta_calls(P, token, pool, meta=True):
    P.call("pool_factory", pool, enc("factoryAddress()"))
    P.call("pool_token_addr", pool, enc("tokenAddress()"))
    P.call("pool_eth_bal", H_BAL, a32(pool))
    P.call("pool_tok_bal", token, enc("balanceOf(address)", pool))
    if meta:
        token_meta_calls(P, token)


def v1_buy_block(P, token, pool, amt):
    P.call("buy_quote", pool, enc("getEthToTokenInputPrice(uint256)", amt))
    P.call("buy", pool, enc("ethToTokenSwapInput(uint256,uint256)", 1, DEADLINE), value=amt, gas=GAS)
    P.call("buy_bal", token, enc("balanceOf(address)", BUYER))


def v1_phase1(token, pool, amt, meta=True):
    P = Plan().block(first_state())
    v1_meta_calls(P, token, pool, meta)
    v1_buy_block(P, token, pool, amt)
    return P


def v1_phase2(token, pool, amt, received):
    P = Plan().block(first_state())
    v1_buy_block(P, token, pool, amt)
    common_after_buy(P, token, pool, received)
    for tag, blk_over, ladder in (("s1", None, SELL1_FR), ("s2", {"number": "+%d" % DELAY_BLOCKS}, SELL2_FR)):
        P.block(None, blk_over)
        for k, (n, d) in enumerate(ladder):
            a = received * n // d
            pre = f"{tag}{k}"
            P.call(pre + "_bal_pre", token, enc("balanceOf(address)", BUYER))
            P.call(pre + "_quote", pool, enc("getTokenToEthInputPrice(uint256)", a))
            P.call(pre + "_eth_pre", H_BAL, a32(BUYER))
            P.call(pre, pool, enc("tokenToEthSwapInput(uint256,uint256,uint256)", a, 1, DEADLINE), gas=GAS)
            P.call(pre + "_eth_post", H_BAL, a32(BUYER))
            P.call(pre + "_bal_post", token, enc("balanceOf(address)", BUYER))
    return P


async def measure_quote_liquidity(ctx, rl, token, pool, block, pair, pi):
    """Optional C17 measurement at the historical parent state, without swaps.

    V4 uses active *virtual* reserves x=L/sqrt(P), y=L*sqrt(P), in
    raw currency units. L and sqrt(P) alone do not identify the aggregate
    deposited balances of concentrated positions. Never use the singleton
    balance as a pool reserve. The method name preserves that distinction.
    """
    row = dict(pair.get('extra') or {})
    dex = pair.get('dex') or pi.get('dex') or ''
    kind = pi.get('kind') or {'uniswap_v1':'v1','uniswap_v3':'v3','uniswap_v4':'v4'}.get(dex,'v2')
    quote = pi.get('quote')
    if not quote:
        t0,t1 = row.get('token0'),row.get('token1')
        quote = t1 if str(t0).lower()==token.lower() else t0
    if kind=='v1':
        quote=ZERO
    out={'kind':kind,'quote':quote,'quote_liquidity_block':int(block)}
    async def read(method, params, label):
        raw=await ctx.archive.request(method,params)
        rl.add('quote_liquidity:'+label,raw)
        if 'error' in raw['response']:
            raise ValueError(str(raw['response']['error']))
        return raw['response']['result']
    async def call(to,data,label):
        value=await read('eth_call',[{'to':to,'data':data},hex(block)],label)
        if not value or len(value)<66:
            raise ValueError('empty liquidity return:'+label)
        return value
    try:
        if kind=='v1':
            reserve=int(await read('eth_getBalance',[pool,hex(block)],'v1_eth_balance'),16)
            out['quote_liquidity_method']='v1_exchange_eth_balance'
        elif kind=='v2':
            reserve=pi.get('reserve_quote')
            if reserve is None:
                rr=await call(pool,enc('getReserves()'),'v2_reserves')
                t0=pi.get('token0') or row.get('token0')
                if not t0:
                    raise ValueError('unknown token ordering')
                i=1 if str(t0).lower()==token.lower() else 0
                reserve=int(rr[2+64*i:66+64*i],16)
            out['quote_liquidity_method']='v2_quote_reserve'
        elif kind=='v3':
            if not quote:
                raise ValueError('unknown V3 quote')
            reserve=int(await call(quote,enc('balanceOf(address)',pool),'v3_quote_balance'),16)
            out['quote_liquidity_method']='v3_pool_quote_balance'
        else:
            from abi import V4_STATE_VIEW
            pid=bytes.fromhex(pool[2:])
            slot,liq=await asyncio.gather(call(V4_STATE_VIEW,enc('getSlot0(bytes32)',pid),'v4_slot0'),
                                          call(V4_STATE_VIEW,enc('getLiquidity(bytes32)',pid),'v4_liquidity'))
            sqrt,L=int(slot[2:66],16),int(liq[2:66],16)
            out.update(sqrt_price_x96=str(sqrt),liquidity=str(L),
                       quote_liquidity_method='v4_active_virtual_quote_amount')
            # Slot0: sqrtPriceX96, tick, protocolFee, lpFee. Dynamic pool-key
            # flags are not fees; retain the actual current LP fee.
            if len(slot)>=258:
                out['current_lp_fee']=int(slot[194:258],16)
            if sqrt==0:
                reserve=0
            elif str(row.get('token0') or pi.get('token0')).lower()==str(quote).lower():
                reserve=L*(2**96)//sqrt
            else:
                reserve=L*sqrt//(2**96)
        out['reserve_quote']=int(reserve)
        if str(quote).lower() in (ZERO,WETH.lower()):
            equiv=int(reserve)
        else:
            refs=getattr(ctx,'quote_refs',{})
            ref=refs.get(str(quote).lower())
            if not ref or int(ref['block'])>block:
                raise ValueError('no_historical_weth_reference_pool')
            rr=await call(ref['pool_address'],enc('getReserves()'),'weth_quote_reference')
            r0,r1=int(rr[2:66],16),int(rr[66:130],16)
            weth,q=(r0,r1) if ref['token0'].lower()==WETH.lower() else (r1,r0)
            if weth<=0 or q<=0:
                raise ValueError('empty_historical_weth_reference_pool')
            equiv=int(reserve)*weth//q
            out['quote_conversion_reference_pool']=ref['pool_address']
        out['quote_liquidity_eth_equiv_wei']=str(equiv)
        out['quote_liquidity_status']='ok'
    except Exception as exc:
        out['quote_liquidity_status']='unprobeable'
        out['quote_liquidity_error']=repr(exc)[:500]
    return out
