// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Synthetic contracts used to unit-test the W3 control classifier on a local anvil chain (no real funds / keys).
bytes32 constant IMPL_1967 = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
bytes32 constant ADMIN_1967 = 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103;
bytes32 constant BEACON_1967 = 0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50;

contract Tok {
    string public name = "T"; string public symbol = "T"; uint8 public decimals = 18; uint256 public totalSupply;
    address public owner; address public minter; address public pauser;
    mapping(address => uint256) public balanceOf;
    constructor() { owner = msg.sender; minter = msg.sender; pauser = msg.sender; }
    function initialize(address o) external { owner = o; minter = o; pauser = o; }
    function setOwner(address o) external { owner = o; }
    function mint(address to, uint256 v) external { require(msg.sender == minter); balanceOf[to] += v; totalSupply += v; }
    function pause() external { require(msg.sender == pauser); }
    function blacklist(address) external { require(msg.sender == owner); }
}

contract TokUUPS is Tok {
    function upgradeTo(address) external { require(msg.sender == owner); }
    function upgradeToAndCall(address, bytes calldata) external payable { require(msg.sender == owner); }
    function proxiableUUID() external pure returns (bytes32) { return IMPL_1967; }
}

// UUPS whose upgrade authority is NOT any standard getter (hidden slot): classifier must NOT claim the owner can upgrade
contract TokUUPSHidden is Tok {
    bytes32 constant HIDDEN = keccak256("hidden.upgrader");
    function setHidden(address a) external { bytes32 h = HIDDEN; assembly { sstore(h, a) } }
    function upgradeTo(address) external view { address a; bytes32 h = HIDDEN; assembly { a := sload(h) } require(msg.sender == a); }
    function upgradeToAndCall(address, bytes calldata) external payable { }
    function proxiableUUID() external pure returns (bytes32) { return IMPL_1967; }
}

abstract contract Fwd {
    function _fwd(address impl) internal {
        assembly {
            calldatacopy(0, 0, calldatasize())
            let r := delegatecall(gas(), impl, 0, calldatasize(), 0, 0)
            returndatacopy(0, 0, returndatasize())
            switch r case 0 { revert(0, returndatasize()) } default { return(0, returndatasize()) }
        }
    }
}

// EIP-1967 proxy, admin slot set (transparent-like; no admin-only dispatch needed for the test)
contract Proxy1967 is Fwd {
    constructor(address impl, address admin_, address owner_) {
        bytes32 i = IMPL_1967; bytes32 a = ADMIN_1967;
        assembly { sstore(i, impl) sstore(a, admin_) }
        (bool ok,) = impl.delegatecall(abi.encodeWithSignature("initialize(address)", owner_)); require(ok);
    }
    fallback() external payable { address impl; bytes32 i = IMPL_1967; assembly { impl := sload(i) } _fwd(impl); }
}

// no-initialise variant (used to stack proxies for the "implementation behind another layer" test)
contract ProxyNoInit is Fwd {
    constructor(address impl, address admin_) {
        bytes32 i = IMPL_1967; bytes32 a = ADMIN_1967;
        assembly { sstore(i, impl) sstore(a, admin_) }
    }
    fallback() external payable { address impl; bytes32 i = IMPL_1967; assembly { impl := sload(i) } _fwd(impl); }
}

// UUPS-style: implementation slot only
contract ProxyUUPS is Fwd {
    constructor(address impl, address owner_) {
        bytes32 i = IMPL_1967;
        assembly { sstore(i, impl) }
        (bool ok,) = impl.delegatecall(abi.encodeWithSignature("initialize(address)", owner_)); require(ok);
    }
    fallback() external payable { address impl; bytes32 i = IMPL_1967; assembly { impl := sload(i) } _fwd(impl); }
}

// custom: implementation kept in an arbitrary slot
contract ProxyCustom is Fwd {
    bytes32 constant SLOT = keccak256("my.weird.impl.slot");
    constructor(address impl, address owner_) {
        bytes32 s = SLOT;
        assembly { sstore(s, impl) }
        (bool ok,) = impl.delegatecall(abi.encodeWithSignature("initialize(address)", owner_)); require(ok);
    }
    fallback() external payable { address impl; bytes32 s = SLOT; assembly { impl := sload(s) } _fwd(impl); }
}

// Safe-style: masterCopy at slot 0
contract ProxySlot0 is Fwd {
    address public masterCopy;
    constructor(address impl) { masterCopy = impl; }
    fallback() external payable { _fwd(masterCopy); }
}
// token whose own storage layout is compatible with slot0 proxy: owner at slot 1.. (the proxy occupies slot0)
contract TokForSlot0 {
    address public masterCopy;   // slot 0 (same as proxy)
    string public name = "S0"; string public symbol = "S0"; uint8 public decimals = 18;
    address public owner;
    function setup(address o) external { owner = o; }
    function totalSupply() external pure returns (uint256) { return 1; }
}

contract Beacon { address public implementation; address public owner; constructor(address i, address o) { implementation = i; owner = o; } }
contract ProxyBeacon {
    constructor(address beacon, address owner_) {
        bytes32 b = BEACON_1967;
        assembly { sstore(b, beacon) }
        (bool ok,) = Beacon(beacon).implementation().delegatecall(abi.encodeWithSignature("initialize(address)", owner_)); require(ok);
    }
    fallback() external payable {
        bytes32 b = BEACON_1967; address beacon; assembly { beacon := sload(b) }
        address impl = Beacon(beacon).implementation();
        assembly {
            calldatacopy(0, 0, calldatasize())
            let r := delegatecall(gas(), impl, 0, calldatasize(), 0, 0)
            returndatacopy(0, 0, returndatasize())
            switch r case 0 { revert(0, returndatasize()) } default { return(0, returndatasize()) }
        }
    }
}

contract DiamondLike is Fwd {
    address public owner; address public facet;
    constructor(address f) { owner = msg.sender; facet = f; }
    function facetAddresses() external view returns (address[] memory a) { a = new address[](1); a[0] = facet; }
    fallback() external payable { _fwd(facet); }
}
contract ProxyAdminM { address public owner; constructor(address o) { owner = o; } function getProxyAdmin(address) external pure returns (address) { return address(0); } }
contract SafeMock {
    address[] _o; uint256 _t;
    constructor(address[] memory o, uint256 t) { _o = o; _t = t; }
    function getThreshold() external view returns (uint256) { return _t; }
    function getOwners() external view returns (address[] memory) { return _o; }
    function VERSION() external pure returns (string memory) { return "1.4.1"; }
}
contract MultisigLegacyMock {
    address[] _o; uint256 public required;
    constructor(address[] memory o, uint256 r) { _o = o; required = r; }
    function getOwners() external view returns (address[] memory) { return _o; }
}
contract OZTimelockMock { uint256 _d; constructor(uint256 d) { _d = d; } function getMinDelay() external view returns (uint256) { return _d; }
    function hasRole(bytes32, address) external pure returns (bool) { return false; } }
contract CompoundTimelockMock { uint256 public delay; address public admin; constructor(uint256 d, address a) { delay = d; admin = a; } }
contract GovMock { function votingPeriod() external pure returns (uint256) { return 40320; } function proposalThreshold() external pure returns (uint256) { return 1; }
    function COUNTING_MODE() external pure returns (string memory) { return "support=bravo&quorum=for"; } function timelock() external pure returns (address) { return address(0); } }
