"""CoinGecko category membership via /coins/markets?category=<id> (250/page, cached raw). A handful of categories only,
to give cheap category hints (stablecoin / wrapped+bridged / LST+LRT / meme / governance / RWA) without burning the rate limit."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from common import *
T = Throttle(9.0)
CATS = ["stablecoins", "wrapped-tokens", "bridged-tokens", "liquid-staking-tokens", "liquid-restaking-tokens", "liquid-staked-eth",
        "governance", "real-world-assets-rwa", "meme-token"]
MAXP = 16
for cat in CATS:
    for pg in range(1, MAXP + 1):
        d = http_get(f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category={cat}&order=market_cap_desc&per_page=250&page={pg}&sparkline=false",
                     "coingecko", f"cat_{cat}_p{pg:02d}", T)
        if d is None: print("fail", cat, pg); break
        print(cat, pg, len(d), flush=True)
        if len(d) < 250: break
