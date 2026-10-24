"""Liquidity-pull ("rug") vs organic death analysis of a DEX pool.

Level 1 -- screen (archive RPC, one batched request): quote-side liquidity at
    created+1 block (~1h), created+50400 (~7d), head, plus the reserve seen by the simulator at the sim block.
Level 2 -- detail (Etherscan v2 getLogs, only for pools whose liquidity fell >=50% from peak): Mint / Burn / LP-token
    Transfer logs of the pair -> who removed liquidity and how much, vs. liquidity that just decayed through sells.

Verdicts (documented in README):
    rug_lp_pull   : final quote reserve <= 10% of peak AND >= 50% of peak removed by "insider" LP burns
    died_by_sells : final quote reserve <= 10% of peak AND insiders removed < 20% of peak (price collapsed via swaps)
    partial_*/mixed otherwise.  insider = first LP-token recipient (+ addresses it forwarded LP tokens to).
"""
import asyncio, gzip, json, os, re, time
import aiohttp
from abi import *
from rpc import RpcTransportError

T_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
V2_MINT = "0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"
V2_BURN = "0xdccd412f0b1252819cb1fd330b93224ca42612892bb3f4f789976e6d81936496"
V3_BURN = "0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c"
V3_MINT = "0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde"
V1_ADD = "0x06239653922ac7bea6aa2b19dc486b9361821d37712eb796adfd38d81de278ca"
V1_REM = "0x0fbf06c058b90cb038a618f8c2acbf6145f8b3570fd1fa56abb8f0f3f05b36e8"
SAMPLE_BLOCKS = [("c+1", 1), ("c+7d", 50400)]


def etherscan_key():
    return os.environ.get("ETHERSCAN_API_KEY")


class Etherscan:
    """Polite v2 client (default 2 req/s; the key is shared with other workstreams). Key never stored in raw logs."""

    def __init__(self, rps=2.0, key=None, cache_dir=None):
        self.key, self.rps = key or etherscan_key(), rps
        self.cache_dir = cache_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "raw", "sim", "_etherscan_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self._lock = asyncio.Lock()
        self._next = 0.0
        self._sess = None

    async def get(self, **params):
        if not self.key:
            raise RpcTransportError("no ETHERSCAN_API_KEY")
        if self._sess is None:
            self._sess = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        q = dict(chainid=1, **params)
        import hashlib
        ck = os.path.join(self.cache_dir, hashlib.sha256(json.dumps(q, sort_keys=True).encode()).hexdigest() + ".json")
        # logs queries with toBlock=latest are not immutable: only cache when toBlock is numeric
        cacheable = str(q.get("toBlock", "")).isdigit()
        if cacheable and os.path.exists(ck):
            return json.load(open(ck))
        delay = 1.0
        for attempt in range(10):
            async with self._lock:
                now = time.monotonic()
                wait = self._next - now
                self._next = max(now, self._next) + 1 / self.rps
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._sess.get("https://api.etherscan.io/v2/api", params={**q, "apikey": self.key}) as r:
                    j = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                j = {"status": "0", "message": "NOTOK", "result": repr(e)}
            res = j.get("result")
            if isinstance(res, str) and ("rate limit" in res.lower() or "max calls" in res.lower() or "busy" in res.lower()):
                await asyncio.sleep(delay)
                delay = min(delay * 2, 40)
                continue
            out = {"request": {"url": "https://api.etherscan.io/v2/api", "params": q}, "response": j,
                   "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            if cacheable and isinstance(res, list):
                json.dump(out, open(ck, "w"))
            return out
        raise RpcTransportError("etherscan rate-limited")

    async def close(self):
        if self._sess:
            await self._sess.close()


# ------------------------------------------------------------------------------------------ level 1
async def quote_liquidity_at(ctx, rl, kind, pool, q_idx, blocks):
    """Quote-side liquidity at each block (list aligned to `blocks`; entries are dicts, {"error":..} where unavailable)."""
    ep = ctx.archive
    futs = []
    for b in blocks:
        tag = hex(b)
        if kind == "v2":
            futs.append(ep.request("eth_call", [{"to": pool, "data": enc("getReserves()")}, tag]))
        elif kind == "v3":
            futs.append(ep.request("eth_call", [{"to": WETH, "data": enc("balanceOf(address)", pool)}, tag]))
        else:
            futs.append(ep.request("eth_getBalance", [pool, tag]))
    outs = await asyncio.gather(*futs, return_exceptions=True)
    vals = []
    for b, raw in zip(blocks, outs):
        if isinstance(raw, Exception):
            vals.append({"error": str(raw)})
            continue
        rl.add(f"rug_sample:{b}", raw)
        r = raw["response"]
        if "error" in r:
            vals.append({"error": json.dumps(r["error"])[:200]})
            continue
        h = r["result"]
        try:
            if kind == "v2":
                words = [int(h[2 + 64 * i: 2 + 64 * (i + 1)], 16) for i in range(2)]
                vals.append({"quote": words[q_idx], "token": words[1 - q_idx]})
            else:
                vals.append({"quote": int(h, 16)})
        except Exception as e:
            vals.append({"error": f"parse:{e!r}", "raw": h[:80]})
    return vals


async def rug_screen(ctx, rl, token, pool, kind, quote_is_token0, created_block, head, sim_reserve=None, sim_block=None):
    """Level-1 screen. quote_is_token0 only matters for v2."""
    q_idx = 0 if quote_is_token0 else 1
    blocks = [min(created_block + off, head) for _, off in SAMPLE_BLOCKS] + [head]
    vals = await quote_liquidity_at(ctx, rl, kind, pool, q_idx, blocks)
    series = [{"label": lbl, "block": b, **v} for (lbl, _), b, v in zip(SAMPLE_BLOCKS + [("head", 0)], blocks, vals)]
    if sim_reserve is not None:
        series.append({"label": "sim_block", "block": sim_block, "quote": sim_reserve})
    qs = [s["quote"] for s in series if s.get("quote") is not None]
    out = {"series": series, "created_block": created_block, "head": head}
    if not qs:
        out["status"] = "unavailable"
        return out
    peak = max(qs)
    final = next((s["quote"] for s in series if s["label"] == "head" and s.get("quote") is not None), None)
    out.update(status="ok", peak_quote=peak, final_quote=final,
               ratio_final_peak=(final / peak) if (final is not None and peak > 0) else None)
    for s in series:
        if s["label"] == "c+7d" and s.get("quote") is not None and peak > 0:
            out["ratio_7d_peak"] = s["quote"] / peak
    return out


# ------------------------------------------------------------------------------------------ level 2
def _log_key(l):
    return (int(l["blockNumber"], 16), int(l["logIndex"], 16) if l["logIndex"] not in ("0x", "") else 0)


def _addr(topic):
    return "0x" + topic[-40:]


async def fetch_logs(es, rl, label, address, topic0, max_pages=2, offset=1000, to_block=None, **extra):
    out = []
    for page in range(1, max_pages + 1):
        raw = await es.get(module="logs", action="getLogs", address=address, fromBlock=0, toBlock=to_block if to_block is not None else "latest",
                           topic0=topic0, page=page, offset=offset, **extra)
        rl.add(f"{label}:p{page}", raw)
        j = raw["response"]
        res = j.get("result")
        if not isinstance(res, list):
            if isinstance(res, str) and "No records" in res:
                break
            if j.get("message") == "No records found":
                break
            raise RpcTransportError(f"etherscan:{j.get('message')}:{str(res)[:100]}")
        out += res
        if len(res) < offset:
            break
    return out, (len(out) >= max_pages * offset)


async def rug_detail(es, rl, token, pool, kind, quote_is_token0, created_block, head=None):
    q = 0 if quote_is_token0 else 1
    d = {"truncated": False}
    if kind == "v2":
        burns, t1 = await fetch_logs(es, rl, "es_burn", pool, V2_BURN, to_block=head)
        mints, t2 = await fetch_logs(es, rl, "es_mint", pool, V2_MINT, to_block=head)
        xfers, t3 = await fetch_logs(es, rl, "es_lp_transfer", pool, T_TRANSFER, to_block=head)
        d["truncated"] = t1 or t2 or t3
        zero = "0x" + "00" * 20
        # LP flows
        xfers.sort(key=_log_key)
        first = next((x for x in xfers if _addr(x["topics"][1]) == zero and _addr(x["topics"][2]) != zero), None)
        insiders = set()
        if first:
            insiders.add(_addr(first["topics"][2]))
        dead = DEAD.lower()
        lp_supply, lp_dead, lp_insider_dead = 0, 0, 0
        burn_senders = {}   # tx -> {from: lp value} for LP sent to pair
        for x in xfers:
            f, t, v = _addr(x["topics"][1]), _addr(x["topics"][2]), int(x["data"], 16)
            if f == zero:
                lp_supply += v
            if t == zero and f == pool.lower():
                lp_supply -= v
            if f in insiders and t not in (zero, pool.lower(), dead):
                insiders.add(t)
            if t == dead:
                lp_dead += v
            if t == pool.lower():
                burn_senders.setdefault(x["transactionHash"], {}).setdefault(f, 0)
                burn_senders[x["transactionHash"]][f] += v
        # re-evaluate insider membership of burn senders (closure could extend after earlier transfers; one more pass)
        tot_burn_q = tot_ins_burn_q = 0
        cum = peak_flow = 0
        mint_q_by_tx = {}
        ev = []
        for m in mints:
            a = int(m["data"][2:66], 16), int(m["data"][66:130], 16)
            ev.append((_log_key(m), "mint", m["transactionHash"], a[q]))
        for b in burns:
            a = int(b["data"][2:66], 16), int(b["data"][66:130], 16)
            ev.append((_log_key(b), "burn", b["transactionHash"], a[q], int(b["blockNumber"], 16)))
        ev.sort(key=lambda e: e[0])
        burn_rows = []
        for e in ev:
            if e[1] == "mint":
                cum += e[3]
                peak_flow = max(peak_flow, cum)
            else:
                cum -= e[3]
                senders = burn_senders.get(e[2], {})
                ins = sum(v for f, v in senders.items() if f in insiders)
                allv = sum(senders.values()) or 1
                share_ins = ins / allv if senders else 0.0
                tot_burn_q += e[3]
                tot_ins_burn_q += e[3] * share_ins
                burn_rows.append({"block": e[4], "tx": e[2], "quote_out": e[3], "insider_share": round(share_ins, 4),
                                  "lp_senders": list(senders)[:3]})
        d.update(n_mint=len(mints), n_burn=len(burns), first_lp_provider=_addr(first["topics"][2]) if first else None,
                 insiders=sorted(insiders)[:8], quote_burned_total=tot_burn_q, quote_burned_by_insiders=tot_ins_burn_q,
                 quote_flow_peak=peak_flow, lp_supply_net=lp_supply, lp_sent_to_dead=lp_dead,
                 lp_dead_frac=(lp_dead / lp_supply if lp_supply > 0 else None), burns=burn_rows[:20], burns_all=burn_rows)
    elif kind == "v3":
        burns, t1 = await fetch_logs(es, rl, "es_v3_burn", pool, V3_BURN, to_block=head)
        tot = 0
        rows = []
        for b in burns:
            # data: amount(uint128), amount0, amount1
            a0, a1 = int(b["data"][2 + 64 * 1:2 + 64 * 2], 16), int(b["data"][2 + 64 * 2:2 + 64 * 3], 16)
            tot += (a0, a1)[q]
            rows.append({"block": int(b["blockNumber"], 16), "quote_out": (a0, a1)[q], "tx": b["transactionHash"]})
        d.update(n_burn=len(burns), quote_burned_total=tot, quote_burned_by_insiders=None, burns=rows[:20], burns_all=[dict(r, insider_share=1.0) for r in rows], truncated=t1,
                 attribution="unavailable_v3")
    else:  # v1: events carry the provider
        rem, t1 = await fetch_logs(es, rl, "es_v1_remove", pool, V1_REM, to_block=head)
        add, t2 = await fetch_logs(es, rl, "es_v1_add", pool, V1_ADD, to_block=head)
        add.sort(key=_log_key)
        first = _addr(add[0]["topics"][1]) if add else None
        tot = ins = 0
        cum = peak = 0
        for e in sorted([(a, 1) for a in add] + [(r, -1) for r in rem], key=lambda t: _log_key(t[0])):
            amt = int(e[0]["topics"][2], 16)
            cum += e[1] * amt
            peak = max(peak, cum)
            if e[1] < 0:
                tot += amt
                if _addr(e[0]["topics"][1]) == first:
                    ins += amt
        d.update(n_add=len(add), n_remove=len(rem), first_lp_provider=first, quote_burned_total=tot, quote_burned_by_insiders=ins,
                 quote_flow_peak=peak, truncated=t1 or t2)
    return d


# ------------------------------------------------------------------------------------------ verdict
RUG_WINDOW_DAYS = 30


def _cross_block(detail, peak_q, created_block, key="insider"):
    """first burn at which cumulative (insider-attributed) removal reaches 50% of peak; None if never / unknown."""
    cum = 0
    for r in sorted(detail.get("burns_all") or detail.get("burns") or [], key=lambda r: r["block"]):
        cum += r["quote_out"] * (r["insider_share"] if key == "insider" else 1.0)
        if cum >= 0.5 * peak_q:
            return r["block"], round((r["block"] - created_block) * 12 / 86400, 2)
    return None, None


def rug_verdict(screen, detail):
    """-> dict(verdict, ...). verdict in none | rug_lp_pull | rug_lp_pull_unattributed | lp_pulled_late | died_by_sells |
    partial_rug_lp_pull | liquidity_declined_mixed | liquidity_declined_screen_only | unknown.
    rug_* only if the 50% pull happened within RUG_WINDOW_DAYS (30) of pool creation; later pulls are `lp_pulled_late`
    (legit migrations to V3 / new pools look like that)."""
    v = {"verdict": "unknown"}
    if not screen or screen.get("status") != "ok":
        return v
    peak, final = screen["peak_quote"], screen["final_quote"]
    if final is None or peak <= 0:
        return v
    ratio = final / peak
    v.update(verdict="none", ratio_final_peak=round(ratio, 5))
    if ratio > 0.15:
        return v
    if not detail:
        v["verdict"] = "liquidity_declined_screen_only"
        return v
    peak_q = max(peak, detail.get("quote_flow_peak") or 0)
    ins = detail.get("quote_burned_by_insiders")
    tot = detail.get("quote_burned_total") or 0
    v.update(peak_quote=peak_q, burned_total_frac_of_peak=round(tot / peak_q, 4) if peak_q else None, truncated=detail.get("truncated"))
    created = screen["created_block"]
    if ins is None:  # V3: no attribution
        if ratio <= 0.1 and tot >= 0.5 * peak_q:
            blk, days = _cross_block(detail, peak_q, created, key="all")
            v.update(rug_block=blk, rug_days_after_creation=days)
            v["verdict"] = "rug_lp_pull_unattributed" if (days is not None and days <= RUG_WINDOW_DAYS) else "lp_pulled_late"
        elif ratio <= 0.1 and tot < 0.2 * peak_q:
            v["verdict"] = "died_by_sells"
        else:
            v["verdict"] = "liquidity_declined_mixed"
        return v
    v["insider_removed_frac_of_peak"] = round(ins / peak_q, 4) if peak_q else None
    if ratio <= 0.1 and ins >= 0.5 * peak_q:
        blk, days = _cross_block(detail, peak_q, created)
        v.update(rug_block=blk, rug_days_after_creation=days)
        v["verdict"] = "rug_lp_pull" if (days is not None and days <= RUG_WINDOW_DAYS) else "lp_pulled_late"
    elif ratio <= 0.1 and ins < 0.2 * peak_q:
        v["verdict"] = "died_by_sells"
    elif ins >= 0.3 * peak_q:
        v["verdict"] = "partial_rug_lp_pull"
    else:
        v["verdict"] = "liquidity_declined_mixed"
    return v
