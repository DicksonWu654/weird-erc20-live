"""Query honeypot.is and GoPlus for every token in the validation subset; resumable; everything raw-cached.

  python query_providers.py [--subset data/validate/subset.jsonl] [--providers honeypot,goplus,honeypot_pinned]
                            [--limit N] [--tag run1]

Provider queues run concurrently (separate rate limiters). A token is skipped if a usable (non-429/5xx, non-GoPlus-
4029) envelope for that provider already exists with retrieved_at >= --since (default: the run's start, stored in
data/validate/query_runs.jsonl so a resumed run keeps its original window). Each run appends one record to
data/validate/query_runs.jsonl with exact start/end UTC times and counters -- latest-state answers drift, so the
simulator should be re-run close to these times.
"""
import argparse, calendar, json, threading, time, queue, collections
from common import VAL, utcnow, latest_envelope, load_envelope
import providers as P


def usable(provider, chain, addr, since_ms):
    p, e = latest_envelope(provider, chain, addr, ok_only=True)
    return bool(e and e["retrieved_at_epoch_ms"] >= since_ms)


def worker(name, items, since_ms, stats, lock, stop):
    for it in items:
        if stop.is_set():
            return
        chain, addr = it["chain"], it["token"]
        if name == "honeypot":
            if chain not in P.HONEYPOT_CHAINS:
                continue
            fn = lambda: P.honeypot_is_honeypot(chain, addr)
            store = "honeypot"
        elif name == "honeypot_pinned":
            if chain not in P.HONEYPOT_CHAINS or not it.get("pool"):
                continue
            fn = lambda: P.honeypot_is_honeypot(chain, addr, pair=it["pool"])
            store = "honeypot_pinned"
        elif name == "goplus":
            if chain not in P.GOPLUS_CHAINS:
                continue
            fn = lambda: P.goplus_token_security(chain, addr)
            store = "goplus"
        elif name == "honeypot_pairs":
            if chain not in P.HONEYPOT_CHAINS:
                continue
            fn = lambda: P.honeypot_get_pairs(chain, addr)
            store = "honeypot_pairs"
        else:
            raise SystemExit("unknown provider " + name)
        if usable(store, chain, addr, since_ms):
            with lock:
                stats[name]["skipped"] += 1
            continue
        path, env = fn()
        with lock:
            stats[name]["queried"] += 1
            stats[name][f"http_{env['status']}"] += 1
            n = stats[name]["queried"]
        if n % 25 == 0:
            print(name, dict(stats[name]), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subset", default=str(VAL / "subset.jsonl"))
    ap.add_argument("--providers", default="honeypot,goplus")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--since", help="ISO time; resume window start (default: now)")
    a = ap.parse_args()
    items = [json.loads(l) for l in open(a.subset)]
    if a.limit:
        items = items[:a.limit]
    t0, iso0 = utcnow()
    since_ms = int(t0 * 1000)
    if a.since:
        since_ms = calendar.timegm(time.strptime(a.since, "%Y-%m-%dT%H:%M:%SZ")) * 1000
    stats = collections.defaultdict(collections.Counter)
    lock, stop = threading.Lock(), threading.Event()
    names = a.providers.split(",")
    ths = [threading.Thread(target=worker, args=(n, items, since_ms, stats, lock, stop)) for n in names]
    [t.start() for t in ths]
    try:
        [t.join() for t in ths]
    except KeyboardInterrupt:
        stop.set()
        [t.join() for t in ths]
    t1, iso1 = utcnow()
    rec = {"tag": a.tag, "subset": a.subset, "n_items": len(items), "providers": names, "started_at": iso0,
           "finished_at": iso1, "since_ms": since_ms, "stats": {k: dict(v) for k, v in stats.items()}}
    with open(VAL / "query_runs.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec, indent=1))


if __name__ == "__main__":
    main()
