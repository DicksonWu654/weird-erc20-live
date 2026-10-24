"""GeckoTerminal public API: top DEX pools by 24h volume per network (10 pages x 20), with base/quote token objects.
Raw pages cached under data/raw/geckoterminal/. Used only as a cross-check source for liquid tokens missing from the CG ranking."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from common import *
T = Throttle(5.0)
NET = {"ethereum": "eth", "base": "base", "arbitrum": "arbitrum", "bsc": "bsc", "polygon": "polygon_pos", "optimism": "optimism"}
for chain, net in NET.items():
    for pg in range(1, 11):
        d = http_get(f"https://api.geckoterminal.com/api/v2/networks/{net}/pools?page={pg}&sort=h24_volume_usd_desc&include=base_token,quote_token",
                     "geckoterminal", f"{chain}_pools_vol_p{pg:02d}", T)
        if d is None: print("fail", chain, pg); break
        print(chain, pg, len(d["data"]), flush=True)
