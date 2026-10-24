"""Minimal owner / admin-function signals from runtime bytecode + owner() (W3 builds the full classifier)."""
from eth_utils import keccak
from abi import ZERO, DEAD

# category -> candidate function signatures (selector hit in the dispatcher == function exists)
SIGS = {
    "mint": ["mint(address,uint256)", "mint(uint256)", "mint(address)", "mint(address,uint256,bytes)", "_mint(address,uint256)",
             "ownerMint(address,uint256)", "mintTo(address,uint256)", "issue(uint256)", "issue(address,uint256)", "mintTokens(address,uint256)"],
    "tax_setter": ["setFee(uint256)", "setFees(uint256,uint256)", "setTax(uint256)", "setTaxes(uint256,uint256)", "setBuyFee(uint256)",
                   "setSellFee(uint256)", "setBuyTax(uint256)", "setSellTax(uint256)", "setBuyFees(uint256,uint256,uint256)",
                   "setSellFees(uint256,uint256,uint256)", "updateFees(uint256,uint256)", "updateFee(uint256)", "updateBuyFees(uint256,uint256,uint256)",
                   "updateSellFees(uint256,uint256,uint256)", "setTaxFee(uint256)", "setTaxFeePercent(uint256)", "setLiquidityFeePercent(uint256)",
                   "setFeePercent(uint256)", "setTransferFee(uint256)", "setTransactionFee(uint256)", "changeFees(uint256,uint256)",
                   "changeTax(uint256)", "setMarketingFee(uint256)", "setDevFee(uint256)", "setTotalFees(uint256)", "setFeeRate(uint256)",
                   "setBuyTaxes(uint256,uint256,uint256)", "setSellTaxes(uint256,uint256,uint256)", "setFeeBps(uint256)", "adjustFees(uint256,uint256)",
                   "setRates(uint256,uint256)", "setBurnFee(uint256)", "setSwapFee(uint256)", "updateTaxes(uint256,uint256)", "setFeesPercent(uint256,uint256)",
                   "_setTaxFee(uint256)", "setMaxFee(uint256)", "updateBuyFee(uint256)", "updateSellFee(uint256)"],
    "blacklist": ["blacklist(address)", "blacklist(address,bool)", "addBlacklist(address)", "addToBlacklist(address)", "setBlacklist(address,bool)",
                  "blacklistAddress(address,bool)", "blacklistAddress(address)", "addBlackList(address)", "removeBlackList(address)",
                  "removeFromBlacklist(address)", "isBlacklisted(address)", "isBlackListed(address)", "setBots(address[])", "addBots(address[])",
                  "delBots(address[])", "setBot(address,bool)", "blockAddress(address)", "blockAddresses(address[],bool)", "setBlacklisted(address,bool)",
                  "blacklistMultiple(address[],bool)", "bots(address)", "isBot(address)", "addBot(address)", "removeBot(address)", "ban(address)", "unban(address)",
                  "setBlackList(address,bool)", "blackList(address)", "setIsBlacklisted(address,bool)", "antiBot(address)", "setBlocklist(address,bool)",
                  "addBotWallet(address)", "setMultipleBots(address[],bool)", "manualBlacklist(address)", "freeze(address)", "freezeAccount(address,bool)",
                  "lockAddress(address)", "setAntiBot(bool)", "setBlacklistEnabled(bool)", "isBlocked(address)", "addToBlocklist(address)"],
    "limits": ["setMaxTx(uint256)", "setMaxTxAmount(uint256)", "setMaxTransactionAmount(uint256)", "setMaxWallet(uint256)", "setMaxWalletAmount(uint256)",
               "setMaxWalletPercent(uint256)", "setMaxTxPercent(uint256)", "removeLimits()", "setLimits(bool)", "setMaxSell(uint256)", "setMaxBuy(uint256)",
               "updateMaxTxnAmount(uint256)", "updateMaxWalletAmount(uint256)", "updateMaxTransactionAmount(uint256)", "setMaxTxnAmount(uint256)",
               "_setMaxTxAmount(uint256)", "setMaxHolding(uint256)", "setLimitsInEffect(bool)", "setMaxTransfer(uint256)", "setTransferLimit(uint256)",
               "setCooldown(uint256)", "setCooldownEnabled(bool)", "setSellLimit(uint256)", "setMaxSellAmount(uint256)", "setMaxBuyAmount(uint256)",
               "setMaxWalletSize(uint256)", "setMaxWalletToken(uint256)"],
    "trading_switch": ["enableTrading()", "openTrading()", "setTradingEnabled(bool)", "setTrading(bool)", "tradingOpen()", "startTrading()", "enableTrading(bool)",
                       "setTradingOpen(bool)", "launch()", "setSwapEnabled(bool)", "setLaunched(bool)", "tradingEnabled()", "setTradeEnabled(bool)",
                       "activateTrading()", "setCanTrade(bool)", "allowTrading()", "setTradingStatus(bool)", "toggleTrading()", "disableTrading()",
                       "setTradingStart(uint256)", "setLaunchTime(uint256)", "openTrade()", "beginTrading()", "enableTrade()", "setPublicTrade(bool)",
                       "setSwapAndLiquifyEnabled(bool)", "setAutoSwapBack(bool)", "go()", "setStart(bool)"],
    "pause": ["pause()", "unpause()", "paused()", "setPaused(bool)", "pauseTrading()", "unpauseTrading()", "setPause(bool)", "togglePause()"],
    "fee_exempt": ["excludeFromFees(address,bool)", "excludeFromFee(address)", "includeInFee(address)", "excludeFromReward(address)",
                   "setIsFeeExempt(address,bool)", "setFeeExempt(address,bool)", "excludeFromFees(address)", "setExcludedFromFee(address,bool)",
                   "setIsExcludedFromFee(address,bool)", "excludeAccount(address)", "setWhitelist(address,bool)", "addToWhitelist(address)",
                   "whitelistAddress(address,bool)", "setExcludeFromFees(address,bool)", "excludeFromMaxTransaction(address,bool)",
                   "setIsExcludedFromLimits(address,bool)", "setTxLimitExempt(address,bool)", "setIsTxLimitExempt(address,bool)", "setMaxTxExempt(address,bool)"],
    "rescue_drain": ["withdraw()", "withdrawAll()", "emergencyWithdraw()", "rescueTokens(address,uint256)", "rescueETH()", "clearStuckBalance()",
                     "clearStuckBalance(uint256)", "sweep(address)", "sweepTokens(address)", "recoverERC20(address,uint256)", "manualSend()",
                     "manualsend()", "manualswap()", "manualSwap()", "withdrawStuckETH()", "withdrawStuckTokens(address)", "rescueStuckTokens(address)",
                     "withdrawTokens(address)", "withdrawETH()", "transferForeignToken(address,address)", "stuckToken(address)"],
    "pair_setter": ["setPair(address)", "setPairAddress(address)", "setAutomatedMarketMakerPair(address,bool)", "setUniswapPair(address)",
                    "updateUniswapV2Router(address)", "setRouter(address)", "setRouterAddress(address)", "setLpPair(address,bool)",
                    "setAMM(address,bool)", "addPair(address)", "setPancakePair(address)"],
    "upgrade_proxy": ["upgradeTo(address)", "upgradeToAndCall(address,bytes)", "implementation()", "proxiableUUID()", "admin()", "changeAdmin(address)",
                      "setImplementation(address)"],
}
SEL = {}
for cat, sigs in SIGS.items():
    for s in sigs:
        SEL[keccak(text=s)[:4].hex()] = (cat, s)
OWNERSHIP = {keccak(text=s)[:4].hex(): s for s in ["owner()", "getOwner()", "renounceOwnership()", "transferOwnership(address)",
                                                    "owner_()", "_owner()", "newOwner()", "acceptOwnership()"]}
RISKY = {"mint", "tax_setter", "blacklist", "limits", "trading_switch", "pause", "pair_setter", "upgrade_proxy"}


def selectors(code_hex):
    """PUSH4 <sel> followed (within 3 bytes) by EQ: dispatcher selectors.  Returns (set_of_hex, flags)."""
    b = bytes.fromhex(code_hex[2:] if code_hex.startswith("0x") else code_hex)
    out, i, n = set(), 0, len(b)
    while i < n:
        op = b[i]
        if op == 0x63 and i + 5 <= n:
            s = b[i + 1:i + 5]
            if 0x14 in b[i + 5:i + 8]:
                out.add(s.hex())
            i += 5
        elif 0x60 <= op <= 0x7f:
            i += 1 + (op - 0x5f)
        else:
            i += 1
    flags = []
    if b[:10] == bytes.fromhex("363d3d373d3d3d363d") or (n < 120 and b"\xf4" in b):
        flags.append("proxy_like")
    elif b"\xf4" in b and len(out) <= 3:
        flags.append("proxy_like")
    return out, flags


def analyse(meta):
    """meta from pipeline.meta_from (+ owner_code_len)."""
    code = meta.get("code_hex") or "0x"
    out = {"code_len": meta.get("code_len")}
    if len(code) <= 2:
        out["no_code"] = True
        return out
    sels, flags = selectors(code)
    out["code_keccak"] = "0x" + keccak(bytes.fromhex(code[2:])).hex()
    out["n_selectors"] = len(sels)
    out["flags"] = flags
    cats = {}
    for s in sorted(sels):
        if s in SEL:
            cats.setdefault(SEL[s][0], []).append(SEL[s][1])
    out["functions"] = cats
    out["has_owner_fn"] = any(OWNERSHIP.get(s, "").startswith(("owner", "getOwner", "_owner", "owner_")) for s in sels)
    out["has_renounce"] = any(OWNERSHIP.get(s) == "renounceOwnership()" for s in sels)
    owner = meta.get("owner") or meta.get("getOwner")
    if owner is None:
        st = "none" if not out["has_owner_fn"] else "unknown"
    elif owner.lower() in (ZERO.lower(), DEAD.lower()):
        st = "renounced"
    else:
        cl = meta.get("owner_code_len")
        st = "live_eoa" if cl == 0 else ("live_contract" if cl else "live")
    out["owner"] = owner
    out["owner_state"] = st
    out["risky_categories"] = sorted(c for c in cats if c in RISKY)
    return out
