"""CoinGecko's global /coins/markets listing omits coins that have no market_cap_rank (wrapped / bridged / staked derivatives
such as stETH, wstETH, WETH, WBTC, bridged USDC.e...), although /coins/markets?ids=... returns a real market cap for them.
This script finds every CG id that has a contract on one of our six chains but is absent from the saved global ranking pages,
and fetches them by id (200 ids/call). Raw pages: data/raw/coingecko/markets_ids_pNNN.json."""
import json, glob, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from common import *
T = Throttle(9.0)
lst = json.load(open(RAW / "coingecko" / "coins_list_include_platform.json"))
have = set()
for p in glob.glob(str(RAW / "coingecko" / "markets_p*.json")):
    have |= {c["id"] for c in json.load(open(p))}
plats = {v["cg"] for v in CHAINS.values()}
need = sorted(c["id"] for c in lst if c["id"] not in have and any((c.get("platforms") or {}).get(p) for p in plats))
print("ids needing market data:", len(need), flush=True)
B = 200
for i in range(0, len(need), B):
    ids = ",".join(need[i:i + B])
    # key by hash of the id set so a changed list never reuses a stale page
    key = f"markets_ids_p{i // B:03d}_{hashlib.sha256(ids.encode()).hexdigest()[:8]}"
    d = http_get(f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}&order=market_cap_desc&per_page=250&page=1&sparkline=false",
                 "coingecko", key, T, timeout=120)
    print(i // B, None if d is None else len(d), flush=True)
