"""The test battery: scripts executed by the harness. Each script is an isolated sequence of ops; the classifier
(classify.py) reads the labelled results. Order of ops inside a script matters only inside that script."""
from common import *

REG_RECIP = keccak(b"ERC777TokensRecipient")
REG_SENDER = keccak(b"ERC777TokensSender")
REG_TOKEN = keccak(b"ERC777Token")

BLOCKLIST_FUNCS = [
    "isBlacklisted(address)", "isBlackListed(address)", "getBlackListStatus(address)", "isBlocked(address)",
    "_isBlacklisted(address)", "blacklisted(address)", "isBlocklisted(address)", "blocklisted(address)",
    "blacklist(address)", "blacklists(address)", "blackList(address)", "isBlackList(address)", "_blacklist(address)", "isFrozen(address)",
    "frozen(address)", "isBanned(address)", "isBot(address)", "bots(address)", "_isBot(address)",
    "isSanctioned(address)", "isRestricted(address)", "blocked(address)", "isDenied(address)",
    "isBlacklist(address)", "isInBlacklist(address)",
]
PAUSE_FUNCS = ["paused()", "isPaused()", "stopped()", "isStopped()", "pausedPublic()", "pausedOwnerAdmin()",
               "transfersPaused()", "transferPaused()", "tradingPaused()", "isTransferPaused()", "pausedTransfers()",
               "emergencyStop()", "isLocked()", "locked()"]
REBASE_FUNCS = [
    ("sharesOf(address)", "H"), ("scaledBalanceOf(address)", "H"), ("gonsPerFragment()", None),
    ("_gonsPerFragment()", None), ("getSharesByPooledEth(uint256)", 10**18), ("getPooledEthByShares(uint256)", 10**18),
    ("getTotalShares()", None), ("totalShares()", None), ("index()", None), ("getRate()", None),
    ("pricePerShare()", None), ("convertToAssets(uint256)", 10**18), ("scaledTotalSupply()", None),
    ("_rTotal()", None), ("totalFees()", None), ("tokenFromReflection(uint256)", 10**18),
    ("isExcludedFromReward(address)", "H"), ("rebaseCount()", None), ("lastRebase()", None),
    ("getCurrentIndex()", None), ("liquidityIndex()", None), ("_totalGons()", None), ("TOTAL_GONS()", None),
]
FEE_LIMIT_FUNCS = [
    ("_taxFee()", None), ("taxFee()", None), ("_liquidityFee()", None), ("liquidityFee()", None), ("buyTax()", None),
    ("sellTax()", None), ("buyFee()", None), ("sellFee()", None), ("_buyTax()", None), ("_sellTax()", None),
    ("fee()", None), ("totalFee()", None), ("_maxTxAmount()", None), ("maxTxAmount()", None),
    ("maxTransactionAmount()", None), ("_maxTransactionAmount()", None), ("maxTransferAmount()", None),
    ("_maxWalletSize()", None), ("maxWalletAmount()", None), ("maxWallet()", None), ("_maxWalletAmount()", None),
    ("tradingOpen()", None), ("tradingEnabled()", None), ("tradingActive()", None), ("launched()", None),
    ("swapEnabled()", None), ("limitsInEffect()", None), ("transferDelayEnabled()", None),
    ("cooldownEnabled()", None), ("antiBotEnabled()", None), ("isExcludedFromFees(address)", "H"),
    ("isExcludedFromFee(address)", "H"), ("_isExcludedFromFee(address)", "H"), ("isTxLimitExempt(address)", "H"),
    ("transferFee()", None), ("burnFee()", None), ("basisPointsRate()", None), ("maximumFee()", None),
    ("feeRate()", None), ("feeParts()", None), ("getFeeFor(uint256)", 10**18), ("feePercent()", None),
    ("transferFeeRate()", None), ("_transferFee()", None),
]
OTHER_VIEWS = [
    ("DOMAIN_SEPARATOR()", None), ("nonces(address)", "H"), ("PERMIT_TYPEHASH()", None), ("version()", None),
    ("eip712Domain()", None), ("granularity()", None), ("defaultOperators()", None),
    ("supportsInterface(bytes4)", b"\xb0\x20\x2a\x11"),
    ("delegate()", None), ("target()", None), ("implementation()", None),
]


def _arg(sig, a):
    if a is None:
        return cd(sig)
    if a == "H":
        return cd(sig, H)
    return cd(sig, a)


def build_views(T):
    s = Script()
    s.v("decimals", T, cd("decimals()"))
    s.v("name", T, cd("name()"))
    s.v("symbol", T, cd("symbol()"))
    s.c("totalSupply", T, cd("totalSupply()"))
    s.c("balanceOf_H", T, cd("balanceOf(address)", H))
    s.c("balanceOf_R", T, cd("balanceOf(address)", R))
    s.c("allowance_H_SINK", T, cd("allowance(address,address)", H, SINK))
    s.v("NAME_upper", T, cd("NAME()"))
    s.v("SYMBOL_upper", T, cd("SYMBOL()"))
    for sig, a in OTHER_VIEWS:
        s.v("v:" + sig, T, _arg(sig, a))
    for sig in PAUSE_FUNCS:
        s.v("pause:" + sig, T, cd(sig))
    for sig in BLOCKLIST_FUNCS:
        s.v("bl_H:" + sig, T, cd(sig, H))
        s.v("bl_S:" + sig, T, cd(sig, SANCTIONED[0]))
    for sig, a in REBASE_FUNCS:
        s.v("rb:" + sig, T, _arg(sig, a))
    for sig, a in FEE_LIMIT_FUNCS:
        s.v("fl:" + sig, T, _arg(sig, a))
    s.v("erc1820_token", ERC1820, cd("getInterfaceImplementer(address,bytes32)", T, REG_TOKEN))
    return s


def choose_amounts(ts, dec, B):
    """transfer amount: 10**decimals (1 token), at least 10_000 base units (so a few-bps fee is visible), but never more
    than 1% of totalSupply (max-tx limits on memecoins) nor 10% of the funded balance."""
    d = dec if isinstance(dec, int) and 0 <= dec <= 30 else 18
    a = max(10 ** d, 10 ** 4)
    if ts:
        a = min(a, max(ts // 100, 1))
    a = min(a, max(B // 10, 1))
    return max(a, 1)


def build_scripts(T, a, B, ts):
    """-> OrderedDict name -> Script"""
    sc = {}

    def new(name):
        s = Script()
        sc[name] = s
        return s

    # balance / supply / allowance reads use the full call gas cap (not the 100k view cap): reflection tokens loop over their
    # excluded-address list with cold SLOADs on the first read (SafeMoon V1: > 100k gas). Only the *views* script, which calls
    # unknown selectors into possibly-payable fallbacks, needs the small cap.
    bal = lambda s, l, x: s.c(l, T, cd("balanceOf(address)", x))
    tsup = lambda s, l: s.c(l, T, cd("totalSupply()"))
    alw = lambda s, l, o, sp: s.c(l, T, cd("allowance(address,address)", o, sp))
    tr = lambda s, l, to, amt: s.c(l, T, cd("transfer(address,uint256)", to, amt))
    ap = lambda s, l, sp, amt: s.c(l, T, cd("approve(address,uint256)", sp, amt))
    tfs = lambda s, l, fr, to, amt: s.sc(l, T, cd("transferFrom(address,address,uint256)", fr, to, amt))

    sc["views"] = build_views(T)

    s = new("transfer")
    bal(s, "bal_h0", H); bal(s, "bal_r0", R); bal(s, "bal_t0", T); tsup(s, "ts0")
    tr(s, "tr", R, a)
    bal(s, "bal_h1", H); bal(s, "bal_r1", R); bal(s, "bal_t1", T); tsup(s, "ts1")

    s = new("transfer_over")
    bal(s, "bal_h0", H)
    over = B + max(B // 100, 1) + 1
    tr(s, "tr", R, over)
    bal(s, "bal_h1", H); bal(s, "bal_r1", R)

    s = new("transfer_zero")
    bal(s, "bal_h0", H); tr(s, "tr", R, 0); bal(s, "bal_h1", H)

    s = new("transfer_self")
    bal(s, "bal_h0", H); tsup(s, "ts0"); tr(s, "tr", H, a); bal(s, "bal_h1", H); tsup(s, "ts1")

    s = new("transfer_zero_addr")
    bal(s, "bal_h0", H); tsup(s, "ts0"); tr(s, "tr", ZERO, a); bal(s, "bal_h1", H); tsup(s, "ts1")

    s = new("transfer_dead")
    bal(s, "bal_h0", H); tsup(s, "ts0"); tr(s, "tr", DEAD, a); bal(s, "bal_h1", H); tsup(s, "ts1")
    bal(s, "bal_d1", DEAD)

    s = new("transfer_token")
    bal(s, "bal_h0", H); bal(s, "bal_t0", T); tr(s, "tr", T, a); bal(s, "bal_h1", H); bal(s, "bal_t1", T)

    frac = ts // 20 if ts else B // 2
    frac = max(min(frac, B), 1)
    s = new("big_frac")
    bal(s, "bal_h0", H); tr(s, "tr", R, frac); bal(s, "bal_h1", H); bal(s, "bal_r1", R)

    s = new("big_96")
    bal(s, "bal_h0", H); tr(s, "tr", R, 1 << 96); bal(s, "bal_h1", H)

    s = new("big_max")
    bal(s, "bal_h0", H); tr(s, "tr", R, UINT_MAX); bal(s, "bal_h1", H); bal(s, "bal_r1", R)

    s = new("big_all")
    bal(s, "bal_h0", H); bal(s, "bal_r0", R); tr(s, "tr", R, B); bal(s, "bal_h1", H); bal(s, "bal_r1", R)

    s = new("transfer_twice")
    bal(s, "bal_h0", H); tr(s, "tr1", R, a); tr(s, "tr2", R, a); bal(s, "bal_h1", H); bal(s, "bal_r1", R)
    tr(s, "tr3", fake_addr("recipient-eoa-2"), a)

    s = new("to_sink")
    s.c("settoken", SINK, cd("setToken(address)", T))
    bal(s, "bal_h0", H); bal(s, "bal_s0", SINK)
    tr(s, "tr", SINK, a)
    bal(s, "bal_h1", H); bal(s, "bal_s1", SINK)
    s.v("sink_calls", SINK, cd("calls()")); s.v("sink_sels", SINK, cd("sels()"))
    s.v("sink_bal_at_call", SINK, cd("balAtCall0()"))

    s = new("to_reject")
    bal(s, "bal_h0", H); tr(s, "tr", REJ, a); bal(s, "bal_h1", H); bal(s, "bal_x1", REJ)

    s = new("hooks")
    s.c("settoken", SINK, cd("setToken(address)", T))
    s.sc("reg_sink_recipient", ERC1820, cd("setInterfaceImplementer(address,bytes32,address)", SINK, REG_RECIP, SINK))
    s.c("reg_h_sender", ERC1820, cd("setInterfaceImplementer(address,bytes32,address)", H, REG_SENDER, H))
    s.c("reg_h_recipient", ERC1820, cd("setInterfaceImplementer(address,bytes32,address)", H, REG_RECIP, H))
    bal(s, "bal_h0", H); bal(s, "bal_s0", SINK)
    tr(s, "tr", SINK, a)
    bal(s, "bal_h1", H); bal(s, "bal_s1", SINK)
    s.v("sink_calls", SINK, cd("calls()")); s.v("sink_sels", SINK, cd("sels()"))
    s.v("sink_bal_at_call", SINK, cd("balAtCall0()"))
    s.v("h_calls", H, cd("hookCount()")); s.v("h_sels", H, cd("hookSels()")); s.v("h_bal_at_call", H, cd("hookBal0()"))
    s.v("reg_impl_sender", ERC1820, cd("getInterfaceImplementer(address,bytes32)", H, REG_SENDER))

    # approve family (spender = SINK)
    s = new("approve_basic")
    alw(s, "alw0", H, SINK); ap(s, "ap", SINK, a); alw(s, "alw1", H, SINK)
    s = new("approve_nznz")
    ap(s, "ap1", SINK, a); alw(s, "alw1", H, SINK); ap(s, "ap2", SINK, a + 1); alw(s, "alw2", H, SINK)
    ap(s, "ap3", SINK, 0); alw(s, "alw3", H, SINK)
    s = new("approve_max")
    ap(s, "ap", SINK, UINT_MAX); alw(s, "alw1", H, SINK)
    s = new("approve_zero_addr")
    ap(s, "ap", ZERO, a); alw(s, "alw1", H, ZERO)
    s = new("approve_zero")
    ap(s, "ap", SINK, 0); alw(s, "alw1", H, SINK)
    s = new("inc_dec")
    s.c("inc", T, cd("increaseAllowance(address,uint256)", SINK, a)); alw(s, "alw1", H, SINK)
    s.c("dec", T, cd("decreaseAllowance(address,uint256)", SINK, a // 2)); alw(s, "alw2", H, SINK)
    s.c("inc_old", T, cd("increaseApproval(address,uint256)", SINK, a)); alw(s, "alw3", H, SINK)
    s.c("dec_old", T, cd("decreaseApproval(address,uint256)", SINK, a // 2)); alw(s, "alw4", H, SINK)

    # transferFrom family: owner = H, spender = SINK
    s = new("tf_noallow")
    bal(s, "bal_h0", H); bal(s, "bal_r0", R); tfs(s, "tf", H, R, a); bal(s, "bal_h1", H); bal(s, "bal_r1", R)
    s = new("tf_allow")
    bal(s, "bal_h0", H); bal(s, "bal_r0", R); ap(s, "ap", SINK, 2 * a); tfs(s, "tf", H, R, a)
    alw(s, "alw1", H, SINK); bal(s, "bal_h1", H); bal(s, "bal_r1", R)
    s = new("tf_max")
    bal(s, "bal_h0", H); bal(s, "bal_r0", R); ap(s, "ap", SINK, UINT_MAX); tfs(s, "tf", H, R, a)
    alw(s, "alw1", H, SINK); bal(s, "bal_h1", H); bal(s, "bal_r1", R)
    s = new("tf_exceed")
    ap(s, "ap", SINK, a // 2); tfs(s, "tf", H, R, a); alw(s, "alw1", H, SINK); bal(s, "bal_r1", R)
    s = new("tf_self")
    bal(s, "bal_h0", H); bal(s, "bal_r0", R)
    s.c("tf", T, cd("transferFrom(address,address,uint256)", H, R, a))
    bal(s, "bal_h1", H); bal(s, "bal_r1", R)
    s = new("tf_zero")
    tfs(s, "tf", H, R, 0)
    s = new("tf_to_self_spender")   # spender moves tokens H -> H (self) after approve
    ap(s, "ap", SINK, 2 * a); bal(s, "bal_h0", H); tfs(s, "tf", H, H, a); bal(s, "bal_h1", H); alw(s, "alw1", H, SINK)

    for i, tgt in enumerate(SANCTIONED):
        s = new(f"sanctioned_{i}")
        bal(s, "bal_h0", H); tr(s, "tr", tgt, a); bal(s, "bal_h1", H)

    return sc
