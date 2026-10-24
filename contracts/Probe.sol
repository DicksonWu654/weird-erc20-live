// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

// Weird-ERC20-Live probe harness.
//
// Deployed NEVER: its RUNTIME bytecode is injected at a fixed fake address through an `eth_call` state override
// (together with the small Sink / Rejecter actor contracts at two more fake addresses) and the token under test gets
// a balance for the harness through a storage-slot override. Nothing is ever sent on-chain.
//
// The harness is a tiny interpreter. Python sends a list of "scripts" (each a list of Ops: call / staticcall as the
// harness, or a call executed by the Sink actor); every script runs in an ISOLATED self-call that always ends in
// `revert Done(payload)`, so state changes made by one script (transfers, approvals, fee burns ...) are rolled back
// before the next one starts. The payload carries the raw result of every op (success flag, gas used, return-data
// length and the first RET_CAP bytes of return data); the classification lives on the Python side, so the raw
// evidence is exactly what the token returned.

struct Op {
    uint8 kind;      // 0 = harness calls target, 1 = harness staticcalls target, 2 = Sink calls target, 3 = Sink staticcalls target
    address target;
    bytes data;
}

struct Step {
    bool ok;
    uint32 gas;      // gas consumed by the call itself (call overhead included)
    uint32 retLen;   // full return-data length (ret is capped)
    bytes ret;
}

error Done(bytes payload);

library Raw {
    uint256 internal constant RET_CAP = 320;

    function doCall(address t, bytes memory d, uint256 cap, bool isStatic) internal returns (Step memory s) {
        uint256 g0 = gasleft();
        bool ok;
        uint256 sz;
        uint256 used;
        if (isStatic) {
            assembly {
                ok := staticcall(cap, t, add(d, 32), mload(d), 0, 0)
                sz := returndatasize()
            }
        } else {
            assembly {
                ok := call(cap, t, 0, add(d, 32), mload(d), 0, 0)
                sz := returndatasize()
            }
        }
        used = g0 - gasleft();
        uint256 n = sz > RET_CAP ? RET_CAP : sz;
        bytes memory ret = new bytes(n);
        assembly {
            returndatacopy(add(ret, 32), 0, n)
        }
        s = Step(ok, uint32(used), uint32(sz), ret);
    }
}

// Accepts any call, counts and records them (ERC-777 / ERC-223 / ERC-677-style callbacks), and can act as the
// spender / caller for transferFrom tests.
contract Sink {
    address public token;
    uint256 public calls;
    uint256 public sels;      // last 8 selectors seen, shifted in 32 bits at a time
    uint256 public balAtCall; // token.balanceOf(this) as observed inside the (last) callback
    uint256 public balAtCall0; // ... as observed inside the FIRST callback

    function setToken(address t) external {
        token = t;
    }

    function exec(address t, bytes calldata d, uint256 cap, bool isStatic)
        external
        returns (bool ok, uint32 gas, uint32 retLen, bytes memory ret)
    {
        Step memory s = Raw.doCall(t, d, cap, isStatic);
        return (s.ok, s.gas, s.retLen, s.ret);
    }

    function _rec() internal {
        calls++;
        sels = (sels << 32) | uint256(uint32(msg.sig));
        address t = token;
        if (t != address(0)) {
            (bool o, bytes memory r) = t.staticcall(abi.encodeWithSelector(0x70a08231, address(this)));
            if (o && r.length >= 32) {
                uint256 b = abi.decode(r, (uint256));
                balAtCall = b;
                if (calls == 1) balAtCall0 = b;
            }
        }
    }

    fallback() external payable {
        _rec();
    }

    receive() external payable {
        _rec();
    }
}

// Reverts on everything (a contract that refuses tokens / ETH).
contract Rejecter {
    fallback() external payable {
        revert("RJ");
    }

    receive() external payable {
        revert("RJ");
    }
}

contract Probe {
    // callback bookkeeping for the harness itself (ERC-777 tokensToSend hook etc.)
    address public token;
    uint256 public hookCount;
    uint256 public hookSels;
    uint256 public hookBal0; // token.balanceOf(this) inside the first callback

    function version() external pure returns (uint256) {
        return 2;
    }

    function run(address tok, uint256 gasCap, uint256 viewCap, uint256 scriptGas, address sink, Op[][] calldata scripts)
        external
        returns (bytes[] memory outs)
    {
        token = tok;
        outs = new bytes[](scripts.length);
        for (uint256 i = 0; i < scripts.length; i++) {
            (bool ok, bytes memory r) =
                address(this).call{gas: scriptGas}(abi.encodeCall(this.exec, (scripts[i], gasCap, viewCap, sink)));
            if (!ok && r.length >= 4 && bytes4(r) == Done.selector) {
                assembly {
                    r := add(r, 4)
                }
                outs[i] = abi.decode(r, (bytes));
            } else {
                // script did not finish (out of gas / harness fault): empty payload = "error" for Python
                outs[i] = "";
            }
        }
    }

    function exec(Op[] calldata ops, uint256 cap, uint256 vcap, address sink) external {
        require(msg.sender == address(this), "self");
        Step[] memory steps = new Step[](ops.length);
        for (uint256 j = 0; j < ops.length; j++) {
            Op calldata o = ops[j];
            if (o.kind == 0) {
                steps[j] = Raw.doCall(o.target, o.data, cap, false);
            } else if (o.kind == 1) {
                steps[j] = Raw.doCall(o.target, o.data, vcap, true);
            } else {
                uint256 g0 = gasleft();
                (bool ok, bytes memory r) = sink.call(
                    abi.encodeCall(Sink.exec, (o.target, o.data, o.kind == 3 ? vcap : cap, o.kind == 3))
                );
                uint256 used = g0 - gasleft();
                if (ok && r.length >= 128) {
                    (bool k, uint32 g, uint32 rl, bytes memory rr) = abi.decode(r, (bool, uint32, uint32, bytes));
                    steps[j] = Step(k, g, rl, rr);
                } else {
                    steps[j] = Step(false, uint32(used), 0, "");
                }
            }
        }
        revert Done(abi.encode(steps));
    }

    function _rec() internal {
        hookCount++;
        hookSels = (hookSels << 32) | uint256(uint32(msg.sig));
        address t = token;
        if (t != address(0)) {
            (bool o, bytes memory r) = t.staticcall(abi.encodeWithSelector(0x70a08231, address(this)));
            if (o && r.length >= 32 && hookCount == 1) hookBal0 = abi.decode(r, (uint256));
        }
    }

    fallback() external payable {
        _rec();
    }

    receive() external payable {
        _rec();
    }
}
