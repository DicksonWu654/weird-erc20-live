---
pagetitle: "Weird ERC-20 Live — behavior and Ethereum launch-state outcomes"
lang: en
bibliography: refs.bib
csl: numeric.csl
link-citations: true
---

# Weird ERC-20 Live: token behavior across six EVM chains and launch-state trading outcomes on Ethereum, 2017–2026

## Abstract

We pair a snapshot of {{n_main}} ranked token deployments across {{n_chains}} EVM chains with an Ethereum launch study that tests a buyer's sale and tax outcomes about an hour (300 blocks) after first liquidity, requiring at least 0.01 ETH-equivalent real quote liquidity. Observed sale failure or token tax of 10% or more fell from **{{v2_2024_measurable_share}}** in 2024 (approximate 95% sampling interval {{v2_2024_measurable_ci_low}}–{{v2_2024_measurable_ci_high}}) to **{{v2_2026_measurable_share}}** in January–September 2026 ({{v2_2026_measurable_ci_low}}–{{v2_2026_measurable_ci_high}}), while the share of pooled tokens already below that floor rose from **{{v2_2024_no_meaningful_liquidity_pooled}} to {{v2_2026_no_meaningful_liquidity_pooled}}**; most below-floor V2 pools had initially been funded, so these results do not identify a fall in overall harm. The 2024–2025 fall also appears within Uniswap V2 ({{v2_2024_uniswap_v2_only}} to {{v2_2025_uniswap_v2_only}}), whereas the 2025–2026 aggregate depends on V4 liquidity and venue composition, and 2026 V2 support is just below the reporting threshold. {{blind300_abstract}}

::: {.key-results}
**Key results**

- **Sale/tax harm:** {{v2_2024_measurable_share}} in 2024 versus {{v2_2026_measurable_share}} in January–September 2026; estimated affected tokens are {{v2_2024_harm_est_count}} and {{v2_2026_harm_est_count}}, respectively (count intervals in Table 9b).
- **Below-floor pools increase:** below-floor pooled weight is {{v2_2024_no_meaningful_liquidity_pooled}}, {{v2_2025_no_meaningful_liquidity_pooled}}, and {{v2_2026_no_meaningful_liquidity_pooled}} in 2024–2026. First-liquidity and reserve-direction evidence distinguish depletion from pools that were never meaningfully funded; neither establishes insider attribution.
- **Venue matters:** the 2024–2025 fall holds within V2; the 2026 V2 rate is withheld because effective positive support is below 30. Real V4 position principal determines eligibility; the old virtual amount is a sensitivity. Fixed-2024-mix estimates have insufficient constituent support and are withheld.
- **A launch label expires:** the earlier matched-domain within-day harm sensitivity is {{delayed_2024_all_within_day_share}}, {{delayed_2025_all_within_day_share}}, and {{delayed_2026_all_within_day_share}} in 2024–2026; {{delayed_reviewed_eip7702_n}} of {{delayed_reviewed_cases_n}} purposively reviewed delayed cases involve EIP-7702 fee-recipient delegations.
- **Validation has a scope:** independent +300 replays test {{blind300_sample_n}} selected token states, with no-liquidity, failed buys and unresolved routes reported separately. Provider agreement tests current-state Ethereum V2/V3; it is not historical accuracy.
:::

## Introduction

An ERC-20 interface does not guarantee interchangeable behavior. In June **2020**, STA's transfer fee interacted with Balancer's balance accounting; in April **2020**, imBTC's ERC-777 callbacks were involved in the Uniswap and Lendf.me incidents; in January **2022**, a permissive WETH fallback enabled a phantom-permit integration failure in Multichain [@balancer_analysis; @imbtc_incident; @dedaub_phantom_permit]. These incidents motivate checking state transitions, return values, and privileges instead of relying on a token's name or nominal interface.

The same caution applies to measurement. A successful EVM call can buy zero tokens. A pool can retain enough wei for an integer-valued swap after essentially all provider liquidity has left. A no-code account can hold a privileged role without revealing how its key is secured. A measured inability to trade can come from the token, the pool, the route, or the test's amount. We retain these distinctions and save the request and response behind each observation.

This study makes three contributions:

- An evidence-linked snapshot of integration behavior and control paths across Ethereum, Base, Arbitrum, BNB Smart Chain, Polygon, and Optimism, with chain × rank-band reporting and a securities-composition sensitivity.
- A probability-sample-based Ethereum launch-state trade series, with explicit liquidity, buy-failure, venue, creation-method, threshold, timing, and classification-error sensitivities.
- Independent router replays, liquidity reconstructions, provider comparisons, and arithmetic checks that expose both useful measurements and important failure modes, including a protocol-fee change that invalidates a tiny-liquidity eligibility rule.

The multi-chain snapshot and Ethereum history answer different questions. The snapshot describes selected deployments at their recorded states. The history describes supported Ethereum-mainnet pools associated with sampled new non-LP tokens. It does not measure Base launchpads or infer their prevalence from Ethereum activity.

## Related work

The d-xo collection and Trail of Bits checklist organize non-standard token behavior and integration hazards; they are taxonomies and per-contract guidance rather than probability samples [@dxo_weird_erc20; @trailofbits_checklist]. TokenScope detects inconsistencies by comparing interface actions, state changes, and events in transaction execution. DEPOSafe studies fake-deposit behavior, including failures that an integration can overlook. Fröwis, Fuchs, and Böhme compare bytecode approaches to identifying token systems, a prerequisite for defining a deployment population [@tokenscope2019; @deposafe; @token_systems]. Our active probes complement transaction-driven observations but introduce their own funding and caller limitations.

HoneyBadger studies bait contracts that entice users with apparent vulnerabilities; its object differs from ERC-20 tokens that block an acquired position's exit [@honeybadger2019]. Xia and colleagues characterize Uniswap scam tokens, Mazorra, Adan, and Daza study automated scam detection, and Cernera and colleagues analyze token spammers, short-lived pools, and sniper bots on Ethereum and BNB Smart Chain [@xia_trade_trick; @xia_sigmetrics; @mazorra2022; @cernera2023]. Their outcome definitions and denominators differ from our tested sale-or-tax union, so their reported rates are not direct baselines for this series.

Huynh and colleagues' TrapdoorAnalyser is the closest methodological predecessor: it combines buy/sell testing with contract-semantic indicators and compares detections with GoPlus [@huynh_trapdoor]. Our contribution is not the invention of buy/sell simulation. It is a time-indexed measurement with saved call evidence, explicit observation timing and liquidity domains, and separate treatment of missing outcomes. Different caller identities, trade sizes, tax thresholds, and pool selections prevent a direct prevalence comparison.

Upgradeable-contract measurement also predates this work. *Not so immutable* examines upgrade patterns and their controlling accounts; *Proxy Hunting* develops a taxonomy and large-scale analysis of proxy-based upgradeability [@not_immutable; @proxy_hunting]. We use these distinctions to separate proxy presence, an active upgrade path, and the weakest verified authority on that path. A legacy deprecate-and-forward mechanism can be upgradeable without using a DELEGATECALL proxy.

Pied-Piper detects token backdoor threats; Tokeer, presented in *Stop Pulling my Rug*, models transfer-related rug risks; CRPWarner analyzes contract-related rug functions; and TMRugPull emphasizes temporal alignment in early-detection datasets [@pied_piper; @stop_rug; @crpwarner; @tm_rugpull]. These works reinforce the distinction between a capability, a simulated trading outcome, and an attributed liquidity removal. We report those as separate quantities. Neither a dangerous-looking function nor a price decline by itself proves that an issuer committed a rug pull.

## Methods

### Snapshot population, calls, and control paths

The unit is a **deployment on a chain**, not an independent economic asset or issuer. Market-cap rank is the global asset capitalization among assets deployed on that chain, not the value circulating locally. The inventory has {{n_universe}} deployments; the main prevalence domain has {{n_main}} ranked rows. The {{n_extras}} additions from corroborating token lists and pool/holder discovery are a separate convenience sample. The deepest rank band exists only on Ethereum; Optimism has less coverage than the other chains.

Table 1. Snapshot coverage. Empty deep bands on other chains must not be interpreted as zero token prevalence.

{{universe_table}}

Read-only calls test return data, transfer and approval transitions, fresh-caller restrictions, metadata, callbacks, fee behavior, and balance/share accounting. Where needed, a derived holder is funded by a calibrated balance-slot override; tokens that require genuine holder history use a real-holder simulation. Each test is isolated and discarded. Every saved observation records chain, block, caller, parameters, raw request/response, retrieval time, and response hash. Rolling block pins on short-retention nodes mean the snapshot is not simultaneous across all deployments. Failed retrievals, inaccessible funding, and exhausted state windows remain unknown with a reason.

The control analysis follows recognized proxy slots and custom forwarding paths, then traverses owners, administrators, roles, multisigs, and timelocks. A claimed upgrade authority requires a successful read-only call from that authority and a reverting stranger control where the path is testable. The displayed class is the **weakest firm path found**, including any enforced delay; it is not merely the first address encountered. Unverified wrappers stay unresolved. An externally owned account (EOA) is an on-chain account classification: it says nothing about the number of people, MPC shares, HSMs, custody, or operational policy behind a key.

Rates use feature-specific measured denominators. A union is positive if a constituent is positive, but negative only when all required constituents are negative. Wilson intervals in the ranked tables are **descriptive 95% intervals for this snapshot**, not design-based intervals for all deployed tokens, and exclude classification error. Securities-excluded rates use a saved name/category rule supplemented by recognized Base stock-policy membership; this is an imperfect composition sensitivity, not an exhaustive legal classification.

### Ethereum creation cohorts and sampling

The deployment census spans {{census_months}} months from January 2017 through September 2026. The sampling frame contains {{sample_frame_n}} rows. The fixed bytecode classifier requires totalSupply/balanceOf/transfer dispatch and the Transfer event topic, resolving EIP-1167 clones. Runtime-bytecode and clone classification separate non-LP tokens from recognizable LP/pool-share tokens. A new LP token is not the same quantity as a new token that acquired a DEX pool. The largest monthly estimate of new non-LP tokens is **{{census_peak}}** (month: estimate [reported interval]). Earlier complete creation tables come from the Paradigm Data Portal and Zellic's all-ethereum-contracts collection; the later period combines pool-linked sampling with random block sampling of other creations, retaining original month/source/stratum expansion weights. Here “non-LP” means not recognized as LP/pool-share by the fixed classifier; unrecognized receipt and LP implementations can remain. Successful-create ancestry checks exclude reverted creation attempts in the RPC segment.

The census estimator and the outcome estimator have separate validation. The census's complete-table Monte Carlo exercise at 1,000 sampled blocks gives {{census_mc_coverage}} nominal-95% coverage for the all-contract decomposition estimator and {{census_nonlp_mc_coverage}} for the non-LP simple-random-sampling estimator. A separate live-RPC comparison covers {{census_live_coverage}} month-series intervals, with mean **relative** error {{census_live_relative_error}}. Both live misses occur in May 2023, a bursty launch month. This is not a mean error in percentage points. Non-clone proxies that hide the ERC-20 implementation remain a known census recall gap; the saved proxy-candidate adjustment is not silently added to the population.

Unknown creation methods remain a separate stratum, rather than being inferred to be factory/launcher deployments. Creation month defines the cohort. The overlapping late-frame row before February 2025 is removed. The full-month all-new estimate for February 2025 is undefined because part of the month has zero no-pool inclusion probability. Annual 2025 all-new results therefore exclude February; 2025 quarterly all-new results with incomplete required coverage are not interpreted. The late pool-linked frame also misses some tokens whose first pool arrives more than 216,000 blocks after creation. Reweighting the observed sample does not repair that missing stratum.

### Launch-state selection and trade definition

We inspect supported pools chronologically and select the earliest positive-liquidity activation within **180 days of token creation**, with deterministic log-index/pool tie breaking. Pool creation or initialization alone is insufficient. The parent state is the first positive liquidity block **+300 blocks**; execution occurs in subsequent synthetic blocks. This is about an hour (300 blocks) after liquidity, not necessarily after deployment; pre-Merge cohorts take about 65–70 minutes. Later-only pools, no detected activation, unavailable routes, and unresolved selection states remain distinct outcomes. The observation head is frozen; an offset is not silently shortened to fit it. Recent cohorts have not accrued a full 180-day follow-up by that head. No-pool and unresolved activation windows describe observation through the saved head, not lifetime absence of a pool.

The initial purchase request is **0.01 ETH**. Existing small-size and integer-lot retries and the sell ladder remain part of the policy, so the saved successful amount can be smaller or otherwise adjusted. The test does not guarantee behavior for a larger buyer or a different caller. Token/hook tax is measured from user and core-swap balance changes and includes independently measured transfer tax. Core pool fees are excluded from the primary token-tax definition; high-fee V4 pools receive separate cost flags and an explicit fee-inclusive sensitivity.

Table 2. Domains and outcomes. The liquidity test precedes the trade label; unknown liquidity never qualifies a pool for the above-floor domain.

| Quantity | Definition and interpretation |
| --- | --- |
| Primary trade domain | Pooled tokens with observed ETH-equivalent quote liquidity ≥0.01 ETH at the test state, including above-floor buy failures and unresolved trade outcomes. |
| Headline numerator | A positive-output purchase followed by failure of the supported positive-quote exit attempts, excluding identified infrastructure failures, **or** observed token tax ≥10%. The union is counted once. |
| Could not buy | The buy reverts or is blocked. Reported separately; it is not evidence that acquired tokens cannot be sold. |
| No meaningful liquidity | The observed venue-specific quote measure is below 0.01 ETH. Outside the primary trade domain, even if a tiny trade executes. This is not an attributed rug label. |
| All-new descriptive rate | The same observed trade numerator divided by all sampled new non-LP token weight. It is diluted by no-pool and unknown outcomes and is not total scam prevalence. |
| Liquidity pulled / insider-linked removal | Separate historical liquidity-reconstruction outcomes. They do not enter the trade numerator; attribution and selection uncertainty prevent a combined scam rate. |

V1 uses exchange native balance; V2 and Sushi use pre-buy quote reserves; V3 uses the pool's quote-token balance, including out-of-range funds and uncollected fees. Non-native quotes require a saved same-block ETH conversion. The V4 singleton balance is never allocated across pools. We reconstruct **real quote-token principal across all positions** from every ModifyLiquidity event through the simulation block, accumulating signed liquidity changes by sender, tick bounds, and salt. With $Q=2^{96}$, position liquidity $L$, boundary square roots $a,b$, and simulation price clipped to $p\in[a,b]$, principal amounts are $\lfloor LQ(b-p)/(bp)\rfloor$ and $\lfloor L(p-a)/Q\rfloor$, summed per position; removed positions contribute zero and out-of-range positions retain their appropriate side. StateView getters independently check position liquidity, active liquidity and price. Uncollected fees and donations are excluded. The old active virtual amounts, $LQ/\sqrt{P}$ and $L\sqrt{P}/Q$, can greatly exceed deposited ETH and are used **only as a sensitivity**. Principal, V3 balances and V2 reserves still differ in fee treatment and do not guarantee equal executable depth.

A honeypot label requires positive acquired tokens and an attempted sale with a non-zero quote. Zero-output buys, identified gas limits, router failures, and pool-math limitations cannot establish token-side exit blocking. Generic wrapper reverts do not establish their underlying origin without a trace; the diagnostic sample and classification-error sensitivity retain this limitation. V3 fee-on-transfer settlement incompatibility is recorded separately unless an independently observed tax already meets the high-tax definition. An observed high tax remains positive even if exit evidence is unresolved. The threshold is **≥10%**, with boundary flags and strict >10% and ≥5% sensitivities. Static owner-control capabilities are overlapping flags, not evidence of realized trade harm.

### Estimation and uncertainty

Within a period and domain, the estimator is $\sum_i w_i H_i/\sum_i w_i D_i$, using original frame expansion weights and domain indicators. Annual ratios aggregate weighted token rows, not averages of monthly percentages. We retain both Kish denominator support and effective positive observations. The conservative support flag uses their minimum; a small effective positive count is not a small denominator sample size. Every table and chart withholds rate estimates and intervals when effective support is below 30; counts and the support flag remain visible. For trade outcomes, effective support is the minimum of positive and denominator Kish sizes, with the threshold applied before rounding. Quarters additionally require full frame coverage. Fixed-mix support is the minimum constituent venue support, so its rates and saved intervals are withheld when that minimum is below 30.

Earlier complete-frame intervals use stratified ratio-score calculations with finite-population correction and a boundary fallback. Late-frame intervals use a stratified cluster bootstrap: original creation blocks, including sampled empty blocks, are primary sampling units within month/source/stratum; other candidate strata are resampled as units. Finite-population rescaling is retained. All-new count intervals additionally propagate marginal census-count uncertainty with split-normal draws and a conservative envelope; pooled-domain counts use original expansion weights and sampled-outcome uncertainty. Unknown census/outcome covariance remains a limitation. We round empirical percentages to one decimal throughout, retain p-values and agreement coefficients at useful precision, and round expanded token counts to whole tokens with thousands separators. Raw fractions remain in the ledger for reproducibility.

All sampling intervals are approximate, lower-confidence for the late frame, and **exclude classification error and frame undercoverage**. Resampling cannot recover a rare launcher burst absent from the sampled blocks. We report an observed block-count-tail inflation sensitivity and separate buy-failure/all-unknown identification bounds. Blind-check coverage states the share of each year's domain weight whose era × venue × current predicted-category cell contains any replay, a jointly decidable replay, or a same-year replay; it is cell coverage, not the proportion of tokens individually verified. The **pooled-assumption scenario** multiplies observed harm by pooled precision's Wilson lower limit for its lower endpoint, and adds normal/owner-controlled weight times one minus pooled NPV's lower limit for its upper endpoint. This assumes pooled error homogeneity across years, venues, and predicted categories, including owner-controlled cases excluded from earlier blind draws; legacy states additionally require transport from adaptive +7200 probes to +300. Unresolved outcomes have a separate upper bound. The scenario omits sampling uncertainty and is not a confidence interval. Without those transport assumptions the strict classification range is 0–100%; detailed envelopes remain in Appendix D. A Rogan–Gladen point correction would require population sensitivity and specificity that the earlier selected samples do not identify.

## Snapshot results

### Integration behavior and issuer composition

Among ranked deployments, {{main_proxy_n_yes}}/{{main_proxy_n_measurable}} have proxy evidence, {{main_upgradeable_n_yes}}/{{main_upgradeable_n_measurable}} have an upgrade authority, and {{main_upgrade_eoa_n_yes}}/{{main_upgrade_eoa_n_measurable}} have a verified path terminating in an EOA without a detected timelock. Static blocklist machinery appears in {{main_blocklist_n_yes}}/{{main_blocklist_n_measurable}}; the broader capability-or-tested-address-rejection union is {{main_blocklist_union_n_yes}}/{{main_blocklist_union_n_measurable}}. These definitions must not be interchanged. They are observed integration properties, not allegations of abuse.

Table 3. Main ranked prevalence, mapped to the d-xo taxonomy [@dxo_weird_erc20]. Convenience extras are excluded. The blocklist union includes the Ronin exploiter recipient and the formerly sanctioned Tornado Cash router, delisted on 2025-03-21 [@treasury_delisting; @ofac_removal].

{{prevalence_table}}

Some taxonomy names describe majority protective behavior: most measured tokens reject approval or transfer to the zero address. For those rows, failure to reject is the minority behavior; the category name does not mean every positive observation is unusual or unsafe.

The corrected fee detector retains {{inventory_fot_n_yes}} inventory observations with a zero recipient pre-balance and a finite fee no greater than the entire transfer. Fresh-address phantom balances are a separate anomaly and are not counted again as fees. Share-accounting capability excludes binary-shift calibration artifacts and remains distinct from observed seven-day drift. Most securities-associated share evidence comes from tokenized-stock multipliers and decimal/share scaling, rather than observed balance drift.

The main securities sensitivity removes {{n_security_exclusions}} ranked deployments. Multi-ticket families contribute repeated implementations and policies, so deployment counts are not independent issuer counts. Table 3 shows that their inclusion materially affects blocklist and share-accounting rates. Names in the following composition table are descriptive family tags attached to deployments whose matrix checks support the counted features; they do not establish the legal issuer of every deployment.

Table 4. Ranked securities-sensitivity composition. Coinbase-listed B20 membership is an address join to a saved Coinbase public API response. Other families, including Ondo and Backed/xStocks, use saved names/categories. Feature counts refer to reproduced on-chain observations or capability checks; listing does not establish issuance.

{{security_composition}}

### Rank depth and cross-chain comparisons

The Ethereum-only tail cannot support a claim that privileges decline with rank across all chains. Table 5 reports chain × rank-band cells. Unknown archive drift on other chains remains unmeasured, not an observed absence of drift.

Table 5. Behavior and control by chain and market-cap rank band. Each cell is positive / measured (rate).

{{chain_rank_table}}

Table 6 limits cross-chain comparisons to ranks 1–500 and repeats them after the securities exclusion. This aligns rank depth, but does not remove issuer, age, or realized rank-composition confounding. The tokens are not independent draws from chain populations.

{{crosschain_table}}

The exploratory Fisher tests apply Holm correction to the saved feature × comparison family. The adjusted tests and denominators are shown rather than the former unadjusted, full-inventory contrasts.

Table 7. Rank-matched Ethereum-versus-rest tests. Holm correction covers 1,300 feature × comparison tests (16 comparisons per feature, with structural exclusions); deployment dependence limits inferential interpretation.

{{crosschain_tests}}

{{missing_return_inference}}

### Named control examples and address restrictions

Our naming rule is explicit: **name an issuer or issuer-associated deployment in a behavioral sentence only when a reproduced on-chain check supports that sentence**, and state the tested deployment and scope. The examples below document verified EOA-terminal paths; they are not a balanced sample of governance designs. Ticker equality does not establish issuance: third-party bridged or pegged assets are separated from issuer-native deployments. We do not infer intent or off-chain security from a role, selector, or account's code.

Table 8 applies this rule consistently to the checked Ethereum deployments of USDC, cbBTC, BUIDL, and PYUSD. The calls reproduce authority success and stranger rejection at the recorded state; no upgrade transaction was broadcast. An EOA terminal path does not establish individual custody, the absence of MPC/HSM controls, or a single human operator.

{{named_upgrade_table}}

For PYUSD specifically, `owner()` returns **{{pyusd_owner}}**, whereas the reproduced upgrade path uses **{{pyusd_upgrade_admin}}**. These are different EOAs. The Paxos owner-role observation must not be mistaken for the upgrade-administrator observation, and neither is a custody claim. USDT's separately reproduced `deprecate(address)` caller differential establishes a legacy forwarding upgrade path even though it is not a DELEGATECALL proxy; its saved owner path is a multisig without a detected timelock.

In the complete inventory, **{{tornado_total}}** deployments reject the tested formerly sanctioned Tornado Cash router; it was delisted by OFAC on **2025-03-21** [@treasury_delisting; @ofac_removal]. Among the **{{inventory_ronin_n_yes}}** deployments that reject the Ronin-exploiter recipient, **{{tornado_overlap}}** also reject this delisted router; a clean-control, one-raw-unit independent retest confirms that intersection. The remaining **{{tornado_only}}** is a Tornado-only matrix observation outside that independent retest. Rejection can reflect a stale or independent policy list; authorization or rejection here establishes neither current legal compliance nor motivation.

The permissioned-transfer feature has {{inventory_permissioned_n_yes}} inventory positives, distributed across all chains below. This is narrower than the {{inventory_fresh_reject_n_yes}} fresh-contract transfer rejections, which can include other gates and failures. We do not describe all fresh-contract failures as permissioning.

{{permissioned_chain_table}}

### Base protocol-token case study

Base's B20 specification describes protocol-level token execution, which ordinary bytecode/proxy-slot analysis alone does not characterize [@base_b20]. The convenience survey contains **{{b20_survey_n}} Base B20 assets**: {{b20_coinbase_survey_n}} joined to Coinbase's public tokenized-stock list and {{b20_other_survey_n}} from CoinGecko's Base list. **{{b20_measured_universe_n}}** surveyed assets are in the measured Base universe (ranked plus extras). Table 4 includes **{{b20_paper_ranked_n}} ranked Coinbase-listed B20 stocks**, or {{b20_paper_inventory_n}} including extras. Membership joins lowercase addresses to `research/findings/base-0xef-tokens/data/coinbase_api_tokens.json`, checked against the saved HTTP 200 response from `https://api.coinbase.com/v1/tokenized-stocks?page_size=500`, retrieved on 2026-10-22. No address prefix assigns the listing label. The case study selects {{b20_sample_n}} tokens with available routes and holder evidence from that survey; it is not a random accuracy sample. honeypot.is flagged every selected token. At block **{{b20_trade_block}}**, saved router buys and sells refute that label for **{{b20_refuted}}**; **{{b20_unresolved}}** remain unresolved because the tested swaps revert. GoPlus returned no honeypot field for these cases. This supports a provider-specific observation, not a claim that all scanners misclassify B20 tokens or that their governance has been established.

A separate policy-registry check at block **{{b20_policy_block}}** finds that policies 5, 118, and 119 reject **{{policy_three_denied}}/{{policy_mirror_n}}** addresses in a saved third-party list mirror, while policy 122 rejects **{{policy_fourth_denied}}/{{policy_mirror_n}}**. The identities of the three differing entries are saved; why those policies differ was not established. The tested Tornado Cash router is authorized by all four policies, consistent with its **2025-03-21 OFAC delisting** [@treasury_delisting; @ofac_removal]. The mirror is not a fresh legal determination, and the registry and router checks use different blocks, amounts, and routes.

## Ethereum launch-state results

### Primary series and excluded outcomes

The primary series measures an early trading encounter. Table 9 separates observed sale/tax harm from missing-outcome bounds; sampling intervals do not include classification error. The primary domain includes above-floor unknown outcomes ({{v21_unresolved_comparison}}), which dilute the 2026 rate more. Recorded 2026 causes include {{v21_2026_non_native_quote_unknown_n}} sampled V4 non-native-quote route failures and {{v21_2026_no_sell_quote_unknown_n}} sampled missing-positive-sell-quote outcomes. Among known trade outcomes only, the 2026 ratio is {{v21_2026_known_outcome_harm_share}}; this comparability sensitivity is not the primary rate. Table 9c reports coverage and the pooled-assumption classification scenario. For January–September 2026, the observed all-new rate is {{v2_2026_all_new_share}}; it describes observed trade harm rather than all harm among new tokens.

Table 9. Launch-state sale restriction or token tax ≥10%. Brackets are approximate sampling intervals. The buy-failure range adds could-not-buy outcomes at its upper endpoint; the second upper endpoint adds all unresolved outcomes. The 2025 all-new column excludes February. Effective support, floor diagnostics and the classification envelope are in Appendix D.

{{v2_annual_table}}

Table 9b. Estimated absolute numbers of tokens from expansion weights, with approximate sampled-outcome count intervals; these pooled domains do not separately propagate marginal census-count uncertainty. The sale/tax and below-floor categories are disjoint at +300 and have different rate denominators; counts refer to the covered frame and do not identify scam intent. The 2026 row covers January–September.

{{v2_count_table}}

Table 9c. Blind-check cell coverage and the pooled-assumption classification range. “No check” is domain weight in era × venue × current predicted-category cells without any blind replay. “Decidable” and “same year” are separate coverage criteria. The range assumes pooled precision/NPV transport, excludes sampling uncertainty, and is not a confidence interval; unresolved outcomes have an additional upper endpoint.

{{v21_coverage_table}}

Below-floor pooled weight rises from **{{v2_2024_no_meaningful_liquidity_pooled}} in 2024 to {{v2_2025_no_meaningful_liquidity_pooled}} in 2025 and {{v2_2026_no_meaningful_liquidity_pooled}} in 2026**. Saved first-liquidity evidence shows that most below-floor V2 pools were meaningfully funded and then depleted within about an hour. Table 10b distinguishes initial funding from the later balance, and Table 10c reports both descriptive combined outcomes. A decline in observed sale/tax harm therefore does not identify a decline in overall harm.

Table 10. Descriptive shares of pooled token weight. Could-not-buy and other-unknown categories can lie inside or outside the above-floor domain; bounds use the appropriate within-domain mass. Small-support rates are withheld.

{{v2_excluded_table}}

Table 10b. Initial funding and depletion by year and venue. First-state amounts refer to the first positive event, not block end. Initial-funding shares use all pooled weight and are observed lower bounds when first liquidity is unknown; the measured share is explicit. Loss below floor conditions on first liquidity ≥0.01 ETH. Withdrawal-direction evidence means both raw token and quote reserves fall, rather than the token reserve rising as in a sale. It does not identify the withdrawing address or malicious intent.

{{v21_first_liquidity_table}}

The V2/Sushi check below conditions instead on pools below the floor at +300. It reports first funding, the weighted median first quote, and reserve direction; unknown first states remain visible rather than being guessed.

{{v21_v2_below_origin_table}}

Table 10c. Descriptive combined outcomes. “Harmful or below floor at +300” uses all pooled tokens; “harmful or emptied within the hour” conditions on initially funded pools and counts sale/tax harm or subsequent below-floor liquidity. Neither is an attributed rug/scam rate. Missing-outcome upper bounds are identification bounds, not sampling intervals.

{{v21_combined_table}}

Figure 1 places the annual trade rates beside the venue weights.

![Figure 1. Ethereum creation-cohort trade harm at first liquidity +300 blocks, requiring ≥0.01 ETH-equivalent quote liquidity. The shaded band is the all-venue approximate sampling interval. The upper panel draws sufficiently supported rates; the lower panel shows measurable-domain venue weights with a separate legend. Undefined venue-year cells are gaps; rates with effective support below 30 are withheld, including the fixed-mix series when its weakest venue is below 30. Open markers and dashed final segments mark January–September 2026 rather than a complete year.](figures/annual_headline.png)

### Venue, launcher, and liquidity sensitivity

A change in the pooled rate is conditional on both venue and creation-method composition. It cannot be read as a uniform change in launch quality. Table 11 supplies the grouped V1/V2-plus-Sushi rate (identical to literal Uniswap V2 from 2023 onward), V3 and V4 rates with domain weights, direct versus factory/launcher creation, and support for standardization to the saved 2024 mix (**{{v2_reference_mix}}**). Standardization controls only venue shares, not launcher templates, cohort age, or classifier sensitivity; missing required venue support produces NA. The 2024–2025 decline is present within V2, so changing venue shares cannot explain it alone. The 2025–2026 V2 point comparison is nearly flat, but its 2026 effective support is {{v2_2026_uniswap_v2_only_support}}, below 30, so that rate is withheld. Fixed-mix point estimates and their saved intervals also have minimum constituent support below 30 in every defined year and are withheld; their input mix is still reported. V4-specific positive support remains limited.

Table 11. Within-venue and creation-method rates in the primary domain, plus fixed-venue-mix standardization.

{{v2_composition_table}}

Table 12 shows real-versus-virtual V4 floors, both alternative all-venue floors, tax boundaries, core fees, legacy selection, and block-tail sensitivities. The legacy +7200 result changes pool selection and liquidity anchoring as well as timing; it is not a controlled estimate of the offset effect alone. Appendix C records early-harm pools that are below the floor at a strictly later, liquidity-known state of the same pool, with effective-support diagnostics. The early label remains in the primary numerator; the transition is not itself rug attribution. High-fee V4 pools can mechanically permit exit while imposing severe round-trip costs. Their primary tax excludes core LP fees, while the fee-inclusive sensitivity combines observed tax and the pool fee on each leg. A mechanically sellable result is not a recommendation or a claim of economic viability.

{{v2_sensitivity_table}}

Real-principal versus virtual-V4 floor results are {{v21_real_virtual_comparison}} The real-principal definition is primary. This sensitivity and the limited 2026 within-venue support prevent interpreting 2025–2026 as a general improvement in launch outcomes.

Figure 2 compares liquidity-floor and observation-policy sensitivities.

![Figure 2. Real-liquidity-floor, virtual-V4, and observation-policy sensitivity; rates with effective support below 30 are withheld. The tiny floor is 10⁻⁹ ETH; the primary floor is 0.01 ETH; the stricter floor is 0.1 ETH, applied across supported venues. The legacy +7200 line also uses the earlier selection/anchor policy. Differences therefore describe policy sensitivity, not causal effects of waiting alone.](figures/sensitivity.png)

A methods finding explains a misleading boundary in the superseded tiny-floor series. At Ethereum block **{{fee_activation_block}}** (2025-12-27), Uniswap V2's factory enables protocol-fee collection. Subsequent provider burns can leave newly minted protocol-owned LP shares in addition to permanently locked minimum liquidity. The remaining quote balance can exceed the old floor despite the provider's exit. Independent parent-state calculations reproduce the fee-mint integer formula in **{{fee_formula_checks}}** saved receipts. This is real residual liquidity crossing an arbitrary threshold, not evidence that drained pools recovered. The venue-consistent floor and earlier observation address that eligibility artifact; neither alone proves that a pool is safe.

Tiny-floor labels among residual-liquidity pools are an informative-exclusion sensitivity: the excluded pools are not a random subset of launches. Those labels are noisier near integer/route limits and are not promoted into the headline.

{{v21_residual_table}}

The all-venue ≥0.1 ETH floor sensitivity in Table 12 differs from the V1/V2-only ≥0.1 ETH tradable stratum. The latter's 2026 rate is withheld because effective support is below 30.

Figure 3 shows monthly variation where effective support reaches 30; other cells are gaps.

![Figure 3. Monthly Ethereum launch-state series. Rates and intervals with effective support below 30 are withheld; annual and sufficiently supported quarterly results carry the interpretation. The band is approximate sampling uncertainty and excludes classification error. Missing values are gaps, not zero observations.](figures/monthly_series.png)

### Delayed restrictions and within-day outcomes

This saved matched-analysis sensitivity retains the eligibility and labels frozen before the real-V4-floor rebuild, including virtual V4 eligibility; its aggregate is not a same-domain update of the headline. Matching the same selected pool at first liquidity +300 and exactly +7,200 blocks (approximately one day) tests whether harm simply activates later. Initially normal means sellable, low-tax states labelled normal or owner-controlled. Within-day harm counts launch harm or subsequent above-floor harm among initially normal pools, retaining late depleted pools in the launch-measurable denominator. In this matched subset, census-weighted launch-time → within-day harm is **{{delayed_2024_all_launch_share}} → {{delayed_2024_all_within_day_share}}** in 2024, **{{delayed_2025_all_launch_share}} → {{delayed_2025_all_within_day_share}}** in 2025, and **{{delayed_2026_all_launch_share}} → {{delayed_2026_all_within_day_share}}** in January–September 2026. The decline from 2024 persists; the small later year-to-year point decline does not. These matched estimates differ from the full annual headline.

The surviving initially normal V1/V2 sample contains {{delayed_2024_v1v2_surviving_n_positive}}/{{delayed_2024_v1v2_surviving_n_sampled}} delayed-harm observations in 2024, {{delayed_2025_v1v2_surviving_n_positive}}/{{delayed_2025_v1v2_surviving_n_sampled}} in 2025, and {{delayed_2026_v1v2_surviving_n_positive}}/{{delayed_2026_v1v2_surviving_n_sampled}} in 2026. Weighted rates and intervals are withheld because positive effective support is below 30 (2026: {{delayed_2026_v1v2_surviving_positive_kish_n_eff}} positive / {{delayed_2026_v1v2_surviving_denominator_kish_n_eff}} denominator Kish observations). The counts indicate a delayed-restriction signal that needs a larger sample. Separately, **{{delayed_2024_v1v2_below_floor_share}}, {{delayed_2025_v1v2_below_floor_share}}, and {{delayed_2026_v1v2_below_floor_share}}** of initially normal V1/V2 weight with known late liquidity is below the floor in 2024–2026. This is neither a trade-failure label nor attributed liquidity withdrawal. Exact followup covers {{delayed_2024_normal_followup_coverage}}, {{delayed_2025_normal_followup_coverage}}, and {{delayed_2026_normal_followup_coverage}} of initially normal aggregate weight in 2024–2026; matching is selective, and intervals exclude matching-selection and classification error.

Purposive manual review of **{{delayed_reviewed_cases_n}}** transitions finds confiscatory fee changes, external restrictions, and code installed at previously empty fee recipients, including **{{delayed_reviewed_eip7702_n}} EIP-7702 delegations**. These are observed mechanisms, not population mechanism frequencies or intent attribution. Fresh-buyer simulations at two states do not establish an earlier holder's overnight experience, and restrictions beyond the followup remain unobserved. A launch-time label is not a safety grade.

### Liquidity withdrawal remains a different outcome

The separate historical liquidity audit reconstructs LP principal withdrawals, swaps, fee collection, and possible migrations. It requires at least 80% of peak quote liquidity to be withdrawn within 30 days for the withdrawal outcome, and a creator/deployer link for the narrower insider-linked outcome. A falling reserve or price alone is insufficient. In its {{lp_blind_n}}-case enriched blind sample, the final reconstruction finds {{lp_blind_rugged_insider}} insider-linked removals, {{lp_blind_died_by_selling}} selling-driven declines, {{lp_blind_stable_or_growing}} surviving-liquidity cases, and {{lp_blind_undetermined}} undetermined cases. These are sample verdicts, not annual prevalence estimates.

The old liquidity screen and second-pass sample were conditioned on a different, later trade domain and miss some pools already drained before measurement. We do not transport their conditional annual ranges to the new early-liquidity domain or add them to the trade headline. A token can fail to exit early and later lose liquidity; the outcomes overlap. The absence of a population rug point estimate is a coverage limitation, not evidence that rug pulls are rare. The first-liquidity removal descriptions above answer a different, shorter-horizon question and do not inherit an insider attribution from this audit. Below-floor and unresolved outcomes remain prominent for that reason.

## Validation and independent checks

### Current-state provider consistency

The fresh comparison re-queries {{validation_cohort_n}} existing chain/address rows; the original preparation supplied {{validation_original_pairs_n}} simulator pairs. The main Ethereum frame contains {{validation_ethereum_n}} tokens, with task-specific paired-complete denominators below. It runs the revised classifier with the uniform floor. The main validation scope is **current-state Ethereum V2/V3 only**. The launch-state series additionally marks known inconsistent buy replays unknown; this provider comparison does not independently validate that historical diagnostic. Provider-internal execution blocks and caches are not disclosed; observed RPC heads must not be presented as provider block pins. Fresh provider/simulator batches satisfy the saved alignment envelope, while earlier observations include multi-hour lags. Provider documentation also defines different fields, tax coverage, and failure modes [@honeypot_api; @goplus_api].

Table 13. Fresh aligned comparisons under the saved post-fix liquidity and tax rules. Matrix order is `[[both negative, first negative/second positive], [first positive/second negative, both positive]]`. Positive and negative *specific agreement* are symmetric overlap measures, not directional sensitivity or specificity. PABAK is twice raw agreement minus one. κ intervals are bootstrap intervals; raw agreement intervals use Wilson's method. All condition on observed labels and exclude classification error.

{{validation_table}}

For simulator–GoPlus honeypot labels, **{{validation_sim_vs_goplus_honeypot_n00}}/{{validation_sim_vs_goplus_honeypot_n}}** paired rows are jointly negative and **{{validation_sim_vs_goplus_honeypot_n11}}** jointly positive. Thus {{validation_sim_vs_goplus_honeypot_agreement}} raw agreement and κ={{validation_sim_vs_goplus_honeypot_kappa}} are consistent: high agreement is driven by negatives, while positive specific agreement is {{validation_sim_vs_goplus_honeypot_positive_specific_agreement}}. The liquid-stratum results below also contain essentially no joint honeypot positives. All {{validation_sim_vs_honeypot_honeypot_n01}} simulator–honeypot.is honeypot disagreements are provider-positive/simulator-negative, with {{validation_sim_vs_honeypot_honeypot_n10}} in the reverse direction. The simulator is the more lenient caller; holder-test positives and simulator under-detection are both compatible with that direction, motivating the separate classification-error scenario. Perfect high-tax agreement rests on very few positives; an empirical bootstrap cannot invent unobserved disagreements, so degenerate κ intervals do not prove perfect performance.

Table 14. Honeypot agreement by original selection stratum. The original “dust”/“thin”/“liquid” names describe selection, not the revised floor at the new observation state. Structurally unavailable paired labels remain NA.

{{validation_strata}}

The candidate-frame weighting is a descriptive calibration of the original new-token liquidity strata; top-token and supplementary selections are excluded. It does not identify population agreement because the age-filtered sample and candidate frame differ, and the above-floor paired support covers only a small fraction of candidate-frame weight. Saved weighted counts, coverage, Kish support, and bootstrap intervals accompany the aggregate tables. Neither provider is ground truth; honeypot.is is itself a simulator, so agreement shows consistency rather than independent accuracy.

{{validation_calibrated}}

Table 15. Earlier versus fresh observations, with timing strata and the revised rules. Comparisons across time can reflect real state changes as well as alignment; only matched identities support a paired difference calculation.

{{validation_lag}}

Two independent adjudications of earlier disagreement scopes agree on **{{adjudicator_result}}**. This low agreement supports qualitative explanations—holder-sample failures versus fresh-buyer exits, rounding, and static capabilities versus effects—rather than a headline accuracy score after adjudication.

### Blind router replays and classifier failure modes

Independent implementations construct canonical-router buys and sales without production simulator labels. Earlier V4 checks exposed a zero-output-buy bug: an EVM-successful purchase acquired nothing, and a subsequent attempt to sell nothing was incorrectly called a honeypot. The corrected rule requires positive acquired balance and a meaningful exit attempt. Agreement between the independent verifier's two routes is not a post-fix simulator accuracy result.

The seeded diagnostic replay of {{origin_n}} generic transfer-from wrapper failures records failure origin separately; a wrapper string alone is not a token-level explanation. Pool, router, dependency, gas, and unresolved paths remain distinct. This selected failure audit does not identify the error rate of every honeypot label.

{{origin_table}}

The legacy V3/V4 blind sample has five confirmed selected V3 honeypot positives and no missed honeypot among 60 sampled V3 normal rows; the old-label-stratified selection excludes owner-controlled rows and does not identify venue-population recall. The legacy honeypot-label task has no V4 honeypot positive; the separate honeypot-or-high-tax task includes two V4 high-tax positives. These are different tasks. The unweighted V3 case-control recall row is omitted.

Table 16. Honeypot-label task: independent V3/V4 sample precision and NPV before restricting to the revised liquidity domain. These selected cases were tested at legacy states with adaptive amounts; they cannot validate historical launch-state classification across every era.

{{blind_v3_table}}

### Independent launch-state blind replay

{{blind300_methods}}

{{blind300_table}}

{{blind300_weighted_table}}

{{blind300_results}}

For harmonized scoring, a verifier sale with token tax ≥10% becomes high tax rather than unrestricted sellability; core LP fees remain separate. Appendix B names the **honeypot-or-high-tax** task and reports sample-conditional metrics after the real-liquidity floor and state deduplication. Precision and NPV condition on sampled predicted labels. Unweighted case-control recall is not population sensitivity. The new replay weights sampling cells for sensitivity and retains uncertainty about unobserved misses. Unsupported strata remain undefined. Appendix D retains the strict and conditional classification envelopes; the main text reports coverage and the pooled-assumption scenario.

## Limitations, evidence access, and ethics

The snapshot is ranked, includes correlated deployments, and has unequal chain depth. Its intervals are descriptive, and market-cap values do not measure local chain float. Securities and bridge tags are incomplete. The behavior battery's golden expectations match **{{golden_result}}**; the remaining mismatches include changing token implementations, source-label errors, and harness limitations. This is a selected regression set, not population accuracy. Fresh derived holders and calibrated storage overrides cannot exercise every address-specific permission, checkpoint, or economic state. Non-Ethereum historical drift often cannot be queried through the available endpoints.

The Ethereum deployment series misses some non-clone proxy tokens and late first-pool strata. Tokens outside supported factories/quotes, a later-only pool, unavailable historical state, and unmeasured quote conversions remain unknown. Pool quote balances can overstate in-range liquidity, while one-sided concentrated liquidity can allow a round trip despite a low pre-buy quote balance. The V4 headline uses reconstructed real principal; its virtual-amount sensitivity is especially far from an execution-depth equivalence. A single test can miss delayed restrictions, privileged exemptions, amount-dependent taxes, transfer-only behaviors, or a later rug pull.

The bootstrap and census-count propagation do not establish nominal coverage under launch bursts. In the saved finite-population stress exercise, rare large clusters cause severe undercoverage in both domains; these synthetic rates do not estimate coverage on Ethereum.

{{bootstrap_checks}}

 The observed block-tail inflation sensitivity cannot account for bursts absent from the draw. Sampling intervals omit classification error, missing-frame mass, and incomplete caller/amount support. Legacy blind checks use selected older labels and states. The +300 replay adds independent historical support only within its pinned binary-prediction frame; pooled transport, untargeted categories, sparse V4 positives and different execution horizons remain limitations. Earlier cohorts lack direct +300 replay validation. Current-state provider consistency must not be presented as validation of the entire historical series.

No transaction is broadcast and no private key is used. Named observations are neutral, limited to reproduced calls and their recorded states. An EOA classification is never a claim about custody, and an address-policy result is never a legal or intent determination. This paper reports aggregate results from restricted sources. Publication artifacts must exclude unlicensed Huynh-derived token lists and Zellic-derived per-token rows; their existence in the private research workspace is not permission to redistribute them. Independently redistributable evidence must be distinguished from restricted source material.

The evidence ledger, `numbers.json`, is generated directly from saved data by `build_numbers.py`; each entry specifies its source, query, and source-file hash. `check_numbers.py` independently checks weighted joins, confusion matrices, structural NA handling, citations, and rendered text. The local full evidence stores contain request/response bodies, URLs or endpoints, retrieval times, and hashes. Local pointers are not promises of a publicly hosted raw bundle; provider bodies remain local pending redistribution review. The release must distinguish shipped aggregates, reproducible offline builders, and any separately available raw evidence.

From the repository root, the offline paper workflow is:

```sh
.venv/bin/python article/build_numbers.py
.venv/bin/python article/check_numbers.py
cd article
pandoc paper.md --citeproc --bibliography=refs.bib --standalone --mathml --css=paper.css --embed-resources -o paper.html
```

## Appendix A: quarterly reporting and convenience extras

Only quarters meeting the stated effective-support and frame-coverage rule are interpreted. Monthly rates below effective support 30 are withheld even when a curve could be drawn.

{{v2_quarter_table}}

Convenience extras do not enter the main prevalence tables or rank-matched tests.

{{extras_table}}

The following diagnostic preserves sample counts from the superseded later-state series. It uses the old tiny floor and selection policy, whose floor coverage differed by venue, and is not the early-liquidity headline or a causal attribution of rug pulls. Rates are withheld because this saved table lacks effective-support diagnostics. The 2026 eligibility discontinuity is explained by protocol-fee residuals above.

{{superseded_dust_screen}}

## Appendix B: era × venue blind support

The task here is honeypot **or high tax ≥10%**, unlike the honeypot-only task in Table 16. These are sample-conditional harm metrics pooling jointly above-floor, decidable legacy and +300 checks after state deduplication. Unknown/outside-floor counts remain visible. They do not inherit the probability-weighted scope of the separately reported +300 replay. A selected normal stratum does not by itself identify sensitivity among all true positives; no-positive cells have undefined precision or recall.

{{v2_blind_era_table}}

## Appendix C: matched-pool timing transitions

Only the same selected pool with a strictly later, liquidity-known legacy state enters this diagnostic. Shares are descriptive weighted transitions within the observed subset; they do not identify population withdrawal rates or isolate the effect of waiting alone.

{{v2_timing_transitions}}

## Appendix D: effective support and classification envelopes

The following table preserves the effective positive/denominator sizes and floor diagnostics. Rates and intervals below effective support 30 are withheld. The conditional envelope includes unrestricted error in unsupported cells; it is not a 95% interval and must not be substituted for the pooled-assumption scenario. Without transport restrictions the strict classification range is 0–100%.

{{v2_diagnostic_table}}

## References
