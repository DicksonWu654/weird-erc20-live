#!/usr/bin/env python3
"""Build the round-2 14-day Ethereum V2/V3 sample without touching W6 outputs.

The W6 builder is reused with its output and raw-evidence roots redirected to
``data/validate/round2``.  This keeps the old 60-day frame reproducible while
retaining the same liquidity strata and contract-age filter.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "validate" / "round2"
OUT.mkdir(parents=True, exist_ok=True)

import build_new_eth as b  # noqa: E402

b.VAL = OUT
b.RAW = OUT / "raw"
b.RAW.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    # A 14-day window and the W6 stratified quotas: 60 liquid, 50 thin,
    # with the remaining sample drawn from dust.
    sys.argv = [sys.argv[0], "--days", "14", "--n", "300", "--n-liquid", "60", "--n-thin", "50", "--seed", "20261021"]
    b.main()
