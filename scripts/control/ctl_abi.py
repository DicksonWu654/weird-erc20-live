"""Selectors, slots, decoders and bytecode scanning for the control classifier."""
import re

from eth_utils import keccak


def k(s):
    return keccak(text=s)


def sel(sig):
    return "0x" + k(sig)[:4].hex()


def slot_of(s, minus1=False):
    n = int.from_bytes(k(s), "big") - (1 if minus1 else 0)
    return "0x" + n.to_bytes(32, "big").hex()


# ---- storage slots ------------------------------------------------------------------------------------------------
SLOTS = {
    "eip1967_impl": slot_of("eip1967.proxy.implementation", True),
    "eip1967_admin": slot_of("eip1967.proxy.admin", True),
    "eip1967_beacon": slot_of("eip1967.proxy.beacon", True),
    "eip1822_proxiable": slot_of("PROXIABLE"),
    "zos_impl": slot_of("org.zeppelinos.proxy.implementation"),
    "zos_admin": slot_of("org.zeppelinos.proxy.admin"),
    "slot0": "0x" + "00" * 32,
}
assert SLOTS["eip1967_impl"] == "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
assert SLOTS["eip1967_admin"] == "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
assert SLOTS["eip1967_beacon"] == "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
assert SLOTS["zos_impl"] == "0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3"

# ---- view functions (no args) -------------------------------------------------------------------------------------
VIEWS = {n: sel(n + "()") for n in [
    "symbol", "decimals", "totalSupply", "name", "implementation", "proxyType", "kernel", "facetAddresses", "owner",
    "getOwner", "_owner", "admin", "governance", "governor", "gov", "authority", "pendingOwner", "paused", "target",
    "getImplementation", "masterMinter", "minter", "supplyController", "supplyControl", "pauser", "blacklister",
    "blocklister", "assetProtectionRole", "rescuer", "feeRecipient", "feeCollector", "feeManager", "feeSetter",
    "taxWallet", "_taxWallet", "marketingWallet", "treasury", "controller", "manager", "operator", "timelock",
    "getThreshold", "getOwners", "VERSION", "required", "getMinDelay", "delay", "getAdmin", "getDelay", "votingPeriod",
    "COUNTING_MODE", "proposalCount", "quorumVotes", "hat", "acl", "voteTime", "supportRequiredPct", "token",
    "proxiableUUID", "appId", "pendingAdmin", "comptrollerImplementation", "getOwnerAddress", "creator", "deployer",
    "burner", "minterAdmin", "mintAdmin", "pauseAdmin", "getPauser", "blacklistAdmin", "freezer", "complianceOfficer",
    "lister", "proposalThreshold", "guardian", "pendingGovernance", "pendingAdmin", "ownerAddress", "isOwner",
    "mintingAdmin", "minterRole", "tokenAdmin", "depositor", "bridge", "l2Bridge", "l1Token", "crossChainAddress",
    "getPayloadsCount", "proxyOwner", "BRIDGE", "l2Gateway", "gateway", "getRoleAdmin", "DEFAULT_ADMIN_ROLE", "MINTER_ROLE", "PAUSER_ROLE", "fiatTokenProxy", "currentTaxRate", "deprecated", "upgradedAddress",
]}


def view(n):
    return VIEWS[n]


SEL = {  # function selectors with args
    "hasRole": sel("hasRole(bytes32,address)"),
    "getRoleMember": sel("getRoleMember(bytes32,uint256)"),
    "getRoleMemberCount": sel("getRoleMemberCount(bytes32)"),
    "wards": sel("wards(address)"),
    "getPermissionManager": sel("getPermissionManager(address,bytes32)"),
    "getProxyAdmin": sel("getProxyAdmin(address)"),
    "isMinter": sel("isMinter(address)"),
    "minters": sel("minters(address)"),
    "isBlacklisted": sel("isBlacklisted(address)"),
    "facetAddress": sel("facetAddress(bytes4)"),
    "getApp": sel("getApp(bytes32)"),
}

# ---- selector hints: capability labels from the PUSH4 set of token (+impl) bytecode (static; may include false pos.) ----
CAPABILITY_SIGS = {
    "mint": ["mint(address,uint256)", "mint(uint256)", "mint(address,uint256,bytes)", "issue(uint256)",
             "mintTo(address,uint256)", "mintFor(address,uint256)", "adminMint(address,uint256)", "mint(address)",
             "mintShares(address,uint256)", "mintExternalShares(address,uint256)", "minter_mint(address,uint256)", "generateTokens(address,uint256)", "mint(uint256,address)",
             "mint(address,uint256,uint256)", "mintTokens(address,uint256)", "issue(address,uint256)", "pool_mint(address,uint256)"],
    "burn_from_others": ["minter_burn_from(address,uint256)", "burn(address,uint256)", "burnFrom(address,uint256)", "destroyBlackFunds(address)",
                         "wipeFrozenAddress(address)", "confiscate(address,uint256)", "forceBurn(address,uint256)", "pool_burn_from(address,uint256)"],
    "pause": ["pause()", "unpause()", "setPaused(bool)", "pauseTrading()", "pauseTransfers()"],
    "blacklist": ["blacklist(address)", "unBlacklist(address)", "isBlacklisted(address)", "addBlackList(address)",
                  "removeBlackList(address)", "getBlackListStatus(address)", "isBlackListed(address)",
                  "addToBlacklist(address)", "removeFromBlacklist(address)", "setBlacklist(address,bool)",
                  "blacklistAddress(address)", "blockAddress(address)", "blocklist(address)", "unBlocklist(address)",
                  "isBlocklisted(address)", "setBlackList(address,bool)", "isBlackList(address)",
                  "addBlacklist(address)", "setBlacklistedAddress(address,bool)", "freeze(address)",
                  "freezeAccount(address,bool)", "isFrozen(address)", "setFrozen(address,bool)",
                  "bots(address)", "setBots(address[])", "delBot(address)", "addBots(address[])",
                  "blacklisted(address)", "_isBlacklisted(address)", "blacklist(address,bool)",
                  "setBlacklisted(address,bool)", "updateBlacklist(address,bool)", "banAddress(address)",
                  "setBlockList(address,bool)", "isBlackListed(address)"],
    "fee_or_tax_setter": ["setFee(uint256)", "setTax(uint256)", "setFees(uint256,uint256)", "setBuyFee(uint256)",
                          "setSellFee(uint256)", "setTaxFee(uint256)", "setMaxTxAmount(uint256)",
                          "setMaxTxPercent(uint256)", "setSwapFee(uint256)", "setTransferFee(uint256)",
                          "setFeeRate(uint256)", "updateFees(uint256,uint256)", "setBuyTax(uint256)",
                          "setSellTax(uint256)", "setTaxes(uint256,uint256)", "setFeePercent(uint256)",
                          "updateBuyFees(uint256,uint256,uint256)", "setBasisPointsRate(uint256,uint256)",
                          "setParams(uint256,uint256)", "setTransferTax(uint256)"],
    "trading_gate": ["enableTrading()", "openTrading()", "setTradingEnabled(bool)", "startTrading()",
                     "tradingOpen()", "setTrading(bool)", "setRule(bool,address,uint256,uint256)", "setLimits(bool)", "removeLimits()", "launch()", "setSwapEnabled(bool)", "tradingEnabled()"],
    "upgrade": ["upgradeTo(address)", "upgradeToAndCall(address,bytes)", "upgrade(address)", "setImplementation(address)", "deprecate(address)"],
    "ownership_ops": ["renounceOwnership()", "transferOwnership(address)", "acceptOwnership()"],
    "rescue_tokens": ["rescueERC20(address,address,uint256)", "rescueTokens(address,uint256)",
                      "rescueFunds(address,uint256)", "sweep(address)", "withdrawStuckTokens(address)",
                      "recoverERC20(address,uint256)", "rescue(address,address,uint256)",
                      "claimStuckTokens(address)", "recoverToken(address,uint256)"],
    "permit": ["permit(address,address,uint256,uint256,uint8,bytes32,bytes32)", "DOMAIN_SEPARATOR()"],
    "role_based_access": ["hasRole(bytes32,address)", "grantRole(bytes32,address)"],
    "ds_auth": ["setAuthority(address)", "setOwner(address)"],
    "wards": ["rely(address)", "deny(address)", "wards(address)"],
}
CAP_BY_SEL = {}
for cap, sigs in CAPABILITY_SIGS.items():
    for sg in sigs:
        CAP_BY_SEL.setdefault(sel(sg), set()).add(cap)
# evidence strength: a few labels are weak on their own (bare DOMAIN_SEPARATOR, hasRole)
WEAK_CAPS = {"permit", "role_based_access", "ownership_ops"}


def capabilities(selectors):
    caps = {}
    for s in selectors:
        for c in CAP_BY_SEL.get(s, ()):
            caps.setdefault(c, []).append(s)
    return caps


# ---- known AccessControl roles --------------------------------------------------------------------------------------
ROLE_NAMES = ["ADMIN_ROLE", "MINTER_ROLE", "MINTER_ADMIN_ROLE", "BURNER_ROLE", "PAUSER_ROLE", "UNPAUSER_ROLE",
              "UPGRADER_ROLE", "UPGRADE_ROLE", "BLACKLISTER_ROLE", "BLACKLIST_ROLE", "BLOCKLISTER_ROLE",
              "FREEZER_ROLE", "WHITELISTER_ROLE", "WHITELIST_ROLE", "RESCUER_ROLE", "OPERATOR_ROLE", "MANAGER_ROLE",
              "GOVERNANCE_ROLE", "GOVERNOR_ROLE", "CONTROLLER_ROLE", "BRIDGE_ROLE", "DEPOSITOR_ROLE",
              "PREDICATE_ROLE", "EXECUTOR_ROLE", "PROPOSER_ROLE", "CANCELLER_ROLE", "TIMELOCK_ADMIN_ROLE",
              "SNAPSHOT_ROLE", "MINT_ROLE", "BURN_ROLE", "PAUSE_ROLE", "ISSUER_ROLE", "FEE_ROLE", "FEE_SETTER_ROLE",
              "SUPPLY_CONTROLLER_ROLE", "KEEPER_ROLE", "GUARDIAN_ROLE", "SECURITY_COUNCIL_ROLE", "ROLE_MANAGER_ROLE", "ASSET_PROTECTION_ROLE", "LAW_ENFORCEMENT_ROLE",
              "WIPER_ROLE", "COMPLIANCE_ROLE", "SUPPLY_CONTROLLER_MANAGER_ROLE", "BETA_DELEGATE_WHITELISTER_ROLE"]
ROLE_HASH = {"0x" + "00" * 32: "DEFAULT_ADMIN_ROLE"}
for _n in ROLE_NAMES:
    ROLE_HASH["0x" + k(_n).hex()] = _n

# canonical bucket for the output `roles` dict
ROLE_BUCKET = {
    "DEFAULT_ADMIN_ROLE": "default_admin", "ADMIN_ROLE": "admin", "MINTER_ROLE": "minter", "MINT_ROLE": "minter",
    "MINTER_ADMIN_ROLE": "minter_admin", "SUPPLY_CONTROLLER_ROLE": "minter", "ISSUER_ROLE": "minter",
    "BURNER_ROLE": "burner", "BURN_ROLE": "burner", "PAUSER_ROLE": "pauser", "PAUSE_ROLE": "pauser",
    "UNPAUSER_ROLE": "pauser", "UPGRADER_ROLE": "upgrader", "UPGRADE_ROLE": "upgrader",
    "BLACKLISTER_ROLE": "blacklister", "BLACKLIST_ROLE": "blacklister", "BLOCKLISTER_ROLE": "blacklister",
    "FREEZER_ROLE": "blacklister", "ASSET_PROTECTION_ROLE": "blacklister", "COMPLIANCE_ROLE": "blacklister",
    "WIPER_ROLE": "blacklister", "RESCUER_ROLE": "rescuer", "FEE_ROLE": "fee_setter", "FEE_SETTER_ROLE": "fee_setter",
}

# named single-address getter -> role bucket
GETTER_BUCKET = {
    "masterMinter": "minter_admin", "minter": "minter", "supplyController": "minter", "supplyControl": "minter",
    "mintingAdmin": "minter_admin", "minterAdmin": "minter_admin", "mintAdmin": "minter_admin", "minterRole": "minter",
    "pauser": "pauser", "pauseAdmin": "pauser", "getPauser": "pauser",
    "blacklister": "blacklister", "blocklister": "blacklister", "assetProtectionRole": "blacklister",
    "blacklistAdmin": "blacklister", "freezer": "blacklister", "complianceOfficer": "blacklister",
    "rescuer": "rescuer", "burner": "burner",
    "feeRecipient": "fee_recipient", "feeCollector": "fee_recipient", "feeManager": "fee_setter",
    "feeSetter": "fee_setter", "taxWallet": "fee_recipient", "_taxWallet": "fee_recipient",
    "marketingWallet": "fee_recipient", "treasury": "fee_recipient",
    "guardian": "guardian", "controller": "controller", "manager": "manager", "operator": "operator",
    "lister": "lister", "tokenAdmin": "admin",
    "bridge": "bridge", "BRIDGE": "bridge", "l2Bridge": "bridge", "l2Gateway": "bridge", "gateway": "bridge",
}
# getters that name the owner / admin authority
OWNER_GETTERS = ["owner", "getOwner", "_owner", "getOwnerAddress", "ownerAddress", "admin", "getAdmin", "governance",
                 "governor", "gov", "authority"]

PRIV_CAPS = {"mint", "burn_from_others", "pause", "blacklist", "fee_or_tax_setter", "trading_gate", "upgrade",
             "rescue_tokens", "ds_auth", "wards"}


# ---- decoders -----------------------------------------------------------------------------------------------------
ZERO = "0x" + "00" * 20
DEAD = "0x000000000000000000000000000000000000dead"


def dec_addr(h):
    """Decode a 32-byte ABI word as an address, or None when it is not address-shaped."""
    if not isinstance(h, str) or len(h) < 66:
        return None
    w = h[2:66]
    if w[:24] != "0" * 24:
        return None
    return "0x" + w[24:].lower()


def dec_uint(h):
    if not isinstance(h, str) or len(h) < 66:
        return None
    return int(h[2:66], 16)


def dec_bool(h):
    v = dec_uint(h)
    return None if v is None or v > 1 else bool(v)


def dec_str(h):
    if not isinstance(h, str) or len(h) < 66:
        return None
    try:
        b = bytes.fromhex(h[2:])
        if len(b) == 32:  # bytes32 symbol (MKR style)
            return b.rstrip(b"\x00").decode("utf8", "replace")
        off = int.from_bytes(b[:32], "big")
        ln = int.from_bytes(b[off:off + 32], "big")
        if ln > 4096:
            return None
        return b[off + 32:off + 32 + ln].decode("utf8", "replace")
    except Exception:  # noqa: BLE001
        return None


def dec_addr_array(h, limit=64):
    if not isinstance(h, str) or len(h) < 130:
        return None
    try:
        b = bytes.fromhex(h[2:])
        off = int.from_bytes(b[:32], "big")
        n = int.from_bytes(b[off:off + 32], "big")
        if n > 5000 or off + 32 + 32 * n > len(b):
            return None
        out = []
        for i in range(min(n, limit)):
            w = b[off + 32 + 32 * i: off + 64 + 32 * i]
            out.append("0x" + w[12:].hex())
        return {"count": n, "items": out}
    except Exception:  # noqa: BLE001
        return None


def pad_addr(a):
    return a.lower().replace("0x", "").rjust(64, "0")


def is_zeroish(a):
    return a is not None and (a == ZERO)


def is_dead(a):
    return a is not None and (a == DEAD or a in ("0xdead000000000000000000000000000000000000",
                                                "0x000000000000000000000000000000000000dead".lower()))


# ---- bytecode ---------------------------------------------------------------------------------------------------------
def scan_code(code_hex):
    """Walk opcodes (skipping PUSH data). Returns dict(selectors:set[str], delegatecall:bool, selfdestruct:bool, size)."""
    if not code_hex or code_hex == "0x":
        return {"selectors": set(), "delegatecall": False, "selfdestruct": False, "size": 0, "create": False}
    b = bytes.fromhex(code_hex[2:])
    i, n = 0, len(b)
    sels, dc, sd, cr = set(), False, False, False
    while i < n:
        op = b[i]
        if 0x60 <= op <= 0x7F:
            ln = op - 0x5F
            if ln == 4 and i + 5 <= n:
                sels.add("0x" + b[i + 1:i + 5].hex())
            i += 1 + ln
            continue
        if op == 0xF4:
            dc = True
        elif op == 0xFF:
            sd = True
        elif op in (0xF0, 0xF5):
            cr = True
        i += 1
    return {"selectors": sels, "delegatecall": dc, "selfdestruct": sd, "size": n, "create": cr}


_CLONE_PATTERNS = [
    ("eip1167", re.compile(r"^363d3d373d3d3d363d73([0-9a-f]{40})5af43d82803e903d91602b57fd5bf3")),
    ("eip1167_0age", re.compile(r"^3d3d3d3d363d3d37363d73([0-9a-f]{40})5af43d3d93803e602a57fd5bf3")),
    ("eip7511_push0", re.compile(r"^365f5f375f5f365f73([0-9a-f]{40})5af43d5f5f3e6029573d5ffd5b3d5ff3")),
    ("eip1167_vanity", re.compile(r"^363d3d373d3d3d363d6f([0-9a-f]{32})5af43d82803e903d91602757fd5bf3")),
]


def clone_target(code_hex):
    """If code is an ERC-1167-style minimal proxy return (variant, implementation)."""
    c = (code_hex or "")[2:].lower()
    for name, rx in _CLONE_PATTERNS:
        m = rx.match(c)
        if m:
            return name, "0x" + m.group(1).rjust(40, "0")
    return None


def is_7702(code_hex):
    c = (code_hex or "").lower()
    if c.startswith("0xef0100") and len(c) == 2 + 2 * 23:
        return "0x" + c[8:48]
    return None
