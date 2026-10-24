#!/bin/sh
# Regenerate every W4 artifact from scratch (resumable; each step skips work already cached).  Wall time on the free endpoints:
#   ~30 min bulk downloads/reductions + ~1 min DEX scans + sampling campaign (4 blocks/s) + pool-frame traces (1-2 req/s).
set -e
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
S=scripts/census
$PY $S/blockmap.py                         # month -> block ranges (secant search on eth_getBlockByNumber)
$PY $S/dex_scan.py                         # complete V1/V2/Sushi/V3/V4 pool-creation scans (Tenderly public gateway)
$PY $S/dex_build.py && $PY $S/dex_crosscheck.py     # data/census/dex_pools.parquet + completeness checks
$PY $S/paradigm_ingest.py                  # stream Paradigm ethereum_contracts (CC0) -> slim tables (needs ~1 GB scratch)
$PY $S/paradigm_monthly.py
$PY $S/zellic_ingest.py stage1 && $PY $S/zellic_ingest.py stage2   # stream Zellic all-ethereum-contracts (no raw storage)
$PY $S/zellic_monthly.py
sh $S/run_sampling.sh                      # live trace_block block sampling (validation months + production months)
$PY $S/pool_frame.py --months 2025-02:2026-09 --ncand 500 --reverse
$PY $S/build_sampled_tokens.py
$PY $S/assemble_pool_frame.py
$PY $S/monthly_counts.py                   # -> data/census/monthly_counts.csv
$PY $S/frame.py                            # -> data/census/sample_frame.parquet
# validation
$PY $S/validate_estimator.py && $PY $S/validate_recall.py && $PY $S/validate_sampling.py && $PY $S/validate_zellic_vs_paradigm.py
$PY $S/validate_opcode_inference.py && $PY $S/validate_dynamic.py && $PY $S/diag_residual.py && $PY $S/leads.py
