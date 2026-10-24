"""Month boundaries -> block numbers (first block with timestamp >= month start, UTC), via secant search on
eth_getBlockByNumber. Writes data/census/blockmap.csv (month, first_block, last_block, ts_first). Raw responses logged."""
import csv, datetime as dt, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from rpc import RPC, ROOT

rpc = RPC(stream="blockmap", rps=4)
_cache = {}


def ts(n):
    if n not in _cache:
        b = rpc.call("eth_getBlockByNumber", [hex(n), False], store=lambda r: {"number": r["number"], "timestamp": r["timestamp"], "hash": r["hash"]})
        _cache[n] = int(b["timestamp"], 16)
    return _cache[n]


def first_block_at(t, lo, hi):
    # smallest n in [lo,hi] with ts(n) >= t ; ts monotone non-decreasing
    while hi - lo > 1:
        tl, th = ts(lo), ts(hi)
        if tl >= t: return lo
        if th < t: return hi + 1
        guess = lo + int((t - tl) * (hi - lo) / max(1, th - tl))
        guess = min(max(guess, lo + 1), hi - 1)
        # bracket tightening with a small window around the guess
        if ts(guess) >= t:
            hi = guess
            if guess - 1 > lo and ts(guess - 1) < t: return guess
        else:
            lo = guess
    return hi if ts(hi) >= t else hi + 1


def main():
    head = int(rpc.call("eth_blockNumber", []), 16)
    months = []
    y, m = 2017, 1
    while (y, m) <= (2026, 10):
        months.append((y, m)); m += 1
        if m == 13: y, m = y + 1, 1
    starts = [int(dt.datetime(y, m, 1, tzinfo=dt.timezone.utc).timestamp()) for y, m in months]
    firsts, lo = [], 2_000_000
    for (y, m), t in zip(months, starts):
        n = first_block_at(t, lo, head)
        firsts.append(n); lo = n
        print(y, m, n, flush=True)
    out = ROOT / "data/census/blockmap.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["month", "first_block", "last_block", "n_blocks", "ts_first"])
        for i, (y, m) in enumerate(months[:-1]):
            w.writerow([f"{y}-{m:02d}", firsts[i], firsts[i + 1] - 1, firsts[i + 1] - firsts[i], starts[i]])
    rpc.close()


if __name__ == "__main__":
    main()
