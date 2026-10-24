"""Fetch Blockscout /api/v2/tokens?type=ERC-20 sorted by holders_count desc (paged, 50/page), raw pages cached.
Only instances reachable without a Cloudflare browser challenge are used (we do not try to evade it):
eth.blockscout.com and optimism.blockscout.com. base/arbitrum/polygon (Cloudflare 403) and bsc (no instance) are skipped.
Usage: fetch_blockscout.py <instance-prefix> <max_pages>"""
import sys, urllib.parse
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from common import *

T = Throttle(1.5)

def main(host, max_pages):
    base = f"https://{host}.blockscout.com/api/v2/tokens"
    params = {"type": "ERC-20", "sort": "holders_count", "order": "desc"}
    nxt = None
    for pg in range(1, max_pages + 1):
        q = dict(params)
        if nxt: q.update({k: ("null" if v is None else str(v).lower() if isinstance(v, bool) else v) for k, v in nxt.items()})
        url = base + "?" + urllib.parse.urlencode(q)
        d = http_get(url, f"blockscout-{host}", f"tokens_holders_p{pg:03d}", T, timeout=180)
        if d is None:
            print("failed page", pg); break
        items = d.get("items", [])
        print(host, "page", pg, len(items), "last holders", items[-1].get("holders_count") if items else None, flush=True)
        nxt = d.get("next_page_params")
        if not nxt: break

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
