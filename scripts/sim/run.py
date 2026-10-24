#!/usr/bin/env python3
"""Buy/sell simulator CLI.

  scripts/sim/run.py --pairs pairs.csv --mode historical --offset-blocks 7200 --out data/sim/<run>/
  scripts/sim/run.py --pairs pairs.csv --mode latest --out data/sim/<run>/

pairs file: CSV (header) or JSONL with columns  token, pool [, dex, created_block, created_ts, block]
  dex (optional): uniswap_v2 | sushiswap | uniswap_v3 | uniswap_v1   (auto-detected from the pool otherwise)
  historical mode: simulate at block = created_block + offset (or the explicit `block` column)
  latest mode:     simulate at the pinned head (head - 3)
Output: <out>/results.jsonl (resumable: finished (token,pool,block) are skipped), <out>/run_meta.json,
        raw request/response per pair under data/raw/sim/<run>/xx/<token>_<pool>.json.gz
"""
import argparse, asyncio, csv, json, os, sys, time, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from abi import *
from rpc import Endpoint, RpcTransportError
import pipeline as PL
import control as CT
import classify as CL
import rug as RG

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class Ctx:
    pass


def read_pairs(path):
    rows = []
    with open(path) as f:
        if path.endswith((".jsonl", ".ndjson")):
            rows = [json.loads(l) for l in f if l.strip()]
        else:
            rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        tok = r.get("token") or r.get("token_address")
        pool = r.get("pool") or r.get("pair") or r.get("pool_address") or r.get("pair_address")
        if not tok or not pool:
            continue
        cb = r.get("created_block") or r.get("pool_created_block") or r.get("pair_created_block")
        dex = (r.get("dex") or "").strip() or None
        # Uniswap V4 uses a bytes32 PoolId from Initialize, while legacy
        # routes use an address.  Auto-detect a 32-byte id when dex is absent.
        is_v4 = (dex or "").lower() in ("v4", "uniswap_v4") or (isinstance(pool, str) and len(pool.strip()) == 66)
        pool_value = ("0x" + pool.strip()[2:].lower().zfill(64)) if is_v4 else to_checksum_address(pool)
        if is_v4 and not dex:
            dex = "uniswap_v4"
        out.append({"token": to_checksum_address(tok), "pool": pool_value, "dex": dex,
                    "created_block": int(float(cb)) if cb not in (None, "", "nan") else None,
                    "block": int(float(r["block"])) if r.get("block") not in (None, "", "nan") else None,
                    "extra": {k: v for k, v in r.items() if k not in ("token", "pool", "dex", "created_block", "block")}})
    return out


def needs_redo(r):
    """transient failures worth retrying: Etherscan enrichment failed, RPC/sim errors."""
    u = r.get("unprobeable") or ""
    if u.startswith(("sim_error", "internal_error", "sim_error_phase2")):
        return True
    return bool(((r.get("rug") or {}).get("screen") or {}).get("detail_error"))


def load_done(path, redo=False):
    done = set()
    if os.path.exists(path):
        with open(path) as f:
            for l in f:
                try:
                    r = json.loads(l)
                    k = (r["token"], r["pool"], r["block"])
                    if redo and needs_redo(r):
                        done.discard(k)
                    else:
                        done.add(k)
                except Exception:
                    pass
    return done


async def process(ctx, pair):
    token, pool = pair["token"], pair["pool"]
    if ctx.mode == "latest":
        block = ctx.head_pinned
    else:
        block = pair["block"] or ((pair["created_block"] + ctx.offset) if pair["created_block"] is not None else None)
        if block is None:
            return None
        block = min(block, ctx.head_pinned)
    t0 = time.time()
    try:
        res, rl = await PL.simulate_pair(ctx, token, pool, block, pair["dex"], amts=ctx.amts, pair_data=pair)
    except Exception as e:  # never lose a pair silently
        res, rl = {"token": token, "pool": pool, "block": block, "unprobeable": f"internal_error:{e!r}"[:300], "meta": {}, "sells": [], "pool_info": {}}, PL.RawLog()
    meta = res.get("meta", {})
    control = CT.analyse(meta) if meta else None
    pi = res.get("pool_info", {})
    if getattr(ctx, "measure_liquidity", False):
        measured = await PL.E.measure_quote_liquidity(ctx, rl, token, pool, block, pair, pi)
        pi.update(measured)
        res["pool_info"] = pi
    # rug analysis
    rug = None
    created = pair["created_block"]
    if ctx.rug and pi.get("kind") and pi.get("kind") != "v4" and created is not None:
        try:
            sc = await RG.rug_screen(ctx, rl, token, pool, pi["kind"], bool(pi.get("quote_is_token0")), created, ctx.head_pinned,
                                     sim_reserve=pi.get("reserve_quote"), sim_block=block)
            detail = None
            rr = sc.get("ratio_final_peak")
            if ctx.etherscan and sc.get("status") == "ok" and rr is not None and rr <= 0.15:
                try:
                    detail = await RG.rug_detail(ctx.etherscan, rl, token, pool, pi["kind"], bool(pi.get("quote_is_token0")), created, head=ctx.head_pinned - ctx.head_pinned % 2000)
                except Exception as e:
                    detail = None
                    sc["detail_error"] = repr(e)[:200]
            verdict = RG.rug_verdict(sc, detail)
            if detail:
                detail.pop("burns_all", None)
            rug = {"screen": sc, "detail": detail, **verdict}
        except Exception as e:
            rug = {"verdict": "unknown", "error": repr(e)[:200]}
    elif ctx.rug and created is None:
        rug = {"verdict": "unknown", "error": "no_created_block"}
    flags, primary, reason, taxes = CL.classify(res, control, rug, ctx.dust_floor_wei)
    if primary == "unprobeable" and reason in ("dust_pool", "no_active_liquidity", "zero_output_buy", "no_positive_sell_quote"):
        res["unprobeable"] = reason
    code_hash = (control or {}).get("code_keccak")
    meta_out = {k: v for k, v in meta.items() if k != "code_hex"}
    # evidence
    ev = None
    if rl.items:
        suffix = f"_{(pair.get('extra') or {}).get('sample_id','row')}_{time.time_ns()}" if getattr(ctx,'unique_raw',False) else ''
        rel = f"{ctx.raw_dir}/{token[2:4].lower()}/{token.lower()}_{pool.lower()[:10]}_{block}{suffix}.json.gz"
        sha = rl.dump(os.path.join(ROOT, rel))
        ev = {"raw": rel, "sha256": sha}
    rec = {"token": token, "pool": pool, "dex": pi.get("dex") or pair["dex"], "block": block, "mode": ctx.mode,
           "created_block": created, "pool_info": pi, "buy": res.get("buy"), "transfer": res.get("transfer"),
           "approve": res.get("approve"), "sells": res.get("sells"), "taxes": taxes, "meta": meta_out, "control": control,
           "rug": rug, "flags": sorted(set(flags)), "primary_label": primary, "label_reason": reason,
           "unprobeable": res.get("unprobeable"), "replay_consistent": res.get("replay_consistent"),
           "sim_blocks": res.get("blocks"), "evidence": ev, "extra": pair["extra"] or None,
           "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "elapsed_s": round(time.time() - t0, 2),
           "rules": {**CL.RULES, "DUST_FLOOR_WEI": ctx.dust_floor_wei}}
    return rec


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--mode", choices=["historical", "latest"], required=True)
    ap.add_argument("--offset-blocks", type=int, default=7200, help="historical: simulate at created_block + offset (7200 ~ 24h)")
    ap.add_argument("--latest-block", type=int, default=None, help="pin latest-mode regression to an existing block")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run", default=None, help="run name for raw dir (default: basename of --out)")
    ap.add_argument("--raw-dir", default=None, help="raw evidence directory relative to repository root")
    ap.add_argument("--archive-url", default=os.environ.get("SIM_ARCHIVE_URL", "https://eth.drpc.org"))
    ap.add_argument("--fresh-url", default=os.environ.get("SIM_FRESH_URL", "https://ethereum-rpc.publicnode.com"),
                    help="latest-mode primary endpoint (no archive needed); archive-url is the fallback")
    ap.add_argument("--rps-archive", type=float, default=4.0)
    ap.add_argument("--rps-fresh", type=float, default=3.0)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--buy-eth", type=float, default=0.01)
    ap.add_argument("--dust-floor-wei", type=int, default=CL.RULES["DUST_FLOOR_WEI"],
                    help="classify measured ETH-equivalent quote liquidity below this as no_meaningful_liquidity (0 disables)")
    ap.add_argument("--measure-liquidity", action="store_true",
                    help="save venue-consistent quote liquidity at the historical parent state")
    ap.add_argument("--rug", choices=["auto", "on", "off"], default="auto", help="auto: on in historical mode, off in latest")
    ap.add_argument("--no-etherscan", action="store_true", help="skip Etherscan log enrichment for rug attribution")
    ap.add_argument("--etherscan-rps", type=float, default=1.5)
    ap.add_argument("--backend", choices=["simulatev1", "anvil"], default="simulatev1",
                    help="anvil: replay plans as real mined txs on a local anvil fork (slow, concurrency forced to 1, rug off)")
    ap.add_argument("--anvil-port", type=int, default=18545)
    ap.add_argument("--anvil-cups", type=int, default=30, help="anvil upstream compute-units/s (30 ~ 2-3 req/s)")
    ap.add_argument("--redo-errors", action="store_true",
                    help="re-process pairs whose earlier record had a transient failure (Etherscan rate limit, RPC/sim error); newest record wins")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--shard", default=None, help="i/n: process only pairs with index %% n == i")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    ctx = Ctx()
    ctx.mode, ctx.offset = args.mode, args.offset_blocks
    ctx.dust_floor_wei = args.dust_floor_wei
    ctx.measure_liquidity = args.measure_liquidity
    ctx.run = args.run or os.path.basename(out_dir.rstrip("/"))
    ctx.raw_dir = (args.raw_dir or f"data/raw/sim/{ctx.run}").rstrip("/")
    amt = int(args.buy_eth * 10 ** 18)
    ctx.amts = [amt, max(amt // 20, 10 ** 12)]
    ctx.rug = (args.mode == "historical") if args.rug == "auto" else (args.rug == "on")
    ctx.archive = Endpoint(args.archive_url, "archive", rps=args.rps_archive, max_batch=3, inflight=args.concurrency)
    ctx.fresh = Endpoint(args.fresh_url, "fresh", rps=args.rps_fresh, max_batch=8, inflight=args.concurrency)
    ctx.sim_endpoints = [ctx.archive] if args.mode == "historical" else [ctx.fresh, ctx.archive]
    if args.backend == "anvil":
        import anvil_check
        ctx.sim_fn = anvil_check.make_sim_fn(args.archive_url, args.anvil_port, args.anvil_cups)
        args.concurrency = 1
        if args.rug == "auto":
            ctx.rug = False
    ctx.etherscan = None if (args.no_etherscan or not ctx.rug) else RG.Etherscan(rps=args.etherscan_rps)

    # pin head
    heads = []
    for ep in (ctx.archive, ctx.fresh):
        try:
            r = await ep.request("eth_blockNumber", [])
            heads.append(int(r["response"]["result"], 16))
        except Exception:
            pass
    if not heads:
        sys.exit("cannot reach any RPC")
    ctx.head_pinned = min(heads) - 3
    if args.latest_block is not None and args.mode == "latest":
        if args.latest_block > ctx.head_pinned:
            raise ValueError("--latest-block exceeds current pinned head")
        ctx.head_pinned = args.latest_block
    pairs = read_pairs(args.pairs)
    if args.shard:
        i, n = map(int, args.shard.split("/"))
        pairs = [p for k, p in enumerate(pairs) if k % n == i]
    if args.limit:
        pairs = pairs[:args.limit]
    res_path = os.path.join(out_dir, "results.jsonl")
    done = load_done(res_path, args.redo_errors)
    todo = []
    for p in pairs:
        if ctx.mode == "latest":
            blk = ctx.head_pinned
        else:
            blk = p["block"] or ((p["created_block"] + ctx.offset) if p["created_block"] is not None else None)
            blk = min(blk, ctx.head_pinned) if blk is not None else None
        if blk is None:
            continue
        if (p["token"], p["pool"], blk) not in done:
            todo.append(p)
    meta = {"args": vars(args), "run": ctx.run, "head_pinned": ctx.head_pinned, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "n_pairs": len(pairs), "n_todo": len(todo), "rules": CL.RULES}
    with open(os.path.join(out_dir, "run_meta.json"), "a") as f:
        f.write(json.dumps(meta) + "\n")
    print(f"[run {ctx.run}] mode={ctx.mode} head_pinned={ctx.head_pinned} pairs={len(pairs)} todo={len(todo)} rug={ctx.rug}", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    wl = asyncio.Lock()
    counts = {}
    t_start = time.time()
    n_done = 0
    fout = open(res_path, "a")

    async def worker(p):
        nonlocal n_done
        async with sem:
            rec = await process(ctx, p)
        if rec is None:
            return
        async with wl:
            fout.write(json.dumps(rec) + "\n")
            fout.flush()
            counts[rec["primary_label"]] = counts.get(rec["primary_label"], 0) + 1
            n_done += 1
            if n_done % 25 == 0 or n_done == len(todo):
                el = time.time() - t_start
                print(f"[{n_done}/{len(todo)}] {n_done / el:.2f} pairs/s  labels={counts}  archive={ctx.archive.stats} fresh={ctx.fresh.stats}", flush=True)

    await asyncio.gather(*(worker(p) for p in todo))
    fout.close()
    if getattr(ctx, "sim_fn", None):
        ctx.sim_fn.fork.stop()
    await ctx.archive.close()
    await ctx.fresh.close()
    if ctx.etherscan:
        await ctx.etherscan.close()
    print("done", counts, f"{time.time() - t_start:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
