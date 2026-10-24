"""Fetch public token lists (raw, cached): CoinGecko tokens.coingecko.com/<platform>/all.json per chain and the Uniswap default list.
Used only as cross-check / listing evidence (unranked)."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from common import *
T = Throttle(4.0)
for chain, c in CHAINS.items():
    d = http_get(f"https://tokens.coingecko.com/{c['cg']}/all.json", "tokenlists", f"coingecko_{c['cg']}_all", T, timeout=120)
    print(chain, len(d["tokens"]) if d else None, flush=True)
d = http_get("https://tokens.uniswap.org", "tokenlists", "uniswap_default", T, timeout=120)
print("uniswap", len(d["tokens"]) if d else None)
