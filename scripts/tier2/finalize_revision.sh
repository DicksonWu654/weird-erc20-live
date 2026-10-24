#!/usr/bin/env bash
# Resumable final evidence enrichment and table build. Run from repository root.
set -euo pipefail
cd "$(dirname "$0")/../.."
export OPENBLAS_NUM_THREADS=1
if [[ -n "${TIER2_WAIT_PID:-}" ]]; then
  while kill -0 "$TIER2_WAIT_PID" 2>/dev/null; do
    sleep 20
  done
fi
echo "$(date -u +%FT%TZ) stage: eligible extension to 40"
TIER2_EXTEND_ELIGIBLE=1 .venv/bin/python scripts/tier2/rug_second_pass.py
echo "$(date -u +%FT%TZ) stage: Sync histories"
.venv/bin/python scripts/tier2/enrich_peak.py
echo "$(date -u +%FT%TZ) stage: creation block headers"
.venv/bin/python scripts/tier2/fetch_creation_blocks.py
echo "$(date -u +%FT%TZ) stage: exact peaks"
.venv/bin/python scripts/tier2/recompute_peaks.py
echo "$(date -u +%FT%TZ) stage: final aggregation"
.venv/bin/python scripts/tier2/reaggregate_revision.py
echo "$(date -u +%FT%TZ) stage: C11 peak comparison"
.venv/bin/python scripts/tier2/peak_crosscheck.py
echo "$(date -u +%FT%TZ) stage: output audit"
.venv/bin/python scripts/tier2/verify_outputs.py
echo "$(date -u +%FT%TZ) stage: synthetic coverage"
.venv/bin/python scripts/tier2/coverage_check.py
echo "$(date -u +%FT%TZ) finalization complete"
