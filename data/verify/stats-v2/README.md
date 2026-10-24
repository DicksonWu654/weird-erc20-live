# Independent statistics audit: aggregate outputs

`recompute.csv`, `mismatches.csv` and `supplemental_checks.csv` compare saved points, counts, support and bounds with independent arithmetic. `ci_check.csv` and `ci_differences.csv` distinguish point discrepancies from finite-seed interval endpoint differences. `coverage_summary.csv` contains synthetic population exercises, not empirical Ethereum coverage. `v2_only.csv` includes Uniswap V2 and Sushi V2, excluding V1; it differs from the literal Uniswap-V2-only paper domain. `summary.json` retains completed counts and limitations, including the bursty synthetic undercoverage failure.

Row-level rederivations/selection lists, raw RPC bodies, local caches and population arrays are omitted. These audit aggregates describe the supplied private evidence audit; public ledger checks independently repeat only the arithmetic supported by shipped tables/moments.
