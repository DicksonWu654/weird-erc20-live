"""Fetch CoinGecko raw pages: /coins/list?include_platform=true and /coins/markets pages (market-cap order).
Raw responses cached under data/raw/coingecko/. Re-run is idempotent (cached pages reused).
Usage: fetch_coingecko.py [max_pages]"""
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from common import *

T = Throttle(9.0)  # ~6.5 calls/min, under the free-tier public limit
SRC = "coingecko"

def main(max_pages=60):
    lst = http_get("https://api.coingecko.com/api/v3/coins/list?include_platform=true", SRC, "coins_list_include_platform", T)
    print("coins/list:", len(lst) if lst else None, flush=True)
    http_get("https://api.coingecko.com/api/v3/asset_platforms", SRC, "asset_platforms", T)
    for pg in range(1, max_pages + 1):
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page={pg}&sparkline=false"
        d = http_get(url, SRC, f"markets_p{pg:03d}", T)
        if d is None:
            print("failed page", pg, flush=True); break
        n_cap = sum(1 for c in d if c.get("market_cap"))
        print(f"page {pg}: {len(d)} coins, {n_cap} with market cap", flush=True)
        n_vol = sum(1 for c in d if (c.get('total_volume') or 0) > 0)
        n_vol = sum(1 for c in d if (c.get("total_volume") or 0) > 0)
        if len(d) < 250 or (n_cap == 0 and n_vol == 0):  # past the coins with market cap AND past those with any volume
            break

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
