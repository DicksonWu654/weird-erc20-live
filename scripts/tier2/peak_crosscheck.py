#!/usr/bin/env python3
"""Compare independent C11 V2 peak amounts with saved Tier-2 Sync reconstruction."""
import json
from decimal import Decimal

import pandas as pd

from reaggregate_revision import ROOT, OUT


def main():
    d = pd.read_parquet(OUT / "token_results.parquet")
    c = pd.read_csv(ROOT / "data/verify/rug/verdicts.csv")
    m = d.merge(c, left_on=["address", "selected_pool"], right_on=["address", "pool"])
    rows = []
    for r in m.itertuples():
        if r.selected_dex not in ("uniswap_v2", "sushiswap_v2", "sushiswap"):
            continue
        p = OUT / "rug-second-pass/exact-peaks" / f"{r.address[2:]}_{int(r.sim_block)}.json"
        if not p.exists():
            continue
        x = json.loads(p.read_text())
        if x.get("error") or x.get("truncated"):
            continue
        decimals = 6 if str(r.quote_symbol).upper() in ("USDC", "USDT") else 18
        observed = Decimal(int(x["peak_quote_30d"])) / (Decimal(10)**decimals)
        c11 = Decimal(str(r.peak_quote_liquidity))
        # C11's CSV decimal formatting can round tiny units; compare at its
        # displayed precision and retain both values for inspection.
        same = abs(observed-c11) <= max(Decimal("1e-12"), abs(c11)*Decimal("1e-12"))
        rows.append({"address": r.address, "pool": r.selected_pool, "quote": r.quote_symbol,
                     "our_peak_quote_units": str(observed), "C11_peak_quote_units": str(c11),
                     "matches_displayed_precision": bool(same), "our_evidence": str(p.relative_to(ROOT)),
                     "C11_evidence": r.evidence})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "peak_crosscheck.csv", index=False)
    print(f"peak crosscheck {int(out.matches_displayed_precision.sum())}/{len(out)}")


if __name__ == "__main__":
    main()
