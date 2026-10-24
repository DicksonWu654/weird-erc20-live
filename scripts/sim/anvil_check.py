"""High-fidelity cross-check backend: run the same simulation plans as *real mined transactions* on a local anvil fork
(`anvil --fork-url <archive> --fork-block-number B`).  Nothing is ever sent to the real network: anvil is a local
sandbox that only reads state from the RPC.  Used via `run.py --backend anvil` on small subsets (every storage slot is
a lazy upstream request -> ~100-600 upstream requests per pair, ~1-2 min per pair at <=5 req/s).

Differences vs eth_simulateV1 (documented in README): real tx pipeline (gas price/basefee, tx.origin, receipts,
per-tx blocks, evm_snapshot/revert), but gas fees are refunded to the buyer after each tx so ETH deltas are pure swap proceeds (anvil picks the hardfork of the fork block).
"""
import asyncio, re, shutil, subprocess, time, json, os
import requests
from abi import *
import engine as E

TX_RE = re.compile(r"^(buy|xfer|approve|permit2_approve|s[12]\d)$")


class AnvilFork:
    def __init__(self, url, port, cups=40):
        self.url, self.port, self.cups = url, port, cups
        self.proc, self.block, self.snap = None, None, None

    def start(self, block):
        self.stop()
        exe = shutil.which("anvil") or os.path.expanduser("~/.foundry/bin/anvil")
        self.proc = subprocess.Popen([exe, "--fork-url", self.url, "--fork-block-number", str(block), "--port", str(self.port),
                                      "--auto-impersonate", "--compute-units-per-second", str(self.cups), "--retries", "8",
                                      "--fork-retry-backoff", "1000", "--timeout", "90000", "--quiet"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            time.sleep(0.5)
            try:
                if self.rpc("eth_blockNumber", []):
                    break
            except Exception:
                pass
        self.block = block
        self.snap = self.rpc("evm_snapshot", [])

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except Exception:
                self.proc.kill()
        self.proc = None

    def rpc(self, method, params):
        r = requests.post(f"http://127.0.0.1:{self.port}", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=300).json()
        if "error" in r:
            raise RuntimeError(f"{method}: {r['error']}")
        return r["result"]

    def rpc_raw(self, method, params):
        return requests.post(f"http://127.0.0.1:{self.port}", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=300).json()

    def receipt(self, h, timeout=300):
        """anvil mines asynchronously (upstream state fetches can take a while): poll until the receipt exists."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            rc = self.rpc("eth_getTransactionReceipt", [h])
            if rc:
                return rc
            time.sleep(0.3)
        raise RuntimeError("receipt timeout " + h)

    def reset(self):
        self.rpc("evm_revert", [self.snap])
        self.snap = self.rpc("evm_snapshot", [])


def failing_path(node, path=None):
    """deepest chain of failed frames in a callTracer tree"""
    path = (path or []) + [{"type": node.get("type"), "to": node.get("to"), "sel": (node.get("input") or "0x")[:10],
                            "error": node.get("error"), "revert_reason": node.get("revertReason"), "output": (node.get("output") or "")[:140]}]
    for c in node.get("calls", []) or []:
        if c.get("error"):
            return failing_path(c, path)
    return path


def run_plan(fork, plan, block):
    """Execute plan on the fork; returns (dict name->engine.R, metas, raw_log_items)."""
    fork.reset()
    out, metas, trace = {}, [], []
    bf = int(fork.rpc_raw("anvil_nodeInfo", [])["result"]["environment"]["baseFee"], 16)
    gp = max(int(fork.rpc("eth_gasPrice", []), 16), bf * 2) // 2  # tx gasPrice = 2*gp >= 2*basefee

    def refund_gas(rc):
        """give the gas fee back to BUYER so ETH balance deltas measure swap proceeds only"""
        paid = int(rc["gasUsed"], 16) * int(rc.get("effectiveGasPrice", hex(gp * 2)), 16)
        bal = int(fork.rpc("eth_getBalance", [BUYER, "latest"]), 16)
        fork.rpc("anvil_setBalance", [BUYER, hex(bal + paid)])
    for bi, b in enumerate(plan.blocks):
        for addr, st in b["state"].items():
            if "balance" in st:
                fork.rpc("anvil_setBalance", [addr, st["balance"]])
            if "code" in st:
                fork.rpc("anvil_setCode", [addr, st["code"]])
        bo = b["bo"] or {}
        if isinstance(bo.get("number"), str) and bo["number"].startswith("+"):
            n = int(bo["number"][1:])
            fork.rpc("anvil_mine", [hex(max(n - 1, 0)), hex(12)])
        for name, c in b["calls"]:
            call = {k: v for k, v in c.items() if k in ("from", "to", "data", "value")}
            call["gas"] = c["gas"]
            d = {}
            pre = fork.rpc_raw("eth_call", [call, "latest"])
            if "error" in pre:
                err = pre["error"]
                d = {"status": "0x0", "returnData": "0x", "gasUsed": "0x0",
                     "error": {"code": err.get("code"), "message": err.get("message"), "data": err.get("data")}, "logs": []}
                if TX_RE.match(name):  # still mine it (a reverting tx is a valid on-chain outcome)
                    try:
                        h = fork.rpc("eth_sendTransaction", [{**call, "gasPrice": hex(gp * 2)}])
                        rc = fork.receipt(h)
                        d["gasUsed"] = rc["gasUsed"]
                        refund_gas(rc)
                        try:
                            d["trace"] = failing_path(fork.rpc("debug_traceTransaction", [h, {"tracer": "callTracer"}]))
                        except Exception as e:
                            d["trace_error"] = str(e)[:200]
                    except Exception as e:
                        d["gasUsed"] = "0x0"
            else:
                d = {"status": "0x1", "returnData": pre["result"], "gasUsed": "0x0", "logs": []}
                if TX_RE.match(name):
                    h = fork.rpc("eth_sendTransaction", [{**call, "gasPrice": hex(gp * 2)}])
                    rc = fork.receipt(h)
                    d["status"] = rc["status"]
                    d["gasUsed"] = rc["gasUsed"]
                    refund_gas(rc)
                    d["logs"] = rc.get("logs", [])
                    if rc["status"] != "0x1":
                        d["error"] = {"message": "execution reverted (mined)", "data": "0x"}
            trace.append({"name": name, "call": call, "result": d})
            out[name] = E.R(d)
        blk = fork.rpc("eth_getBlockByNumber", ["latest", False])
        metas.append({"number": int(blk["number"], 16), "timestamp": int(blk["timestamp"], 16)})
    return out, metas, trace


def make_sim_fn(url, port=18545, cups=40):
    fork = AnvilFork(url, port, cups)

    async def sim_fn(rl, label, plan, block):
        if fork.block != block:
            await asyncio.to_thread(fork.start, block)
        out, metas, trace = await asyncio.to_thread(run_plan, fork, plan, block)
        rl.add("anvil:" + label, {"request": {"backend": "anvil", "fork_url": url, "fork_block": block, "calls": [t["call"] | {"name": t["name"]} for t in trace]},
                                  "response": {"results": [{"name": t["name"], **t["result"]} for t in trace]},
                                  "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return out, metas

    sim_fn.fork = fork
    return sim_fn
