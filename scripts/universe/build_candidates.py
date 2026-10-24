"""Build the per-chain candidate universe from saved raw sources (no network).

Sources (all under data/raw/):
  coingecko/coins_list_include_platform.json   per-chain contract addresses (CoinGecko ids)
  coingecko/markets_pNNN.json                  market cap / volume / global rank (order=market_cap_desc)
  coingecko/markets_ids_pNNN_<h>.json          same fields for coins CG leaves out of its global ranking (fetched by id)
  coingecko/cat_<category>_pNNN.json           category membership (markets?category=...), optional
  blockscout-<host>/tokens_holders_pNNN.json   holders-sorted ERC-20 lists (ethereum, optimism only)
  tokenlists/coingecko_<platform>_all.json, uniswap_default.json   listing cross-check

INCLUSION RULE (per chain, target N = 2000 for ethereum, 500 otherwise):
  1. Valid-address CG coins with a contract on the chain, ordered by global CG market cap (desc): take the top N.
  2. If fewer than N have a market cap, fill the remaining slots with CG-listed coins that have 24h volume (volume desc).
  3. Plus every token on the Uniswap Labs default list (tokens.uniswap.org) for that chainId that is not already included
     (source=uniswap_default_list; a curated list that catches major tokens CG's platform map misses, e.g. cbBTC on ethereum).
  4. Plus liquid DEX tokens from GeckoTerminal: tokens in the top-200 pools by 24h volume (per network) with pool reserve >= $100k,
     ranked by summed pool volume, top M_GT (100 ethereum, 50 others) not already included, not spam-shaped (source=geckoterminal_dex_volume).
  5. Plus "holders extras" (ethereum, optimism): the top M by Blockscout holder count (M = 10% of N) among tokens NOT
     already included that pass the corroboration test: Blockscout has a price for it OR it is on a CG/Uniswap token list,
     AND its name/symbol is not URL/airdrop-lure shaped. Airdrop-lure spam dominates holder-sorted lists, so
     uncorroborated high-holder tokens are recorded in exclusions with reason, not silently dropped.
  6. Hard exclusions (recorded): invalid address, native-gas placeholder (0xeeee..), zero address. (ERC-721/1155
     detection happens on-chain in verify_onchain.py / finalize.py.)
Dedup: one row per address; if several CG ids share an address the highest-market-cap id is primary, the rest go to cg_ids_alt.
"""
import csv, glob, json, re, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from common import *
from eth_utils import to_checksum_address

ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
NATIVE = {"0x" + "ee" * 20: "native gas token placeholder (0xeeee...)", "0x" + "00" * 20: "zero address"}
SPAM = re.compile(r"https?:|www\.|\.(com|io|xyz|net|org|app|top|cc|club|site|vip|fun|live|online|gift|claims?|network|finance|co|me|info|cfd|rest|lol|store|pro|one|website|ws|asia)\b|airdrop|claim|reward|voucher|giveaway|bonus|visit |telegram|free|[✅\U0001F381\U0001F7E2\U0001F7E9\U0001F48E\U0001F511☑]", re.I)
LATIN_MIX = re.compile(r"[Ѐ-ӿͰ-Ͽ]")  # Cyrillic/Greek homoglyph scripts in a token name/symbol


def load(p):
    return json.load(open(p))


def load_markets():
    mk = {}
    for p in sorted(glob.glob(str(RAW / "coingecko" / "markets_p*.json"))):
        for c in load(p):
            mk[c["id"]] = c
    # coins CG omits from its global ranking (no market_cap_rank: stETH, WETH, WBTC, bridged stables...) fetched by id
    for p in sorted(glob.glob(str(RAW / "coingecko" / "markets_ids_p*.json"))):
        for c in load(p):
            mk.setdefault(c["id"], c)
    return mk


def load_categories():
    cats = {}
    for p in sorted(glob.glob(str(RAW / "coingecko" / "cat_*_p*.json"))):
        cat = re.match(r"cat_(.+)_p\d+\.json", pathlib.Path(p).name).group(1)
        for c in load(p):
            cats.setdefault(c["id"], set()).add(cat)
    return cats


def load_blockscout(host):
    rows = []
    for p in sorted(glob.glob(str(RAW / f"blockscout-{host}" / "tokens_holders_p*.json"))):
        rows += load(p)["items"]
    return rows


def main(chain, M=None):
    cfg = CHAINS[chain]; N = cfg["target"]
    M = M if M is not None else N // 10
    lst = load(RAW / "coingecko" / "coins_list_include_platform.json")
    mk = load_markets(); cats = load_categories()
    plat = cfg["cg"]
    # token lists
    cg_tl = {t["address"].lower() for t in load(RAW / "tokenlists" / f"coingecko_{plat}_all.json")["tokens"]}
    uni_tl = {t["address"].lower() for t in load(RAW / "tokenlists" / "uniswap_default.json")["tokens"] if t["chainId"] == cfg["chain_id"]}
    excl = []
    by_addr = {}
    for c in lst:
        a = (c.get("platforms") or {}).get(plat)
        if not a:
            continue
        a_s = a.strip()
        al = a_s.lower()
        m = mk.get(c["id"], {})
        rec = dict(cg_id=c["id"], symbol=c["symbol"], name=c["name"], address_raw=a_s,
                   market_cap_usd=m.get("market_cap"), volume_24h=m.get("total_volume"), cg_market_cap_rank=m.get("market_cap_rank"),
                   price_usd=m.get("current_price"))
        if not ADDR.match(a_s):
            excl.append(dict(rec, reason="invalid_address_format", source="coingecko")); continue
        if al in NATIVE:
            excl.append(dict(rec, reason=NATIVE[al], source="coingecko")); continue
        by_addr.setdefault(al, []).append(rec)
    rows = []
    for al, recs in by_addr.items():
        recs.sort(key=lambda r: -(r["market_cap_usd"] or 0))
        p = dict(recs[0]); p["address"] = to_checksum_address(al)
        p["cg_ids_alt"] = ";".join(r["cg_id"] for r in recs[1:])
        rows.append(p)
    # blockscout attach
    bs_host = {"ethereum": "eth", "optimism": "optimism"}.get(chain)
    bs = {}
    if bs_host:
        for i, r in enumerate(load_blockscout(bs_host), 1):
            bs[r["address_hash"].lower()] = dict(r, bs_rank_holders=i)
    cg_by_addr = {r["address"].lower(): r for r in rows}
    for r in rows:
        b = bs.get(r["address"].lower())
        r["holders"] = int(b["holders_count"]) if b and b.get("holders_count") else None
        r["bs_price"] = b.get("exchange_rate") if b else None
        r["bs_rank_holders"] = b["bs_rank_holders"] if b else None
        r["bs_name"] = b.get("name") if b else None
        r["bs_symbol"] = b.get("symbol") if b else None
    # rank by mcap among valid CG-on-chain coins
    withcap = sorted([r for r in rows if (r["market_cap_usd"] or 0) > 0], key=lambda r: -r["market_cap_usd"])
    for i, r in enumerate(withcap, 1):
        r["rank_mcap_chain"] = i
    nocap = sorted([r for r in rows if not (r["market_cap_usd"] or 0) > 0], key=lambda r: -(r["volume_24h"] or 0))
    for r in withcap + nocap:
        r.setdefault("rank_mcap_chain", None)
    sel = []
    for r in withcap[:N]:
        r["source"] = "coingecko_mcap"; sel.append(r)
    if len(sel) < N:
        for r in nocap:
            if len(sel) >= N: break
            if (r["volume_24h"] or 0) > 0:
                r["source"] = "coingecko_volume_no_mcap"; sel.append(r)
    chosen = {r["address"].lower() for r in sel}
    # Uniswap default-list extras (curated list; catches major tokens whose CG platform address is missing/mismatched)
    uni_tokens = {t["address"].lower(): t for t in load(RAW / "tokenlists" / "uniswap_default.json")["tokens"] if t["chainId"] == cfg["chain_id"]}
    for al, t in sorted(uni_tokens.items(), key=lambda kv: (kv[1]["symbol"].lower(), kv[0])):
        if al in chosen or not ADDR.match(al): continue
        if al in NATIVE: continue
        r = cg_by_addr.get(al) or dict(cg_id="", symbol=t["symbol"], name=t["name"], address=to_checksum_address(al), cg_ids_alt="",
                                       market_cap_usd=None, volume_24h=None, cg_market_cap_rank=None, price_usd=None, rank_mcap_chain=None,
                                       holders=None, bs_price=None, bs_rank_holders=None, bs_name=None, bs_symbol=None)
        r["source"] = "uniswap_default_list"; sel.append(r); chosen.add(al)
    # GeckoTerminal liquid-token extras (top DEX pools by 24h volume; pool reserve >= $100k; not spam-shaped)
    GT_NET = {"ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum", "bsc": "bsc", "polygon": "polygon", "optimism": "optimism"}[chain]
    gt = {}
    for pth in sorted(glob.glob(str(RAW / "geckoterminal" / f"{GT_NET}_pools_vol_p*.json"))):
        d = load(pth)
        toks = {t["id"]: t["attributes"] for t in d.get("included", []) if t["type"] == "token"}
        for pool in d["data"]:
            at = pool["attributes"]
            res = float(at.get("reserve_in_usd") or 0); vol = float((at.get("volume_usd") or {}).get("h24") or 0)
            for side in ("base_token", "quote_token"):
                tid = pool["relationships"][side]["data"]["id"]
                ta = toks.get(tid)
                if not ta: continue
                g = gt.setdefault(ta["address"].lower(), dict(symbol=ta["symbol"], name=ta["name"], cg=ta.get("coingecko_coin_id"), vol=0.0, res=0.0))
                g["vol"] += vol; g["res"] = max(g["res"], res)
    M_GT = 100 if chain == "ethereum" else 50
    n_gt = 0
    for al, g in sorted(gt.items(), key=lambda kv: -kv[1]["vol"]):
        if al in chosen or n_gt >= M_GT or al in NATIVE or not ADDR.match(al): continue
        if g["res"] < 100_000: continue
        nm = f"{g['name'] or ''} {g['symbol'] or ''}"
        if SPAM.search(nm) or LATIN_MIX.search(nm):
            excl.append(dict(cg_id=g.get("cg") or "", symbol=g["symbol"], name=g["name"], address_raw=al, reason="geckoterminal_spam_pattern_name",
                             source="geckoterminal")); continue
        r = cg_by_addr.get(al) or dict(cg_id=g.get("cg") or "", symbol=g["symbol"], name=g["name"], address=to_checksum_address(al), cg_ids_alt="",
                                       market_cap_usd=None, volume_24h=None, cg_market_cap_rank=None, price_usd=None, rank_mcap_chain=None,
                                       holders=None, bs_price=None, bs_rank_holders=None, bs_name=None, bs_symbol=None)
        r["source"] = "geckoterminal_dex_volume"; sel.append(r); chosen.add(al); n_gt += 1
    for r in sel:
        g = gt.get(r["address"].lower())
        r["dex_volume_24h_top_pools"] = round(g["vol"], 2) if g else None
        r["dex_max_pool_reserve_usd"] = round(g["res"], 2) if g else None
    # holders extras
    n_extra = 0
    if bs:
        for al, b in sorted(bs.items(), key=lambda kv: kv[1]["bs_rank_holders"]):
            if al in chosen or n_extra >= M: continue
            if al in NATIVE: continue
            nm = f"{b.get('name') or ''} {b.get('symbol') or ''}"
            corroborated = bool(b.get("exchange_rate")) or al in cg_tl or al in uni_tl or al in cg_by_addr
            spammy = bool(SPAM.search(nm)) or bool(LATIN_MIX.search(nm))
            if spammy or not corroborated:
                excl.append(dict(cg_id="", symbol=b.get("symbol"), name=b.get("name"), address_raw=b["address_hash"],
                                 holders=b.get("holders_count"), bs_rank_holders=b["bs_rank_holders"],
                                 reason="holders_list_spam_pattern" if spammy else "holders_list_uncorroborated(no price, not on any token list)",
                                 source=f"blockscout-{bs_host}"))
                continue
            if al in cg_by_addr:
                r = cg_by_addr[al]
            else:
                r = dict(cg_id="", symbol=b.get("symbol"), name=b.get("name"), address=to_checksum_address(al), cg_ids_alt="",
                         market_cap_usd=None, volume_24h=None, cg_market_cap_rank=None, price_usd=None, rank_mcap_chain=None,
                         holders=int(b["holders_count"]), bs_price=b.get("exchange_rate"), bs_rank_holders=b["bs_rank_holders"],
                         bs_name=b.get("name"), bs_symbol=b.get("symbol"))
            r["source"] = "blockscout_holders"; sel.append(r); chosen.add(al); n_extra += 1
    for r in sel:
        al = r["address"].lower()
        r["in_cg_tokenlist"] = al in cg_tl; r["in_uniswap_list"] = al in uni_tl
        r["categories"] = ";".join(sorted(cats.get(r["cg_id"], [])))
        nm = f"{r.get('name') or ''} {r.get('symbol') or ''}"
        flags = []
        if SPAM.search(nm): flags.append("spam_pattern_name")
        if LATIN_MIX.search(nm): flags.append("homoglyph_script_in_name")
        r["flags"] = ";".join(flags)
    for i, r in enumerate(sel, 1):
        r["rank"] = i; r["chain"] = chain; r["chain_id"] = cfg["chain_id"]
    cols = ["chain", "chain_id", "rank", "address", "cg_id", "symbol", "name", "market_cap_usd", "volume_24h", "price_usd",
            "cg_market_cap_rank", "rank_mcap_chain", "holders", "bs_rank_holders", "source", "cg_ids_alt", "in_cg_tokenlist",
            "in_uniswap_list", "dex_volume_24h_top_pools", "dex_max_pool_reserve_usd", "categories", "flags"]
    UNI.mkdir(parents=True, exist_ok=True)
    with open(UNI / f"{chain}.candidates.csv", "w", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore"); w.writeheader(); w.writerows(sel)
    ecols = ["chain", "cg_id", "symbol", "name", "address_raw", "reason", "source", "market_cap_usd", "holders", "bs_rank_holders"]
    with open(UNI / f"{chain}.exclusions_sources.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ecols, extrasaction="ignore"); w.writeheader()
        for e in excl: e["chain"] = chain; w.writerow(e)
    from collections import Counter
    print(chain, "selected", len(sel), dict(Counter(r["source"] for r in sel)), "cg-listed-valid", len(rows),
          "with mcap", len(withcap), "exclusions", len(excl), "uniswap-list on chain", len(uni_tl),
          "uniswap-not-in-universe", len(uni_tl - chosen))


if __name__ == "__main__":
    for ch in (sys.argv[1:] or CHAINS):
        main(ch)
