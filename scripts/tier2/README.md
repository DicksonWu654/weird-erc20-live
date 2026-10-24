# Tier-2 pipelines

The current launch-state series is **v2.1 / classifier rules 3.3** in `data/tier2/v2/`.
Its [data dictionary and reproduction commands](../../data/tier2/v2/README.md) describe
real V4 position liquidity, first-funding/removal descriptions, uncertainty and blind checks.
`finalize_v2.sh` rebuilds this series; `verify_outputs.py --v2` verifies it.
Only `data/tier2/v2/V21_READY` establishes final v2.1 readiness. Original simulation
checkpoints stay immutable; `liquidity_v21.py` supplies measurement overlays.

## Legacy C1b pipeline

Run from the repository root with `.venv/bin/python`. All collectors are resumable and write only under `data/tier2/`.

1. `build_outputs.py build-pairs` builds the selected-pool manifest from W4's frame. Its older `assemble`, `monthly`, `timeline`, and `spotchecks` commands are superseded by `aggregate.py` and must not be used for final numbers.
2. `scripts/sim/reclassify.py data/tier2/sim-run/results.jsonl` reapplies C2's fixed classifier and 1e9-wei native-quote floor offline. This has already been done, with the prior JSONL retained as `.bak`.
3. `v1_reserves.py` records sim-block ETH and token reserves for V1 buy failures. `quote_prices.py` records WETH/quote reserve conversions, preserving retries as separate files. `rug_second_pass.py` records seeded monthly second-pass Etherscan samples.
4. `aggregate.py` reads the completed evidence and creates `token_results.parquet`, `monthly_shares.csv`, `annual_shares.csv`, `rug_rates_by_frame_month.csv`, `dust_sensitivity.csv`, `precision_vs_blind*.csv`, `spotchecks.csv`, `timeline_join.csv`, `unprobeable_by_era.csv`, and `v1-audit-summary.csv`.
5. `scripts/sim/run.py --pairs data/tier2/pairs-sensitive.csv --mode historical --rug off --no-etherscan --out data/tier2/sim-sensitive --raw-dir data/tier2/sim-sensitive/raw` runs the +300-block subset. `offset_compare.py` compares it with the main +7,200-block pass.

`data/tier2/v2/README.md` documents the current denominators, classification, caveats and results; the pipeline above describes the legacy two-phase rug estimator. Source evidence remains in the simulator JSONL's `evidence` field, the saved gzip RPC files, and the second-pass request/response files.
