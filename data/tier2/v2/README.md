# Launch-state trade measurement: public aggregates

The observation state is the first positive liquidity activation within 180 calendar days of token creation, plus 300 blocks (approximately one hour). Pool candidates are checked in creation order; ties use log index and pool ID. The initial buy policy is 0.01 ETH, retaining smaller and integer-lot retries and the sell ladder. A successful probe can therefore use less than the initial amount.

Measurable means observed ETH-equivalent quote liquidity of at least 0.01 ETH, including above-floor buy failures and unknown trade outcomes. V1 uses native exchange balance; V2/Sushi use pre-buy quote reserves; V3 uses quote-token pool balance, including out-of-range funds and uncollected fees; V4 uses real quote-token principal reconstructed from all position-liquidity events at the simulation state and valued at its square-root price. The active virtual-liquidity proxy is a separate sensitivity. These proxies do not imply equal executable depth. Historical quote conversion failures remain unknown.

Headline harm is honeypot OR tax >=10%. Owner capability and liquidity withdrawal are separate. Below-floor pools take precedence over trade labels and are not attributed rugs. Categories, failures and structural NA cells remain distinct.

The all-new denominator is the original census frame. The measurable denominator includes only above-floor pooled observations. Analysis month is creation month. Overlapping first-pool-frame observations before February 2025 are excluded. February 2025 all-new estimates are undefined; annual 2025 and its first quarter omit February. 2026 covers January–September. Late first-pool and proxy frame coverage gaps remain.

Public files:

- `annual_shares.csv`, `quarterly_shares.csv`, `monthly_shares.csv`: period x denominator x label; fractions, approximate intervals, counts, frame weights, Kish support, identification/coverage and low-support flags.
- `venue_by_year.csv`, `method_by_year.csv`, `standardized.csv`: venue, creation-method and fixed-2024 venue-mix estimates. V1/V2 includes Sushi; literal Uniswap V2 points are independently recovered from `cohort_weights.csv`.
- `sensitivity.csv`, `dust_sensitivity.csv`, `bounds.csv`, `misclass_adjusted.csv`: floors, thresholds, fee/legacy-design sensitivities, unknown-outcome bounds and explicitly conditional classification-error envelopes. The legacy +7200 design changes pool selection too.
- `blind_metrics.csv`: era x venue sample-conditional confusion metrics; zero trials are NA. Selected legacy blind states do not identify population recall or transport to launch state.
- `offset_transition_by_year.csv`, `headline_old_vs_new.csv`: descriptive matched transitions and explicitly different historical estimands.
- `bootstrap_check.csv`, `bootstrap_check_replicates.csv`: seeded synthetic coverage, not empirical Ethereum coverage.
- `cohort_weights.csv`: grouped creation month/frame/category/venue/domain predicates, `n`, `weight=sum(original expansion weights)`, `weight_sq=sum(original squared weights)`. No token, pool, sample identifier or creation block is retained.
- `below_origin_stats.csv`: annual below-floor V2/Sushi sample counts, weight moments, first-reserve threshold masses and weighted medians, with no identities or first-reserve token rows.
- `blind_pair_counts.csv`: matched, deduplicated known-outcome predicted/truth harm counts from the private legacy blind checks.
- `launch_liquidity_by_year_venue.csv`: first-state coverage/funding, loss by +300 and combined descriptive shares; withdrawal/dumping directions remain descriptive, without actor attribution.
- `blind_coverage_by_year.csv`, `pooled_assumption_ranges.csv`, `pooled_blind_metrics.csv`: domain coverage and explicitly conditional classification-transport scenarios.
- `unresolved_dilution_by_year_venue.csv`, `residual_liquidity_harm.csv`: unresolved mass and residual-liquidity sensitivities.
- `origin_counts.csv`: diagnostic wrapper-revert origin counts; no purposively selected token rows.
- `verification.json`: completed upstream verification counts, not redistributed raw evidence.

Intervals use stratified ratio-score/FPC or full-frame cluster-bootstrap/FPC methods, with sparse/boundary Kish fallbacks; census-count uncertainty is propagated where marked. Empirical intervals are supplied rather than resampled from these grouped tables. Clustered burst undercoverage and classification/coverage error remain limitations. Conditional error bounds depend on unverified transport; strict bounds remain separately reported.

Per-token Parquet/JSONL, blind comparisons, origin samples/attributions, spotchecks and raw traces inherit restricted census provenance and are omitted. Grouped projections replace only the sufficient arithmetic inputs; their upstream file hashes are retained in `data/release-projections.json`. See the root README for offline and full remeasurement tiers.
