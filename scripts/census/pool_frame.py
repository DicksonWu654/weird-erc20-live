"""Two-phase DEX-linked sampling of NEW tokens for months not covered by a complete contract-creation dataset (and, as validation,
for a month that is).  Population for month m: non-quote tokens whose FIRST pool-creation event (Uniswap V1/V2/Sushi/V3/V4, complete
scans) is in month m -- M_m is known exactly.  Phase 1: seeded SRS of n_cand candidates.  Phase 2 per candidate:
  old/new : eth_getCode(token, P-W-1) non-empty => created more than W=216,000 blocks (~30 d) before its first pool => 'old'
  creation block : first Transfer log in [P-W, P] (Tenderly getLogs) verified by getCode at fl-1/fl, else bisection (batches of 3 getCode)
  creation details : trace_block(creation block) -> creator, tx, tx sender, CREATE/CREATE2, trace address, runtime code flags
Estimate: N_new_pooled(m) = M_m * (#new / n_cand), Wilson 95% CI;  every new token gets weight M_m / n_cand.
  .venv/bin/python scripts/census/pool_frame.py --months 2025-02:2026-09 [--ncand 500] [--seed S]
Output: data/census/cache/poolframe/<month>.jsonl (resumable), assembled by assemble_pool_frame.py
"""
import argparse, concurrent.futures as cf, json, math, pathlib, sys, threading, time
import numpy as np, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from rpc import RPC, ROOT
from classify import flags, kind as kind_of
from dex_build import QUOTES
from sample import month_list

W = 216_000
CODE_RPS = 3.5
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TENDERLY = "https://gateway.tenderly.co/public/mainnet"
MEV = "https://rpc.mevblocker.io"
OUT = ROOT / "data/census/cache/poolframe"
QSET = {a for a, _ in QUOTES}


def candidates():
    d = pd.read_parquet(ROOT / "data/census/dex_pools.parquet", columns=["dex", "token0", "token1", "block", "quote_token", "month", "pool_address", "log_index"])
    long = pd.concat([d.assign(token=d.token0), d.assign(token=d.token1)], ignore_index=True)
    long = long[~long.token.isin(QSET)]
    first = long.sort_values(["block", "log_index"]).groupby("token").head(1)
    return first[["token", "block", "month", "dex", "pool_address"]].rename(columns={"block": "first_pool_block", "dex": "first_pool_dex", "pool_address": "first_pool_address"})


class Ctx:
    def __init__(self, rps_trace, tag):
        self.logs = RPC(url=TENDERLY, rps=2.5, stream=f"poolframe_logs_{tag}", timeout=120)
        self.code = RPC(url=MEV, rps=CODE_RPS, stream=f"poolframe_code_{tag}", timeout=60)
        self.trace = RPC(rps=rps_trace, stream=f"poolframe_trace_{tag}")


def code_at(ctx, addr, blocks):
    res = ctx.code.batch([("eth_getCode", [addr, hex(max(b, 0))]) for b in blocks])
    out = []
    for r, e in res:
        if e: raise RuntimeError(str(e)[:100])
        out.append(r not in (None, "0x"))
    return out


def first_with_code(ctx, addr, lo, hi):
    """smallest block in (lo, hi] with code, given no code at lo and code at hi (4-ary search via batches of 3)"""
    while hi - lo > 1:
        step = (hi - lo) / 4
        pts = sorted({min(max(lo + int(step * k), lo + 1), hi - 1) for k in (1, 2, 3)})
        if not pts: break
        has = code_at(ctx, addr, pts)
        for p, h in zip(pts, has):
            if h: hi = p; break
            lo = p
    return hi


def process(ctx, c):
    tok, P = c["token"], int(c["first_pool_block"])
    lo = P - W
    r = dict(token=tok, first_pool_block=P, month=c["month"], first_pool_dex=c["first_pool_dex"], first_pool_address=c["first_pool_address"])
    if code_at(ctx, tok, [lo - 1])[0]:
        r["status"] = "old"; return r
    r["status"] = "new"
    fl = None
    try:
        lg = ctx.logs.call("eth_getLogs", [{"address": tok, "topics": [TRANSFER], "fromBlock": hex(lo), "toBlock": hex(P)}], store="hash")
        if lg: fl = min(int(x["blockNumber"], 16) for x in lg); r["n_transfers_before_first_pool"] = len(lg)
        else: r["n_transfers_before_first_pool"] = 0
    except RuntimeError:
        r["n_transfers_before_first_pool"] = None
    hi = fl if fl is not None else P
    if fl is not None:
        a, b = code_at(ctx, tok, [fl - 1, fl])
        if b and not a: cb = fl
        elif not b: cb = first_with_code(ctx, tok, fl, P) if fl < P else P
        else: cb = first_with_code(ctx, tok, lo - 1, fl - 1)
    else:
        cb = first_with_code(ctx, tok, lo - 1, P)
    r["creation_block"] = cb; r["age_blocks_at_first_pool"] = P - cb
    tr = ctx.trace.call("trace_block", [hex(cb)], store=lambda x: {"n_traces": len(x)})
    tx_traces = {}
    for t in tr:
        if t.get("transactionHash"): tx_traces.setdefault(t["transactionHash"], []).append(t)
    found = None
    for h, ts in tx_traces.items():
        failed = {tuple(t["traceAddress"]) for t in ts if t.get("error")}
        for t in ts:
            res = t.get("result") or {}
            if t["type"] == "create" and res.get("address", "").lower() == tok and not t.get("error"):
                ta = tuple(t["traceAddress"])
                if any(ta[:i] in failed for i in range(len(ta))): continue
                root = next((u["action"]["from"] for u in ts if u["traceAddress"] == []), None)
                found = (h, t, root)
    if not found:
        r["creation_trace"] = "not_found"; return r
    h, t, root = found
    code = bytes.fromhex(t["result"]["code"][2:])
    f = flags(code)
    r.update(creation_tx=h, creator=t["action"]["from"], tx_sender=root, creation_opcode=t["action"].get("creationMethod"),
             direct_deploy=(t["traceAddress"] == []), trace_address=list(t["traceAddress"]), code_hash=f["code_hash"], code_len=f["code_len"],
             static_c20=bool(f["c20_core"]), clone_impl=f["clone_impl"], kind=kind_of(f) if f["c20_core"] else None, creation_trace="ok")
    return r


def run_month(m, ncand, seed, workers, rps_trace, cands):
    OUT.mkdir(parents=True, exist_ok=True)
    pool = cands[cands.month == m].sort_values("token")
    M = len(pool)
    rng = np.random.default_rng([seed, int(m.replace("-", ""))])
    idx = rng.permutation(M)[:ncand]
    chosen = pool.iloc[idx]
    path = OUT / f"{m}.jsonl"
    done = set()
    if path.exists():
        for line in open(path):
            try: done.add(json.loads(line)["token"])
            except Exception: pass
    todo = [r for r in chosen.to_dict("records") if r["token"] not in done]
    meta = dict(month=m, M_population=M, n_cand_target=len(chosen), seed=seed)
    (OUT / f"{m}.meta.json").write_text(json.dumps(meta))
    if not todo:
        print(m, "complete", len(done)); return
    ctx = Ctx(rps_trace, m)
    lock, fh, cnt, t0 = threading.Lock(), open(path, "a"), [0], time.time()
    ffh = open(OUT / f"{m}.failed.jsonl", "a")
    def work(c):
        try:
            r = process(ctx, c)
        except Exception as e:  # noqa: BLE001
            with lock: ffh.write(json.dumps(dict(token=c["token"], error=str(e)[:200])) + "\n"); ffh.flush()
            return
        with lock:
            fh.write(json.dumps(r) + "\n"); fh.flush(); cnt[0] += 1
            if cnt[0] % 50 == 0: print(m, f"{cnt[0]}/{len(todo)}", f"{cnt[0]/(time.time()-t0):.2f}/s", flush=True)
    for attempt in range(2):
        with cf.ThreadPoolExecutor(workers) as ex: list(ex.map(work, todo))
        got = {json.loads(l)["token"] for l in open(path)}
        todo = [c for c in todo if c["token"] not in got]
        if not todo: break
    print(m, "done; failed:", len(todo), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", required=True); ap.add_argument("--ncand", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20261021); ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--rps-trace", type=float, default=1.0); ap.add_argument("--reverse", action="store_true")
    a = ap.parse_args()
    cands = candidates()
    ms = month_list(a.months)
    for m in (ms[::-1] if a.reverse else ms):
        run_month(m, a.ncand, a.seed, a.workers, a.rps_trace, cands)
