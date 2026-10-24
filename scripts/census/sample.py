"""Stratified random block sampling of contract creations via trace_block (one stratum per calendar month).

For month M with N_M blocks, a seeded random permutation of the month's blocks is drawn and the first n are traced
(simple random sample without replacement; extendable by raising n).  For every successful create trace (ancestor
calls must not have reverted) we record creator, tx, trace address, CREATE/CREATE2, code hash and static ERC-20 flags.

  .venv/bin/python scripts/census/sample.py --months 2023-04:2026-09 --n 1500 [--seed S] [--workers 4]

Output: data/census/cache/blocksample/<month>.jsonl (one line per sampled block = completion marker; resumable);
raw evidence (request, response sha256, extract of creates) in data/raw/census/trace_<month>.jsonl.gz.
"""
import argparse, concurrent.futures as cf, csv, json, pathlib, sys, threading, time
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from rpc import RPC, ROOT
from classify import flags

OUTD = ROOT / "data/census/cache/blocksample"
SAMPLE_FROM = 21_850_000  # first block NOT covered by a complete contract-creation dataset (Paradigm to 16,799,999; Zellic 16.8M..21,849,999)
KEEP = ("s_totalSupply s_balanceOf s_transfer s_approve s_allowance s_transferFrom s_getReserves s_token0 s_token1 "
        "s_v1_tokenAddress s_v1_addLiquidity s_minter s_bind s_getCurrentTokens s_getPoolId s_aave_underlying "
        "s_ctoken_rate s_asset s_convertToShares s_pricePerShare s_deposit s_withdraw s_decimals d_totalSupply d_balanceOf d_transfer").split()


def creates_from_trace(traces):
    """Return (list of create dicts, n_create_ok_total, n_create_failed, n_empty_code)."""
    by_tx = {}
    for t in traces:
        if t.get("type") in ("create",):
            pass
        h = t.get("transactionHash")
        if h is None:
            continue
        by_tx.setdefault(h, []).append(t)
    out, n_ok, n_fail, n_empty = [], 0, 0, 0
    for h, ts in by_tx.items():
        failed = {tuple(t["traceAddress"]) for t in ts if t.get("error")}
        root_from = next((t["action"]["from"] for t in ts if t["traceAddress"] == []), None)
        for t in ts:
            if t["type"] != "create":
                continue
            ta = tuple(t["traceAddress"])
            if t.get("error") or any(ta[:i] in failed for i in range(0, len(ta))):
                n_fail += 1
                continue
            res = t.get("result")
            if not res or "address" not in res:
                n_fail += 1
                continue
            code = bytes.fromhex(res.get("code", "0x")[2:])
            n_ok += 1
            if not code:
                n_empty += 1
                continue
            f = flags(code)
            rec = dict(address=res["address"], creator=t["action"]["from"], tx_hash=h, tx_from=root_from,
                       trace_address=list(ta), method=t["action"].get("creationMethod"), block=t["blockNumber"],
                       code_len=f["code_len"], code_hash=f["code_hash"], c20_core=f["c20_core"], c20_loose=f["c20_loose"],
                       clone_impl=f["clone_impl"], proxy_slot=f["proxy_slot"])
            if f["c20_loose"] or f["clone_impl"] or f["proxy_slot"]:
                rec["flags"] = [k for k in KEEP if f[k]]
            out.append(rec)
    return out, n_ok, n_fail, n_empty


def month_blocks(m):
    with open(ROOT / "data/census/blockmap.csv") as f:
        for r in csv.DictReader(f):
            if r["month"] == m:
                lo, hi = int(r["first_block"]), int(r["last_block"])
                if hi >= SAMPLE_FROM and lo < SAMPLE_FROM:  # month straddles the exact-data cutoff: sample only the uncovered tail
                    lo = SAMPLE_FROM
                return lo, hi
    raise KeyError(m)


def month_list(spec):
    a, b = spec.split(":") if ":" in spec else (spec, spec)
    out, (y, m) = [], map(int, a.split("-"))
    ye, me = map(int, b.split("-"))
    while (y, m) <= (ye, me):
        out.append(f"{y}-{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def sample_order(month, lo, hi, seed):
    ym = int(month.replace("-", ""))
    rng = np.random.default_rng([seed, ym])
    return (lo + rng.permutation(hi - lo + 1)).tolist()


def run_month(month, n, seed, workers, rpc_url, rps):
    OUTD.mkdir(parents=True, exist_ok=True)
    lo, hi = month_blocks(month)
    order = sample_order(month, lo, hi, seed)[:n]
    path = OUTD / f"{month}.jsonl"
    done = set()
    if path.exists():
        for line in open(path):
            try: done.add(json.loads(line)["block"])
            except Exception: pass
    todo = [b for b in order if b not in done]
    if not todo:
        print(month, "complete", len(done)); return
    rpc = RPC(url=rpc_url, rps=rps, stream=f"trace_{month}")
    lock, fh = threading.Lock(), open(path, "a")
    t0 = time.time(); cnt = [0]

    ffh = open(OUTD / f"{month}.failed.jsonl", "a")

    def work(b):
        try:
            _work(b)
        except Exception as e:  # noqa: BLE001  (e.g. free-plan timeout on huge blocks) -> recorded, handled by fallback
            with lock:
                ffh.write(json.dumps({"block": b, "error": str(e)[:300]}) + "\n"); ffh.flush()

    def _work(b):
        res, sha = rpc.call("trace_block", [hex(b)], store=lambda r: {"n_traces": len(r), "creates": [
            [t["transactionHash"], t["traceAddress"], (t.get("result") or {}).get("address"), t.get("error")]
            for t in r if t["type"] == "create"]}, meta=True)
        cr, n_ok, n_fail, n_empty = creates_from_trace(res)
        line = json.dumps(dict(block=b, n_ok=n_ok, n_fail=n_fail, n_empty=n_empty, n_traces=len(res), sha256=sha, creates=cr),
                          separators=(",", ":"))
        with lock:
            fh.write(line + "\n"); fh.flush(); cnt[0] += 1
            if cnt[0] % 100 == 0:
                print(f"{month} {cnt[0]}/{len(todo)} {cnt[0]/(time.time()-t0):.2f} blk/s", flush=True)

    for attempt in range(3):  # transient free-plan timeouts: retry the failed blocks (they are recorded in <month>.failed.jsonl)
        with cf.ThreadPoolExecutor(workers) as ex:
            list(ex.map(work, todo))
        got = set()
        for line in open(path):
            try: got.add(json.loads(line)["block"])
            except Exception: pass
        todo = [b for b in todo if b not in got]
        if not todo: break
        print(month, "retrying", len(todo), flush=True)
    if todo:
        print(month, "WARNING permanently failed blocks:", len(todo), flush=True)
    fh.close(); ffh.close(); rpc.close()
    print(month, "done", len(todo), f"{time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", required=True); ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=20261021); ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--reverse", action="store_true"); ap.add_argument("--rpc", default="https://eth.drpc.org"); ap.add_argument("--rps", type=float, default=4.0)
    a = ap.parse_args()
    ms = month_list(a.months)
    for m in (ms[::-1] if a.reverse else ms):
        run_month(m, a.n, a.seed, a.workers, a.rpc, a.rps)
