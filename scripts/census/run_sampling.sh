#!/bin/sh
# Block-sampling campaign (resumable; rerun to continue/extend).  ~4 blocks/s on the free drpc endpoint.
#   1. pre-2023 validation months (also covered by the complete Paradigm table)
#   2. post-2023-03 validation months (also covered by the complete Zellic table)
#   3. production months not covered by any complete dataset (>= block 21,850,000), newest first
cd "$(dirname "$0")/../.." || exit 1
PY=.venv/bin/python
N_VAL=${N_VAL:-1000}; N_PROD=${N_PROD:-1000}
for m in 2021-05 2019-06 2022-06 2018-03 2020-09; do $PY scripts/census/sample.py --months $m --n $N_VAL --workers 3 --rps 4; done
for m in 2023-05 2024-06 2025-01; do $PY scripts/census/sample.py --months $m --n $N_VAL --workers 3 --rps 4; done
$PY scripts/census/sample.py --months 2025-02:2026-09 --n $N_PROD --reverse --workers 3 --rps ${RPS_PROD:-3}
