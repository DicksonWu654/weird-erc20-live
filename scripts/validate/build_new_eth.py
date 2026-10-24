"""Build the 'new Ethereum tokens' half of the validation subset.

1. Etherscan v2 getLogs (eth.drpc.org free tier rejects eth_getLogs ranges >~100 blocks with misleading errors, so
   Etherscan is used for logs) for Uniswap V2 PairCreated and V3 PoolCreated over the last --days days; every raw
   response is saved under data/raw/validate/etherscan/.
2. Keep pairs whose other side is WETH/USDC/USDT/DAI (so a buy->sell route exists), de-dupe by token.
3. Measure the base-token balance of EVERY pool of every candidate now (batched eth_call balanceOf on publicnode),
   take each token's deepest pool as `best_pool`, and stratify: liquid (base side >= ~$1k), thin ($50-$1k), dust
   (< $50; WETH priced at a fixed $3000, only for bucketing). Observed: ~99% of new V2 pairs are dust (drained or
   spam). We keep the whole candidate frame in data/validate/new_eth_candidates.jsonl and sample all liquid
   tokens available, then thin, then dust, so the subset is not trivially "everything is empty".
4. Etherscan v2 getcontractcreation for the sampled tokens -> keep tokens whose contract was created inside the
   window (so they are genuinely *new tokens*, not old tokens that got a new pool).
5. Write data/validate/subset_new_eth.jsonl.
"""
import argparse, json, os, pathlib, random, sys, time
from collections import Counter, defaultdict
import requests
from common import RAW, VAL, UA, utcnow, sha256_text

RPC = "https://eth.drpc.org"  # head block / timestamp only
RPC_BATCH = "https://ethereum-rpc.publicnode.com"  # latest-state batched eth_call (drpc free: 3 per batch)
V2F = "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"
V3F = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
T_V2 = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
T_V3 = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
BASES = {  # address -> (symbol, decimals, min raw balance for 'liquid')
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18, int(0.3e18)),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6, 1000 * 10**6),
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6, 1000 * 10**6),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18, 1000 * 10**18),
}
S = requests.Session()
S.headers["User-Agent"] = UA
LAST = [0.0]


def rpc(method, params, key=None, retries=5):
    for i in range(retries):
        dt = time.time() - LAST[0]
        if dt < 0.25:
            time.sleep(0.25 - dt)
        LAST[0] = time.time()
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        t0, iso = utcnow()
        try:
            r = S.post(RPC, json=body, timeout=90)
            j = r.json()
            if key:
                d = RAW / "rpc"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{key}.json").write_text(json.dumps({
                    "url": RPC, "request": body, "status": r.status_code, "retrieved_at": iso,
                    "body_sha256": sha256_text(r.text), "body_text": r.text}))
            if "error" in j:
                raise RuntimeError(str(j["error"]))
            return j["result"]
        except Exception as e:  # noqa: BLE001
            print("rpc retry", method, e, file=sys.stderr)
            time.sleep(2 * (i + 1))
    raise RuntimeError("rpc failed " + method)


def esc_get(url, rawkey, apikey, retries=15):
    """Etherscan v2 GET (<=4 req/s), raw response saved under data/raw/validate/etherscan/."""
    cached = RAW / "etherscan" / f"{rawkey}.json"
    if rawkey.startswith("getLogs_") and cached.exists():  # historical log pages are immutable: reuse
        return json.loads(json.loads(cached.read_text())["body_text"])
    for i in range(retries):
        time.sleep(0.4)
        t0, iso = utcnow()
        r = S.get(url, timeout=90)
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            j = {}
        d = RAW / "etherscan"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rawkey}.json").write_text(json.dumps({
            "url": url.replace(apikey, "REDACTED"), "status": r.status_code, "retrieved_at": iso,
            "body_sha256": sha256_text(r.text), "body_text": r.text}))
        if j.get("status") == "1" or j.get("message") in ("No records found", "No data found"):
            return j
        print("etherscan retry", j.get("message"), str(j.get("result"))[:100], file=sys.stderr)
        time.sleep(min(1.0 + i, 8))
    return j


def batch(calls, retries=4):
    for i in range(retries):
        try:
            time.sleep(0.25)
            r = S.post(RPC_BATCH, json=calls, timeout=90).json()
            if isinstance(r, list):
                return {x["id"]: x for x in r}
        except Exception as e:  # noqa: BLE001
            print("batch retry", e, file=sys.stderr)
            r = None
        if isinstance(r, dict):
            print("batch error", str(r)[:200], file=sys.stderr)
        time.sleep(2 * (i + 1))
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--n", type=int, default=300, help="total new-token subset size")
    ap.add_argument("--n-liquid", type=int, default=60, help="max tokens from stratum liquid (best pool >= ~$1k base side)")
    ap.add_argument("--n-thin", type=int, default=90, help="max tokens from stratum thin ($50-$1k); rest is dust")
    ap.add_argument("--seed", type=int, default=20261021)
    a = ap.parse_args()
    key = os.environ.get("ETHERSCAN_API_KEY")
    if not key:
        raise RuntimeError("Set ETHERSCAN_API_KEY in the environment")
    head = int(rpc("eth_blockNumber", []), 16)
    hts = int(rpc("eth_getBlockByNumber", [hex(head), False])["timestamp"], 16)
    start = head - a.days * 7200
    print("head", head, "start", start)
    cands = {}  # token -> dict
    for fac, topic, kind in ((V2F, T_V2, "v2"), (V3F, T_V3, "v3")):
        for lo in range(start, head + 1, 30000):
            hi = min(lo + 29999, head)
            logs, page = [], 1
            while True:
                url = ("https://api.etherscan.io/v2/api?chainid=1&module=logs&action=getLogs"
                       f"&fromBlock={lo}&toBlock={hi}&address={fac}&topic0={topic}&page={page}&offset=1000&apikey={key}")
                j = esc_get(url, f"getLogs_{kind}_{lo}_{hi}_p{page}", key)
                res = j.get("result")
                if not isinstance(res, list):
                    if j.get("message") == "No records found":
                        break
                    raise RuntimeError(f"etherscan getLogs: {j}")
                logs += res
                if len(res) < 1000:
                    break
                page += 1
            for lg in logs:
                t0 = "0x" + lg["topics"][1][-40:]
                t1 = "0x" + lg["topics"][2][-40:]
                data = lg["data"][2:]
                pool = "0x" + (data[24:64] if kind == "v2" else data[64 + 24:128])
                for tok, base in ((t0, t1), (t1, t0)):
                    if base in BASES and tok not in BASES:
                        c = cands.setdefault(tok, {"token": tok, "pools": []})
                        c["pools"].append({"kind": kind, "pool": pool, "base": base,
                                           "block": int(lg["blockNumber"], 16),
                                           "tx": lg["transactionHash"],
                                           "fee": int(lg["topics"][3], 16) if kind == "v3" else None})
            print(kind, lo, len(logs), "cands", len(cands), flush=True)
    print("candidate tokens", len(cands))
    # --- liquidity now: balanceOf(base, pool) for EVERY pool of every candidate (batched, publicnode, latest) ---
    rows = list(cands.values())
    allp = [(r, p) for r in rows for p in r["pools"]]
    ETH_USD = 3000.0  # only used to put WETH and stablecoin pools on one rough scale for stratification
    for i in range(0, len(allp), 20):
        chunk = allp[i:i + 20]
        calls = [{"jsonrpc": "2.0", "id": j, "method": "eth_call",
                  "params": [{"to": p["base"], "data": "0x70a08231" + "0" * 24 + p["pool"][2:]}, "latest"]}
                 for j, (r, p) in enumerate(chunk)]
        res = batch(calls)
        if not res:
            raise RuntimeError("liquidity batch failed")
        for j, (r, p) in enumerate(chunk):
            x = res.get(j, {})
            bal = int(x["result"], 16) if x.get("result", "0x") not in ("0x", None) else None
            p["base_balance_raw"] = bal
            sym, dec, _ = BASES[p["base"]]
            p["base_usd_approx"] = None if bal is None else (bal / 10**dec) * (ETH_USD if sym == "WETH" else 1.0)
        if (i // 20) % 40 == 0:
            print("liq", i, "/", len(allp), flush=True)
    for r in rows:
        ok = [p for p in r["pools"] if p["base_usd_approx"] is not None]
        r["best_pool"] = max(ok, key=lambda p: p["base_usd_approx"]) if ok else min(r["pools"], key=lambda p: p["block"])
        u = r["best_pool"]["base_usd_approx"] or 0.0
        # one side of the pool is the base token, so total pool value ~ 2x for V2; stratify on the base side alone
        r["liq_stratum"] = "liquid" if u >= 1000 else "thin" if u >= 50 else "dust"
    VAL.mkdir(parents=True, exist_ok=True)
    with open(VAL / "new_eth_candidates.jsonl", "w") as f:  # full candidate frame (no sampling) for denominators
        for r in rows:
            f.write(json.dumps({"token": r["token"], "stratum": r["liq_stratum"], "best_pool": r["best_pool"],
                                "n_pools": len(r["pools"])}) + "\n")
    cnt = Counter((r["best_pool"]["kind"], r["liq_stratum"]) for r in rows)
    print("candidate frame by (kind, stratum):", dict(cnt))
    # --- sample: contract creation (Etherscan, 5 per call) filters to genuinely new tokens, so check all strata
    rng = random.Random(a.seed)
    by = defaultdict(list)
    for r in rows:
        by[r["liq_stratum"]].append(r)
    for g in by.values():
        rng.shuffle(g)
    want = {"liquid": a.n_liquid, "thin": a.n_thin, "dust": a.n}
    # oversample for the age filter; dust fills the remainder to reach a.n total
    pre = by["liquid"][:int(a.n_liquid * 1.6)] + by["thin"][:int(a.n_thin * 1.6)] + by["dust"][:int(a.n * 1.3)]
    toks = [r["token"] for r in pre]
    for i in range(0, len(toks), 5):
        grp = toks[i:i + 5]
        url = ("https://api.etherscan.io/v2/api?chainid=1&module=contract&action=getcontractcreation"
               f"&contractaddresses={','.join(grp)}&apikey={key}")
        j = esc_get(url, f"getcontractcreation_{grp[0]}", key)
        for x in (j.get("result") or []) if isinstance(j.get("result"), list) else []:
            for rr in pre:
                if rr["token"] == x["contractAddress"].lower():
                    rr["created_block"] = int(x["blockNumber"]) if x.get("blockNumber") else None
                    rr["created_ts"] = int(x["timestamp"]) if x.get("timestamp") else None
                    rr["creator"] = x.get("contractCreator")
                    rr["creation_tx"] = x.get("txHash")
        if (i // 5) % 10 == 0:
            print("creation", i, "/", len(toks), flush=True)
    keep = [r for r in pre if r.get("created_block") and r["created_block"] >= start]
    print("after contract-age filter:", len(keep), "of", len(pre))
    final = []
    for g, n in (("liquid", a.n_liquid), ("thin", a.n_thin)):
        final += [r for r in keep if r["liq_stratum"] == g][:n]
    dust = [r for r in keep if r["liq_stratum"] == "dust"]
    final += dust[:max(0, a.n - len(final))]
    with open(VAL / "subset_new_eth.jsonl", "w") as f:
        for r in final:
            fp = r["best_pool"]
            f.write(json.dumps({
                "chain": 1, "token": r["token"], "source": f"new_eth_uniswap_{fp['kind']}",
                "stratum": r["liq_stratum"], "pool": fp["pool"], "pool_kind": fp["kind"], "pool_fee": fp["fee"],
                "base": fp["base"], "base_symbol": BASES[fp["base"]][0],
                "base_balance_raw_at_build": fp["base_balance_raw"], "base_usd_approx_at_build": fp["base_usd_approx"],
                "pool_created_block": fp["block"], "pool_created_tx": fp["tx"], "n_pools_in_window": len(r["pools"]),
                "token_created_block": r.get("created_block"), "token_created_ts": r.get("created_ts"),
                "build_head_block": head, "build_head_ts": hts, "seed": a.seed}) + "\n")
    print("wrote", len(final), dict(Counter(r["liq_stratum"] for r in final)))


if __name__ == "__main__":
    main()
