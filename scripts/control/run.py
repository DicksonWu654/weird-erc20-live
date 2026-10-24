#!/usr/bin/env python3
"""W3 control & admin classifier.  who can change this token, and how?

    scripts/control/run.py --chain ethereum --tokens tokens.csv --block latest --out data/control/ethereum/

Resumable (rows already in <out>/control.jsonl with completeness=complete are skipped), batched, <=5 req/s per RPC
endpoint, raw evidence saved under <out>/evidence/.  See README.md.
"""
import argparse
import csv
import json
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ctl_classify import Env, probe_token  # noqa: E402
from ctl_rpc import CHAINS, now_iso  # noqa: E402


def read_tokens(path, chain=None):
    p = pathlib.Path(path)
    toks = []
    text = p.read_text()
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return toks
    header = [h.strip().lower() for h in lines[0].split(",")]
    if any(h in header for h in ("address", "token", "token_address", "contract", "contract_address")):
        col = next(h for h in ("address", "token", "token_address", "contract", "contract_address") if h in header)
        rd = csv.DictReader(lines)
        for rec in rd:
            rec = {k.strip().lower(): v for k, v in rec.items()}
            if chain and rec.get("chain") and rec["chain"].strip().lower() != chain:
                continue
            toks.append(rec[col].strip().lower())
    else:
        for l in lines:
            t = l.split(",")[0].strip().split()[0]
            if t.startswith("0x") and len(t) == 42:
                toks.append(t.lower())
    seen, out = set(), []
    for t in toks:
        if len(t) == 42 and t.startswith("0x") and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", required=True, choices=sorted(CHAINS))
    ap.add_argument("--tokens", required=True, help="CSV (address/token column) or one address per line")
    ap.add_argument("--block", default="latest", help="block number or 'latest' (= head-2, pinned and recorded)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None, help="only first N tokens")
    ap.add_argument("--retry-partial", action="store_true", help="re-probe rows whose completeness != complete")
    ap.add_argument("--no-l1", action="store_true", help="skip L1-alias lookups on L2 chains")
    ap.add_argument("--reprobe-unverified", action="store_true", help="re-probe rows whose upgrade authority is entirely unverified (needed after classifier improvements)")
    ap.add_argument("--force", action="store_true", help="re-probe every listed token even if already complete (rows are replaced)")
    ap.add_argument("--rolling", action="store_true", help="re-pin each token to a fresh head-2 block (for long runs on pruned public RPCs); block recorded per row")
    ap.add_argument("--archive", action="store_true", help="prefer archive endpoints (needed for old --block)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    runf = out / "run.json"
    prev = json.loads(runf.read_text()) if runf.exists() else None
    block = a.block
    if prev and a.block == "latest":
        block = str(prev["block"])
    elif prev and a.block != "latest" and str(prev["block"]) != a.block:
        sys.exit(f"{out} already holds a run pinned at block {prev['block']}; use a new --out or --block {prev['block']}")
    env = Env(a.chain, out, verbose=a.verbose, l1=not a.no_l1, block_is_head=(block == "latest" and not a.archive))
    env.rolling = a.rolling
    env.pin_block(block if block != "latest" else None)
    env.load_ctrl_cache()
    if not prev:
        runf.write_text(json.dumps({"chain": a.chain, "block": env.block_int, "block_meta": env.block_meta,
                                    "started": now_iso(), "argv": sys.argv[1:]}, indent=1))
    toks = read_tokens(a.tokens, a.chain)
    if a.limit:
        toks = toks[:a.limit]
    rowf = out / "control.jsonl"
    done = {}
    if rowf.exists():
        for line in rowf.read_text().splitlines():
            try:
                j = json.loads(line)
            except ValueError:
                continue
            done[j["token"]] = j
    def unverified(j):
        ps = j.get("upgrade_paths") or []
        return j.get("upgradeable") == "yes" and ps and all(p.get("assumed_or_unverified") for p in ps)
    def has_unclassified(j):
        return any(n.get("kind") == "unclassified" for n in (j.get("controllers") or {}).values())
    todo = [t for t in toks if t not in done or (a.retry_partial and (done[t].get("completeness") != "complete" or has_unclassified(done[t])))
            or (a.reprobe_unverified and unverified(done[t])) or a.force or done[t].get("status") == "error"]
    print(f"[{a.chain}] block {env.block_int}  tokens {len(toks)}  already done {len(toks) - len(todo)}  to do {len(todo)}",
          flush=True)
    lock = threading.Lock()
    n = 0
    t0 = time.time()
    fh = open(rowf, "a")
    try:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(probe_token, env, t): t for t in todo}
            for f in as_completed(futs):
                row = f.result()
                with lock:
                    fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                    fh.flush()
                    n += 1
                    done[row["token"]] = row
                if n % 10 == 0 or a.verbose:
                    print(f"  {n}/{len(todo)}  {time.time() - t0:.0f}s  rpc stats {env.client.stats}", flush=True)
    finally:
        fh.close()
    # compact: last row per token wins
    rows = {}
    for line in rowf.read_text().splitlines():
        try:
            j = json.loads(line)
            rows[j["token"]] = j
        except ValueError:
            pass
    tmp = rowf.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for t in toks:
            if t in rows:
                f.write(json.dumps(rows[t], separators=(",", ":")) + "\n")
        for t, j in rows.items():
            if t not in set(toks):
                f.write(json.dumps(j, separators=(",", ":")) + "\n")
    tmp.replace(rowf)
    st = {}
    for t in toks:
        r = rows.get(t)
        key = (r or {}).get("status", "missing") + "/" + (r or {}).get("completeness", "-")
        st[key] = st.get(key, 0) + 1
    print(f"done in {time.time() - t0:.0f}s: {st}   rpc {env.client.stats}")


if __name__ == "__main__":
    main()
