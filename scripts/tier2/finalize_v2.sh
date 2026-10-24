#!/usr/bin/env bash
# Run from the repository root. No v1 outputs are rewritten.
set -euo pipefail
.venv/bin/python -u scripts/tier2/early_liquidity.py --concurrency 16 --rps-archive 3 --rps-logs .8 --retry-errors
.venv/bin/python -u scripts/tier2/legacy_v2.py --fetch-missing
.venv/bin/python -u scripts/tier2/origin_audit.py
.venv/bin/python -u scripts/tier2/liquidity_v21.py --mode early --retry-errors
.venv/bin/python -u scripts/tier2/liquidity_v21.py --mode legacy --retry-errors
.venv/bin/python -u scripts/tier2/liquidity_v21.py --mode conversions
.venv/bin/python scripts/tier2/initialize_index_v21.py
.venv/bin/python -u scripts/tier2/liquidity_v21.py --mode validate
OPENBLAS_NUM_THREADS=1 .venv/bin/python -u scripts/tier2/reestimate_v2.py --reps 1000
.venv/bin/python scripts/tier2/misclassification_v2.py
.venv/bin/python scripts/tier2/spotchecks_v2.py
.venv/bin/python scripts/tier2/report_v2.py
.venv/bin/python scripts/tier2/report_v21.py
.venv/bin/python scripts/tier2/verify_outputs.py --v2
