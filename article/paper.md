---
pagetitle: "Weird ERC-20 Live — behavior and Ethereum launch-state outcomes"
lang: en
bibliography: refs.bib
csl: numeric.csl
link-citations: true
---

# Weird ERC-20 Live: token behavior across six EVM chains and launch-state trading outcomes on Ethereum, 2017–2026

## Abstract

We pair a snapshot of 4,198 ranked token deployments across 6 EVM chains with an Ethereum launch study that tests a buyer's sale and tax outcomes about an hour (300 blocks) after first liquidity, requiring at least 0.01 ETH-equivalent real quote liquidity. Observed sale failure or token tax of 10% or more fell from **23.2%** in 2024 (approximate 95% sampling interval 21.2%–25.4%) to **8.7%** in January–September 2026 (6.4%–11.2%), while the share of pooled tokens already below that floor rose from **40.4% to 72.6%**; most below-floor V2 pools had initially been funded, so these results do not identify a fall in overall harm. The 2024–2025 fall also appears within Uniswap V2 (24.2% to 9.6%), whereas the 2025–2026 aggregate depends on V4 liquidity and venue composition, and 2026 V2 support is just below the reporting threshold. An independent +300 replay covers 376 selected states, with 335/341 real-floor states yielding a known binary outcome; inference remains conditional on the sampled prediction frame, sampling intervals exclude classification error, and blind-cell coverage and a pooled error scenario describe the remaining gap.

::: {.key-results}
**Key results**

- **Sale/tax harm:** 23.2% in 2024 versus 8.7% in January–September 2026; estimated affected tokens are 13,205 and 1,868, respectively (count intervals in Table 9b).
- **Below-floor pools increase:** below-floor pooled weight is 40.4%, 55.6%, and 72.6% in 2024–2026. First-liquidity and reserve-direction evidence distinguish depletion from pools that were never meaningfully funded; neither establishes insider attribution.
- **Venue matters:** the 2024–2025 fall holds within V2; the 2026 V2 rate is withheld because effective positive support is below 30. Real V4 position principal determines eligibility; the old virtual amount is a sensitivity. Fixed-2024-mix estimates have insufficient constituent support and are withheld.
- **A launch label expires:** the earlier matched-domain within-day harm sensitivity is 20.2%, 7.0%, and 7.7% in 2024–2026; 9 of 24 purposively reviewed delayed cases involve EIP-7702 fee-recipient delegations.
- **Validation has a scope:** independent +300 replays test 376 selected token states, with no-liquidity, failed buys and unresolved routes reported separately. Provider agreement tests current-state Ethereum V2/V3; it is not historical accuracy.
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

The unit is a **deployment on a chain**, not an independent economic asset or issuer. Market-cap rank is the global asset capitalization among assets deployed on that chain, not the value circulating locally. The inventory has 4,740 deployments; the main prevalence domain has 4,198 ranked rows. The 542 additions from corroborating token lists and pool/holder discovery are a separate convenience sample. The deepest rank band exists only on Ethereum; Optimism has less coverage than the other chains.

Table 1. Snapshot coverage. Empty deep bands on other chains must not be interpreted as zero token prevalence.

| Chain | Ranks 1–100 | 101–500 | 501–2000 | Extras |
| --- | --- | --- | --- | --- |
| Ethereum | 100 | 400 | 1500 | 103 |
| Base | 100 | 400 | 0 | 82 |
| Arbitrum | 100 | 400 | 0 | 152 |
| BNB Smart Chain | 100 | 400 | 0 | 93 |
| Polygon | 100 | 400 | 0 | 45 |
| Optimism | 100 | 98 | 0 | 67 |

Read-only calls test return data, transfer and approval transitions, fresh-caller restrictions, metadata, callbacks, fee behavior, and balance/share accounting. Where needed, a derived holder is funded by a calibrated balance-slot override; tokens that require genuine holder history use a real-holder simulation. Each test is isolated and discarded. Every saved observation records chain, block, caller, parameters, raw request/response, retrieval time, and response hash. Rolling block pins on short-retention nodes mean the snapshot is not simultaneous across all deployments. Failed retrievals, inaccessible funding, and exhausted state windows remain unknown with a reason.

The control analysis follows recognized proxy slots and custom forwarding paths, then traverses owners, administrators, roles, multisigs, and timelocks. A claimed upgrade authority requires a successful read-only call from that authority and a reverting stranger control where the path is testable. The displayed class is the **weakest firm path found**, including any enforced delay; it is not merely the first address encountered. Unverified wrappers stay unresolved. An externally owned account (EOA) is an on-chain account classification: it says nothing about the number of people, MPC shares, HSMs, custody, or operational policy behind a key.

Rates use feature-specific measured denominators. A union is positive if a constituent is positive, but negative only when all required constituents are negative. Wilson intervals in the ranked tables are **descriptive 95% intervals for this snapshot**, not design-based intervals for all deployed tokens, and exclude classification error. Securities-excluded rates use a saved name/category rule supplemented by recognized Base stock-policy membership; this is an imperfect composition sensitivity, not an exhaustive legal classification.

### Ethereum creation cohorts and sampling

The deployment census spans 117 months from January 2017 through September 2026. The sampling frame contains 47,963 rows. The fixed bytecode classifier requires totalSupply/balanceOf/transfer dispatch and the Transfer event topic, resolving EIP-1167 clones. Runtime-bytecode and clone classification separate non-LP tokens from recognizable LP/pool-share tokens. A new LP token is not the same quantity as a new token that acquired a DEX pool. The largest monthly estimate of new non-LP tokens is **2026-05: 44,264 [31,959, 56,568]** (month: estimate [reported interval]). Earlier complete creation tables come from the Paradigm Data Portal and Zellic's all-ethereum-contracts collection; the later period combines pool-linked sampling with random block sampling of other creations, retaining original month/source/stratum expansion weights. Here “non-LP” means not recognized as LP/pool-share by the fixed classifier; unrecognized receipt and LP implementations can remain. Successful-create ancestry checks exclude reverted creation attempts in the RPC segment.

The census estimator and the outcome estimator have separate validation. The census's complete-table Monte Carlo exercise at 1,000 sampled blocks gives 93.6% nominal-95% coverage for the all-contract decomposition estimator and 93.1% for the non-LP simple-random-sampling estimator. A separate live-RPC comparison covers 14/16 (87.5%) month-series intervals, with mean **relative** error -0.9%. Both live misses occur in May 2023, a bursty launch month. This is not a mean error in percentage points. Non-clone proxies that hide the ERC-20 implementation remain a known census recall gap; the saved proxy-candidate adjustment is not silently added to the population.

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

Among ranked deployments, 1,891/4,182 have proxy evidence, 1,682/4,041 have an upgrade authority, and 314/4,150 have a verified path terminating in an EOA without a detected timelock. Static blocklist machinery appears in 469/4,183; the broader capability-or-tested-address-rejection union is 921/4,183. These definitions must not be interchanged. They are observed integration properties, not allegations of abuse.

Table 3. Main ranked prevalence, mapped to the d-xo taxonomy [@dxo_weird_erc20]. Convenience extras are excluded. The blocklist union includes the Ronin exploiter recipient and the formerly sanctioned Tornado Cash router, delisted on 2025-03-21 [@treasury_delisting; @ofac_removal].

| Behavior / capability | Ranked: yes / measured (rate) | Descriptive Wilson 95% interval | Excluding securities: yes / measured (rate) |
| --- | --- | --- | --- |
| Reentrant calls (hooks) | 16/4,065 (0.4%) | 0.2%–0.6% | 16/3,576 (0.4%) |
| Missing return values | 15/4,170 (0.4%) | 0.2%–0.6% | 15/3,656 (0.4%) |
| Fee on transfer | 29/4,066 (0.7%) | 0.5%–1.0% | 29/3,577 (0.8%) |
| Observed balance drift | 26/2,484 (1.0%) | 0.7%–1.5% | 24/2,192 (1.1%) |
| Share-accounting capability | 320/4,169 (7.7%) | 6.9%–8.5% | 86/3,655 (2.4%) |
| Upgrade authority exists | 1,682/4,041 (41.6%) | 40.1%–43.2% | 1,175/3,531 (33.3%) |
| Flash mintable tokens | 15/4,170 (0.4%) | 0.2%–0.6% | 15/3,664 (0.4%) |
| Blocklist capability or tested-address rejection | 921/4,183 (22.0%) | 20.8%–23.3% | 439/3,669 (12.0%) |
| Pausable tokens | 1,097/4,183 (26.2%) | 24.9%–27.6% | 817/3,669 (22.3%) |
| Approval race protections | 47/4,170 (1.1%) | 0.8%–1.5% | 47/3,656 (1.3%) |
| Revert on approval to zero address | 3,712/4,175 (88.9%) | 87.9%–89.8% | 3,221/3,661 (88.0%) |
| Revert on zero-value approvals | 14/4,175 (0.3%) | 0.2%–0.6% | 14/3,661 (0.4%) |
| Revert on zero-value transfers | 138/4,078 (3.4%) | 2.9%–4.0% | 138/3,589 (3.8%) |
| Multiple token addresses / forwarding | 13/4,155 (0.3%) | 0.2%–0.5% | 13/3,649 (0.4%) |
| Low decimals (<=5) | 79/4,183 (1.9%) | 1.5%–2.3% | 67/3,669 (1.8%) |
| High decimals (>18) | 2/4,183 (0.0%) | 0.0%–0.2% | 2/3,669 (0.1%) |
| transferFrom with src == msg.sender | 145/4,175 (3.5%) | 3.0%–4.1% | 145/3,661 (4.0%) |
| Non-string metadata | 4/4,183 (0.1%) | 0.0%–0.2% | 4/3,669 (0.1%) |
| Revert on transfer to zero address | 3,957/4,175 (94.8%) | 94.1%–95.4% | 3,457/3,661 (94.4%) |
| No revert on failure | 24/4,175 (0.6%) | 0.4%–0.9% | 24/3,661 (0.7%) |
| Revert on large approvals/transfers (uint96) | 35/4,070 (0.9%) | 0.6%–1.2% | 35/3,581 (1.0%) |
| Code injection via token name | 0/4,183 (0.0%) | 0.0%–0.1% | 0/3,669 (0.0%) |
| Unusual permit (DAI-style) | 15/4,183 (0.4%) | 0.2%–0.6% | 15/3,669 (0.4%) |
| Transfer of less than amount (max-uint = all) | 0/4,175 (0.0%) | 0.0%–0.1% | 0/3,661 (0.0%) |
| ERC-20 representation of native currency | 1/4,155 (0.0%) | 0.0%–0.1% | 1/3,649 (0.0%) |

Some taxonomy names describe majority protective behavior: most measured tokens reject approval or transfer to the zero address. For those rows, failure to reject is the minority behavior; the category name does not mean every positive observation is unusual or unsafe.

The corrected fee detector retains 29 inventory observations with a zero recipient pre-balance and a finite fee no greater than the entire transfer. Fresh-address phantom balances are a separate anomaly and are not counted again as fees. Share-accounting capability excludes binary-shift calibration artifacts and remains distinct from observed seven-day drift. Most securities-associated share evidence comes from tokenized-stock multipliers and decimal/share scaling, rather than observed balance drift.

The main securities sensitivity removes 518 ranked deployments. Multi-ticket families contribute repeated implementations and policies, so deployment counts are not independent issuer counts. Table 3 shows that their inclusion materially affects blocklist and share-accounting rates. Names in the following composition table are descriptive family tags attached to deployments whose matrix checks support the counted features; they do not establish the legal issuer of every deployment.

Table 4. Ranked securities-sensitivity composition. Coinbase-listed B20 membership is an address join to a saved Coinbase public API response. Other families, including Ondo and Backed/xStocks, use saved names/categories. Feature counts refer to reproduced on-chain observations or capability checks; listing does not establish issuance.

| Family / listing basis | Deployments | Upgrade authority | Blocklist union | Share capability |
| --- | --- | --- | --- | --- |
| Backed/other | 27 | 27 | 22 | 0 |
| Backed/xStocks | 149 | 149 | 147 | 141 |
| Coinbase-listed B20 (API address join) | 8 | 0 | 8 | 8 |
| Dinari (name-derived) | 7 | 7 | 7 | 7 |
| Ondo | 219 | 219 | 217 | 0 |
| Reality (name-derived) | 70 | 70 | 69 | 69 |
| Spiko | 12 | 12 | 0 | 0 |
| other_or_unattributed | 26 | 23 | 12 | 9 |

### Rank depth and cross-chain comparisons

The Ethereum-only tail cannot support a claim that privileges decline with rank across all chains. Table 5 reports chain × rank-band cells. Unknown archive drift on other chains remains unmeasured, not an observed absence of drift.

Table 5. Behavior and control by chain and market-cap rank band. Each cell is positive / measured (rate).

| Chain | Rank band | Missing transfer return | Observed transfer fee | Share capability | Observed drift | Blocklist union | EOA upgrade path |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Ethereum | 1-100 | 2/99 (2.0%) | 0/93 (0.0%) | 4/99 (4.0%) | 3/99 (3.0%) | 31/100 (31.0%) | 15/100 (15.0%) |
| Ethereum | 101-500 | 5/399 (1.3%) | 0/380 (0.0%) | 19/396 (4.8%) | 5/396 (1.3%) | 75/400 (18.8%) | 24/395 (6.1%) |
| Ethereum | 501-2000 | 8/1,496 (0.5%) | 13/1,469 (0.9%) | 96/1,492 (6.4%) | 13/1,490 (0.9%) | 371/1,500 (24.7%) | 76/1,494 (5.1%) |
| Base | 1-100 | 0/100 (0.0%) | 0/91 (0.0%) | 3/100 (3.0%) | 0/0 (Not reported (n<30)) | 13/100 (13.0%) | 8/92 (8.7%) |
| Base | 101-500 | 0/400 (0.0%) | 2/393 (0.5%) | 17/400 (4.2%) | 0/1 (Not reported (n<30)) | 48/400 (12.0%) | 27/381 (7.1%) |
| Arbitrum | 1-100 | 0/99 (0.0%) | 0/93 (0.0%) | 7/99 (7.1%) | 0/0 (Not reported (n<30)) | 26/99 (26.3%) | 8/99 (8.1%) |
| Arbitrum | 101-500 | 0/397 (0.0%) | 1/390 (0.3%) | 131/397 (33.0%) | 0/0 (Not reported (n<30)) | 155/397 (39.0%) | 34/398 (8.5%) |
| BNB Smart Chain | 1-100 | 0/97 (0.0%) | 0/93 (0.0%) | 2/97 (2.1%) | 0/0 (Not reported (n<30)) | 18/97 (18.6%) | 17/97 (17.5%) |
| BNB Smart Chain | 101-500 | 0/393 (0.0%) | 6/391 (1.5%) | 18/393 (4.6%) | 0/0 (Not reported (n<30)) | 94/393 (23.9%) | 49/398 (12.3%) |
| Polygon | 1-100 | 0/99 (0.0%) | 0/90 (0.0%) | 1/100 (1.0%) | 1/100 (1.0%) | 19/100 (19.0%) | 11/100 (11.0%) |
| Polygon | 101-500 | 0/398 (0.0%) | 6/390 (1.5%) | 8/398 (2.0%) | 4/398 (1.0%) | 47/399 (11.8%) | 34/399 (8.5%) |
| Optimism | 1-100 | 0/100 (0.0%) | 1/97 (1.0%) | 1/100 (1.0%) | 0/0 (Not reported (n<30)) | 8/100 (8.0%) | 8/99 (8.1%) |
| Optimism | 101-500 | 0/98 (0.0%) | 0/96 (0.0%) | 13/98 (13.3%) | 0/0 (Not reported (n<30)) | 16/98 (16.3%) | 3/98 (3.1%) |

Table 6 limits cross-chain comparisons to ranks 1–500 and repeats them after the securities exclusion. This aligns rank depth, but does not remove issuer, age, or realized rank-composition confounding. The tokens are not independent draws from chain populations.

| Chain | Domain | Proxy | EOA upgrade path | Blocklist union | Share capability |
| --- | --- | --- | --- | --- | --- |
| Ethereum | Ranked ≤500 | 212/500 (42.4%) | 39/495 (7.9%) | 106/500 (21.2%) | 23/495 (4.6%) |
| Ethereum | Ex securities | 186/472 (39.4%) | 37/467 (7.9%) | 86/472 (18.2%) | 13/467 (2.8%) |
| Base | Ranked ≤500 | 197/487 (40.5%) | 35/473 (7.4%) | 61/500 (12.2%) | 20/500 (4.0%) |
| Base | Ex securities | 188/477 (39.4%) | 34/466 (7.3%) | 52/482 (10.8%) | 11/482 (2.3%) |
| Arbitrum | Ranked ≤500 | 359/500 (71.8%) | 42/497 (8.5%) | 181/496 (36.5%) | 138/496 (27.8%) |
| Arbitrum | Ex securities | 215/356 (60.4%) | 35/353 (9.9%) | 43/354 (12.1%) | 11/354 (3.1%) |
| BNB Smart Chain | Ranked ≤500 | 215/498 (43.2%) | 66/495 (13.3%) | 112/490 (22.9%) | 20/490 (4.1%) |
| BNB Smart Chain | Ex securities | 162/445 (36.4%) | 60/442 (13.6%) | 62/439 (14.1%) | 6/439 (1.4%) |
| Polygon | Ranked ≤500 | 293/499 (58.7%) | 45/499 (9.0%) | 66/499 (13.2%) | 9/498 (1.8%) |
| Polygon | Ex securities | 281/487 (57.7%) | 44/487 (9.0%) | 59/487 (12.1%) | 9/486 (1.9%) |
| Optimism | Ranked ≤500 | 71/198 (35.9%) | 11/197 (5.6%) | 24/198 (12.1%) | 14/198 (7.1%) |
| Optimism | Ex securities | 60/187 (32.1%) | 10/186 (5.4%) | 14/187 (7.5%) | 4/187 (2.1%) |

The exploratory Fisher tests apply Holm correction to the saved feature × comparison family. The adjusted tests and denominators are shown rather than the former unadjusted, full-inventory contrasts.

Table 7. Rank-matched Ethereum-versus-rest tests. Holm correction covers 1,300 feature × comparison tests (16 comparisons per feature, with structural exclusions); deployment dependence limits inferential interpretation.

| Feature | Ethereum | Rest | Raw Fisher p | Holm p |
| --- | --- | --- | --- | --- |
| Missing transfer return | 7/498 | 0/2,181 | 7.41e-06 | 0.00864 |
| False on failure | 7/498 | 1/2,181 | 4.97e-05 | 0.0571 |
| Approval race guard | 13/496 | 4/2,179 | 3.06e-07 | 0.000363 |
| Bounded-integer limit | 6/473 | 3/2,123 | 0.00181 | 1 |

The missing-transfer-return contrast is significant at the deployment level (Holm-adjusted p=0.00864); dependence between deployments and shared issuers prevents a chain-population inference.

### Named control examples and address restrictions

Our naming rule is explicit: **name an issuer or issuer-associated deployment in a behavioral sentence only when a reproduced on-chain check supports that sentence**, and state the tested deployment and scope. The examples below document verified EOA-terminal paths; they are not a balanced sample of governance designs. Ticker equality does not establish issuance: third-party bridged or pegged assets are separated from issuer-native deployments. We do not infer intent or off-chain security from a role, selector, or account's code.

Table 8 applies this rule consistently to the checked Ethereum deployments of USDC, cbBTC, BUIDL, and PYUSD. The calls reproduce authority success and stranger rejection at the recorded state; no upgrade transaction was broadcast. An EOA terminal path does not establish individual custody, the absence of MPC/HSM controls, or a single human operator.

| Ethereum deployment | Reproduced method | Terminal EOA | Pinned block | Differential result |
| --- | --- | --- | --- | --- |
| USDC | upgradeTo(address) | 0x807a96288a1a408dbc13de2b1d087d10356395d2 | 26247291 | Authority succeeds; stranger reverts |
| cbBTC | upgradeTo(address) | 0x42d90b3faf2287475cdc863706bd7cc3e938c2af | 26247291 | Authority succeeds; stranger reverts |
| BUIDL | setTarget(address) | 0xe01605f6b6dc593b7d2917f4a0940db2a625b09e | 26247293 | Authority succeeds; stranger reverts |
| PYUSD | upgradeTo(address) | 0xc94bcf6e1d8b3558e3b62e743630d50497e3851c | 26247292 | Authority succeeds; stranger reverts |

For PYUSD specifically, `owner()` returns **0x3af3e85f4f97de7ad0f000b724fb77fe5ffc024b**, whereas the reproduced upgrade path uses **0xc94bcf6e1d8b3558e3b62e743630d50497e3851c**. These are different EOAs. The Paxos owner-role observation must not be mistaken for the upgrade-administrator observation, and neither is a custody claim. USDT's separately reproduced `deprecate(address)` caller differential establishes a legacy forwarding upgrade path even though it is not a DELEGATECALL proxy; its saved owner path is a multisig without a detected timelock.

In the complete inventory, **33** deployments reject the tested formerly sanctioned Tornado Cash router; it was delisted by OFAC on **2025-03-21** [@treasury_delisting; @ofac_removal]. Among the **526** deployments that reject the Ronin-exploiter recipient, **32** also reject this delisted router; a clean-control, one-raw-unit independent retest confirms that intersection. The remaining **1** is a Tornado-only matrix observation outside that independent retest. Rejection can reflect a stale or independent policy list; authorization or rejection here establishes neither current legal compliance nor motivation.

The permissioned-transfer feature has 67 inventory positives, distributed across all chains below. This is narrower than the 110 fresh-contract transfer rejections, which can include other gates and failures. We do not describe all fresh-contract failures as permissioning.

| Chain | Permissioned-transfer observations |
| --- | --- |
| Ethereum | 25 |
| Base | 12 |
| Arbitrum | 11 |
| BNB Smart Chain | 4 |
| Polygon | 13 |
| Optimism | 2 |

### Base protocol-token case study

Base's B20 specification describes protocol-level token execution, which ordinary bytecode/proxy-slot analysis alone does not characterize [@base_b20]. The convenience survey contains **208 Base B20 assets**: 194 joined to Coinbase's public tokenized-stock list and 14 from CoinGecko's Base list. **17** surveyed assets are in the measured Base universe (ranked plus extras). Table 4 includes **8 ranked Coinbase-listed B20 stocks**, or 12 including extras. Membership joins lowercase addresses to `research/findings/base-0xef-tokens/data/coinbase_api_tokens.json`, checked against the saved HTTP 200 response from `https://api.coinbase.com/v1/tokenized-stocks?page_size=500`, retrieved on 2026-10-22. No address prefix assigns the listing label. The case study selects 5 tokens with available routes and holder evidence from that survey; it is not a random accuracy sample. honeypot.is flagged every selected token. At block **52,929,712**, saved router buys and sells refute that label for **2**; **3** remain unresolved because the tested swaps revert. GoPlus returned no honeypot field for these cases. This supports a provider-specific observation, not a claim that all scanners misclassify B20 tokens or that their governance has been established.

A separate policy-registry check at block **52,936,930** finds that policies 5, 118, and 119 reject **117/120** addresses in a saved third-party list mirror, while policy 122 rejects **120/120**. The identities of the three differing entries are saved; why those policies differ was not established. The tested Tornado Cash router is authorized by all four policies, consistent with its **2025-03-21 OFAC delisting** [@treasury_delisting; @ofac_removal]. The mirror is not a fresh legal determination, and the registry and router checks use different blocks, amounts, and routes.

## Ethereum launch-state results

### Primary series and excluded outcomes

The primary series measures an early trading encounter. Table 9 separates observed sale/tax harm from missing-outcome bounds; sampling intervals do not include classification error. The primary domain includes above-floor unknown outcomes (2024: 6.2%; 2026: 9.7%), which dilute the 2026 rate more. Recorded 2026 causes include 52 sampled V4 non-native-quote route failures and 6 sampled missing-positive-sell-quote outcomes. Among known trade outcomes only, the 2026 ratio is 9.6%; this comparability sensitivity is not the primary rate. Table 9c reports coverage and the pooled-assumption classification scenario. For January–September 2026, the observed all-new rate is 0.9%; it describes observed trade harm rather than all harm among new tokens.

Table 9. Launch-state sale restriction or token tax ≥10%. Brackets are approximate sampling intervals. The buy-failure range adds could-not-buy outcomes at its upper endpoint; the second upper endpoint adds all unresolved outcomes. The 2025 all-new column excludes February. Effective support, floor diagnostics and the classification envelope are in Appendix D.

| Creation year | Primary ≥0.01 ETH [approx. 95%] | Buy-failure range / all-unknown upper | All-new [approx. 95%] |
| --- | --- | --- | --- |
| 2017 | NA | Not reported (effective support <30) | Not reported (effective support <30) |
| 2018 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) |
| 2019 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) |
| 2020 | 27.1% [24.3%, 30.0%] | 27.1%–28.8% / 29.7% | 5.1% [4.6%, 5.7%] |
| 2021 | 51.3% [48.9%, 53.7%] | 51.3%–54.0% / 56.2% | 16.3% [15.4%, 17.2%] |
| 2022 | 35.3% [32.9%, 37.8%] | 35.3%–42.3% / 43.8% | 11.8% [10.9%, 12.7%] |
| 2023 | 25.2% [23.1%, 27.4%] | 25.2%–31.4% / 32.0% | 10.8% [9.8%, 11.8%] |
| 2024 | 23.2% [21.2%, 25.4%] | 23.2%–28.8% / 29.5% | 9.1% [8.2%, 10.0%] |
| 2025 | 7.7% [6.5%, 8.9%] | 7.7%–10.4% / 12.1% | 1.4% [1.1%, 1.6%] |
| 2026 Jan–Sep | 8.7% [6.4%, 11.2%] | 8.7%–10.1% / 18.4% | 0.9% [0.7%, 1.2%] |

Table 9b. Estimated absolute numbers of tokens from expansion weights, with approximate sampled-outcome count intervals; these pooled domains do not separately propagate marginal census-count uncertainty. The sale/tax and below-floor categories are disjoint at +300 and have different rate denominators; counts refer to the covered frame and do not identify scam intent. The 2026 row covers January–September.

| Year | Estimated sale/tax-harm tokens [approx. 95%] | Estimated below-floor pooled tokens [approx. 95%] |
| --- | --- | --- |
| 2024 | 13,205 [11,989, 14,521] | 41,743 [39,649, 43,705] |
| 2025 | 3,193 [2,671, 3,703] | 56,543 [55,050, 57,964] |
| 2026 | 1,868 [1,374, 2,428] | 72,555 [71,058, 74,154] |

Table 9c. Blind-check cell coverage and the pooled-assumption classification range. “No check” is domain weight in era × venue × current predicted-category cells without any blind replay. “Decidable” and “same year” are separate coverage criteria. The range assumes pooled precision/NPV transport, excludes sampling uncertainty, and is not a confidence interval; unresolved outcomes have an additional upper endpoint.

| Year | No blind check in cell | Jointly decidable cell coverage | Same-year cell coverage | Pooled-assumption range | Plus all unresolved: upper |
| --- | --- | --- | --- | --- | --- |
| 2017 | NA | NA | NA | Not reported (effective support <30) | Not reported (effective support <30) |
| 2018 | 46.7% | 53.3% | 53.3% | Not reported (effective support <30) | Not reported (effective support <30) |
| 2019 | 35.7% | 64.3% | 64.3% | Not reported (effective support <30) | Not reported (effective support <30) |
| 2020 | 21.5% | 78.5% | 0.0% | 25.1%–28.6% | 31.3% |
| 2021 | 17.4% | 82.6% | 82.6% | 47.5%–52.3% | 57.2% |
| 2022 | 24.0% | 76.0% | 35.3% | 32.7%–36.5% | 45.0% |
| 2023 | 6.8% | 93.2% | 72.6% | 23.4%–26.7% | 33.5% |
| 2024 | 6.2% | 93.8% | 93.8% | 21.5%–24.8% | 31.0% |
| 2025 | 5.1% | 94.9% | 94.9% | 7.2%–9.7% | 14.1% |
| 2026 | 9.9% | 90.1% | 89.2% | 8.0%–10.5% | 20.2% |

Below-floor pooled weight rises from **40.4% in 2024 to 55.6% in 2025 and 72.6% in 2026**. Saved first-liquidity evidence shows that most below-floor V2 pools were meaningfully funded and then depleted within about an hour. Table 10b distinguishes initial funding from the later balance, and Table 10c reports both descriptive combined outcomes. A decline in observed sale/tax harm therefore does not identify a decline in overall harm.

Table 10. Descriptive shares of pooled token weight. Could-not-buy and other-unknown categories can lie inside or outside the above-floor domain; bounds use the appropriate within-domain mass. Small-support rates are withheld.

| Year | Below meaningful-liquidity floor | Could not buy | Late pool | Other unprobeable |
| --- | --- | --- | --- | --- |
| 2017 | Not reported (effective support <30) | Not reported (effective support <30) | 100.0% | Not reported (effective support <30) |
| 2018 | Not reported (effective support <30) | Not reported (effective support <30) | 96.2% | 2.5% |
| 2019 | 14.4% | Not reported (effective support <30) | 45.6% | 22.4% |
| 2020 | 29.5% | Not reported (effective support <30) | 1.9% | 4.7% |
| 2021 | 28.3% | 1.8% | 1.5% | 4.1% |
| 2022 | 42.9% | 3.6% | Not reported (effective support <30) | 5.7% |
| 2023 | 41.3% | 3.4% | Not reported (effective support <30) | 3.8% |
| 2024 | 40.4% | 3.0% | Not reported (effective support <30) | 4.5% |
| 2025 | 55.6% | 1.1% | Not reported (effective support <30) | 4.5% |
| 2026 | 72.6% | Not reported (effective support <30) | Not reported (effective support <30) | 7.6% |

Table 10b. Initial funding and depletion by year and venue. First-state amounts refer to the first positive event, not block end. Initial-funding shares use all pooled weight and are observed lower bounds when first liquidity is unknown; the measured share is explicit. Loss below floor conditions on first liquidity ≥0.01 ETH. Withdrawal-direction evidence means both raw token and quote reserves fall, rather than the token reserve rising as in a sale. It does not identify the withdrawing address or malicious intent.

| Year | Venue | First state measured / pooled | Initially ≥0.01 ETH / pooled (lower) | Below floor at +300 / initially funded [approx. 95%] | Both reserves fell / funded then depleted |
| --- | --- | --- | --- | --- | --- |
| 2017 | All | Not reported (effective support <30) | Not reported (effective support <30) | NA | Not reported (effective support <30) |
| 2018 | All | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) |
| 2018 | V1/V2 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) |
| 2019 | All | 32.8% | 21.9% | 17.9% [14.5%, 22.0%] | 63.9% |
| 2019 | V1/V2 | 100.0% | 66.7% | 17.9% [14.5%, 22.0%] | 63.9% |
| 2020 | All | 94.0% | 89.7% | 29.4% [27.1%, 31.9%] | 97.9% |
| 2020 | V1/V2 | 99.3% | 94.8% | 29.4% [27.1%, 31.9%] | 97.9% |
| 2021 | All | 96.0% | 92.7% | 28.0% [26.1%, 30.0%] | 95.1% |
| 2021 | V1/V2 | 99.8% | 98.0% | 28.9% [26.9%, 30.9%] | 95.2% |
| 2021 | V3 | 99.2% | 71.4% | Not reported (effective support <30) | Not reported (effective support <30) |
| 2022 | All | 94.4% | 93.4% | 45.2% [43.1%, 47.3%] | 89.6% |
| 2022 | V1/V2 | 100.0% | 99.6% | 45.4% [43.3%, 47.6%] | 89.6% |
| 2022 | V3 | 96.6% | 67.5% | Not reported (effective support <30) | Not reported (effective support <30) |
| 2023 | All | 96.2% | 94.7% | 42.4% [40.6%, 44.4%] | 91.5% |
| 2023 | V1/V2 | 100.0% | 99.2% | 42.0% [40.1%, 43.9%] | 91.2% |
| 2023 | V3 | 99.5% | 76.0% | 59.4% [46.9%, 70.7%] | 100.0% |
| 2024 | All | 95.3% | 93.0% | 41.7% [39.8%, 43.6%] | 91.1% |
| 2024 | V1/V2 | 100.0% | 99.0% | 40.8% [38.9%, 42.8%] | 91.0% |
| 2024 | V3 | 97.1% | 72.7% | 57.7% [49.0%, 65.9%] | 92.8% |
| 2024 | V4 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) |
| 2025 | All | 96.3% | 73.0% | 56.1% [54.4%, 57.7%] | 98.9% |
| 2025 | V1/V2 | 99.7% | 94.2% | 57.1% [55.4%, 58.7%] | 98.9% |
| 2025 | V3 | 99.6% | 6.9% | Not reported (effective support <30) | Not reported (effective support <30) |
| 2025 | V4 | 98.7% | 45.7% | Not reported (effective support <30) | Not reported (effective support <30) |
| 2026 | All | 95.1% | 49.7% | 71.7% [69.5%, 74.0%] | 98.8% |
| 2026 | V1/V2 | 99.8% | 93.8% | 71.6% [69.1%, 73.9%] | 98.6% |
| 2026 | V3 | 99.9% | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) |
| 2026 | V4 | 98.6% | 23.6% | 74.9% [69.0%, 80.5%] | 100.0% |

The V2/Sushi check below conditions instead on pools below the floor at +300. It reports first funding, the weighted median first quote, and reserve direction; unknown first states remain visible rather than being guessed.

| Year | Sampled below-floor V2/Sushi pools | First quote known | Initially ≥0.01 ETH | Initially ≥1 ETH | Weighted median first quote (known) | Both reserves fell |
| --- | --- | --- | --- | --- | --- | --- |
| 2020 | 579 | 100.0% | 93.6% | 82.3% | 15 ETH | 92.7% |
| 2021 | 686 | 100.0% | 95.4% | 88.4% | 4 ETH | 91.6% |
| 2022 | 1,095 | 100.0% | 99.3% | 88.3% | 1.55 ETH | 89.0% |
| 2023 | 1,283 | 100.0% | 98.6% | 88.8% | 1 ETH | 90.2% |
| 2024 | 1,052 | 100.0% | 97.8% | 82.1% | 1 ETH | 89.4% |
| 2025 | 2,057 | 100.0% | 91.1% | 82.2% | 1 ETH | 90.7% |
| 2026 | 1,158 | 100.0% | 92.2% | 64.7% | 1 ETH | 91.8% |

Table 10c. Descriptive combined outcomes. “Harmful or below floor at +300” uses all pooled tokens; “harmful or emptied within the hour” conditions on initially funded pools and counts sale/tax harm or subsequent below-floor liquidity. Neither is an attributed rug/scam rate. Missing-outcome upper bounds are identification bounds, not sampling intervals.

| Year | Venue | Harm or below floor / pooled [approx. 95%] | Unknown-state upper | Harmful or emptied / initially funded [approx. 95%] | Estimated initially funded then depleted [approx. 95%] |
| --- | --- | --- | --- | --- | --- |
| 2017 | All | Not reported (effective support <30) | Not reported | NA | NA |
| 2018 | All | Not reported (effective support <30) | Not reported | Not reported (effective support <30) | Not reported (effective support <30) |
| 2018 | V1/V2 | Not reported (effective support <30) | Not reported | Not reported (effective support <30) | Not reported (effective support <30) |
| 2019 | All | 14.4% [12.9%, 16.1%] | 83.7% | 18.2% [14.7%, 22.2%] | 72 [58, 88] |
| 2019 | V1/V2 | 43.9% [40.0%, 47.9%] | 50.2% | 18.2% [14.7%, 22.2%] | 72 [58, 88] |
| 2020 | All | 46.9% [44.5%, 49.4%] | 54.7% | 48.8% [46.2%, 51.4%] | 6,166 [5,679, 6,683] |
| 2020 | V1/V2 | 49.6% [47.1%, 52.2%] | 52.1% | 48.8% [46.2%, 51.4%] | 6,166 [5,679, 6,683] |
| 2021 | All | 63.0% [61.1%, 64.9%] | 70.4% | 65.5% [63.5%, 67.4%] | 8,594 [7,983, 9,238] |
| 2021 | V1/V2 | 68.1% [66.2%, 70.0%] | 70.4% | 68.1% [66.1%, 70.0%] | 8,451 [7,848, 9,083] |
| 2021 | V3 | Not reported (effective support <30) | Not reported | Not reported (effective support <30) | Not reported (effective support <30) |
| 2022 | All | 61.1% [59.1%, 63.0%] | 71.1% | 64.6% [62.6%, 66.6%] | 30,161 [28,757, 31,699] |
| 2022 | V1/V2 | 65.2% [63.2%, 67.2%] | 69.6% | 65.2% [63.1%, 67.2%] | 29,886 [28,490, 31,416] |
| 2022 | V3 | Not reported (effective support <30) | Not reported | Not reported (effective support <30) | Not reported (effective support <30) |
| 2023 | All | 55.1% [53.2%, 57.0%] | 62.6% | 57.0% [55.1%, 58.9%] | 67,279 [64,186, 70,457] |
| 2023 | V1/V2 | 57.0% [55.1%, 58.9%] | 60.9% | 56.9% [54.9%, 58.8%] | 64,916 [61,665, 68,026] |
| 2023 | V3 | 65.3% [54.8%, 74.5%] | 69.1% | 63.1% [50.8%, 74.0%] | 2,363 [1,602, 3,169] |
| 2024 | All | 53.1% [51.3%, 55.0%] | 61.2% | 55.4% [53.5%, 57.3%] | 40,041 [38,008, 42,020] |
| 2024 | V1/V2 | 55.6% [53.6%, 57.5%] | 58.8% | 55.2% [53.2%, 57.1%] | 37,359 [35,411, 39,166] |
| 2024 | V3 | 57.0% [49.6%, 64.1%] | 67.8% | 59.5% [50.8%, 67.6%] | 2,682 [2,081, 3,377] |
| 2024 | V4 | Not reported (effective support <30) | Not reported | Not reported (effective support <30) | Not reported (effective support <30) |
| 2025 | All | 58.7% [57.2%, 60.1%] | 64.3% | 60.2% [58.6%, 61.8%] | 41,656 [40,161, 43,170] |
| 2025 | V1/V2 | 62.9% [61.4%, 64.4%] | 64.6% | 61.2% [59.6%, 62.8%] | 40,676 [39,137, 42,145] |
| 2025 | V3 | 51.3% [47.7%, 55.3%] | 53.8% | Not reported (effective support <30) | Not reported (effective support <30) |
| 2025 | V4 | 65.5% [58.5%, 71.9%] | 80.3% | Not reported (effective support <30) | Not reported (effective support <30) |
| 2026 | All | 74.5% [73.1%, 75.9%] | 82.4% | 74.0% [71.9%, 76.2%] | 35,611 [33,907, 37,270] |
| 2026 | V1/V2 | 75.5% [73.2%, 77.5%] | 76.3% | 74.2% [71.8%, 76.5%] | 29,931 [28,276, 31,409] |
| 2026 | V3 | 90.8% [88.9%, 92.8%] | 92.5% | Not reported (effective support <30) | Not reported (effective support <30) |
| 2026 | V4 | 72.4% [69.6%, 75.2%] | 81.9% | 75.2% [69.3%, 80.7%] | 5,301 [4,492, 6,212] |

Figure 1 places the annual trade rates beside the venue weights.

![Figure 1. Ethereum creation-cohort trade harm at first liquidity +300 blocks, requiring ≥0.01 ETH-equivalent quote liquidity. The shaded band is the all-venue approximate sampling interval. The upper panel draws sufficiently supported rates; the lower panel shows measurable-domain venue weights with a separate legend. Undefined venue-year cells are gaps; rates with effective support below 30 are withheld, including the fixed-mix series when its weakest venue is below 30. Open markers and dashed final segments mark January–September 2026 rather than a complete year.](figures/annual_headline.png)

### Venue, launcher, and liquidity sensitivity

A change in the pooled rate is conditional on both venue and creation-method composition. It cannot be read as a uniform change in launch quality. Table 11 supplies the grouped V1/V2-plus-Sushi rate (identical to literal Uniswap V2 from 2023 onward), V3 and V4 rates with domain weights, direct versus factory/launcher creation, and support for standardization to the saved 2024 mix (**V1/V2: 95.3%, V3: 4.7%, V4: 0.0%, unknown: 0.0%**). Standardization controls only venue shares, not launcher templates, cohort age, or classifier sensitivity; missing required venue support produces NA. The 2024–2025 decline is present within V2, so changing venue shares cannot explain it alone. The 2025–2026 V2 point comparison is nearly flat, but its 2026 effective support is 29.98, below 30, so that rate is withheld. Fixed-mix point estimates and their saved intervals also have minimum constituent support below 30 in every defined year and are withheld; their input mix is still reported. V4-specific positive support remains limited.

Table 11. Within-venue and creation-method rates in the primary domain, plus fixed-venue-mix standardization.

| Year | V1/V2 + Sushi rate (mix) | V3 rate (mix) | V4 rate (mix) | Direct deploys | Factory / launcher | Fixed 2024 mix [approx. 95%] / support |
| --- | --- | --- | --- | --- | --- | --- |
| 2017 | NA (no support) | NA (no support) | NA (no support) | Not reported (effective support <30) | Not reported (effective support <30) | NA; minimum venue support 0.0 |
| 2018 | Not reported (effective support <30) (100.0% of domain) | NA (no support) | NA (no support) | Not reported (effective support <30) | Not reported (effective support <30) | NA; minimum venue support 0.0 |
| 2019 | Not reported (effective support <30) (100.0% of domain) | NA (no support) | NA (no support) | Not reported (effective support <30) | Not reported (effective support <30) | NA; minimum venue support 0.0 |
| 2020 | 27.1% (100.0% of domain) | NA (no support) | NA (no support) | 28.0% | Not reported (effective support <30) | NA; minimum venue support 0.0 |
| 2021 | 54.8% (93.4% of domain) | Not reported (effective support <30) (6.6% of domain) | NA (no support) | 52.5% | Not reported (effective support <30) | NA; minimum venue support 0.0 |
| 2022 | 36.1% (97.7% of domain) | Not reported (effective support <30) (2.3% of domain) | NA (no support) | 35.5% | Not reported (effective support <30) | NA; minimum venue support 0.0 |
| 2023 | 25.5% (97.8% of domain) | Not reported (effective support <30) (2.2% of domain) | NA (no support) | 25.2% | Not reported (effective support <30) | NA; minimum venue support 0.0 |
| 2024 | 24.2% (95.3% of domain) | Not reported (effective support <30) (4.7% of domain) | Not reported (effective support <30) (0.0% of domain) | 23.5% | Not reported (effective support <30) | Not reported (effective support <30); minimum venue support 0.0 |
| 2025 | 9.6% (74.7% of domain) | Not reported (effective support <30) (21.8% of domain) | Not reported (effective support <30) (3.5% of domain) | 9.4% | Not reported (effective support <30) | Not reported (effective support <30); minimum venue support 2.0 |
| 2026 | Not reported (effective support <30) (55.6% of domain) | Not reported (effective support <30) (9.1% of domain) | Not reported (effective support <30) (35.3% of domain) | 13.5% | Not reported (effective support <30) | Not reported (effective support <30); minimum venue support 3.7 |

Table 12 shows real-versus-virtual V4 floors, both alternative all-venue floors, tax boundaries, core fees, legacy selection, and block-tail sensitivities. The legacy +7200 result changes pool selection and liquidity anchoring as well as timing; it is not a controlled estimate of the offset effect alone. Appendix C records early-harm pools that are below the floor at a strictly later, liquidity-known state of the same pool, with effective-support diagnostics. The early label remains in the primary numerator; the transition is not itself rug attribution. High-fee V4 pools can mechanically permit exit while imposing severe round-trip costs. Their primary tax excludes core LP fees, while the fee-inclusive sensitivity combines observed tax and the pool fee on each leg. A mechanically sellable result is not a recommendation or a claim of economic viability.

| Year | Scenario | Share [approx. 95%] |
| --- | --- | --- |
| 2021 | Launch +300, real-V4 ≥0.01 ETH, tax ≥10% | 51.3% [48.9%, 53.7%] |
| 2021 | All-venue tiny floor 10⁻⁹ ETH | 49.4% [47.1%, 51.8%] |
| 2021 | All-venue ≥0.1 ETH floor | 53.2% [50.8%, 55.7%] |
| 2021 | Virtual-V4 ≥0.01 ETH floor (sensitivity) | 51.3% [48.9%, 53.7%] |
| 2021 | Tax >10% | 48.9% [46.6%, 51.3%] |
| 2021 | Tax ≥5% | 58.8% [56.5%, 61.1%] |
| 2021 | Include core LP fee | 51.3% [48.9%, 53.7%] |
| 2021 | Legacy pool +7200; selection also differs | 39.9% [37.1%, 42.9%] |
| 2021 | Observed block-tail CI inflation | 51.3% [48.9%, 53.7%] |
| 2024 | Launch +300, real-V4 ≥0.01 ETH, tax ≥10% | 23.2% [21.2%, 25.4%] |
| 2024 | All-venue tiny floor 10⁻⁹ ETH | 23.6% [21.6%, 25.6%] |
| 2024 | All-venue ≥0.1 ETH floor | 23.3% [21.2%, 25.5%] |
| 2024 | Virtual-V4 ≥0.01 ETH floor (sensitivity) | 23.2% [21.2%, 25.4%] |
| 2024 | Tax >10% | 22.9% [20.9%, 25.1%] |
| 2024 | Tax ≥5% | 26.9% [24.8%, 29.2%] |
| 2024 | Include core LP fee | 23.2% [21.2%, 25.4%] |
| 2024 | Legacy pool +7200; selection also differs | 16.6% [14.1%, 19.4%] |
| 2024 | Observed block-tail CI inflation | 23.2% [21.2%, 25.4%] |
| 2025 | Launch +300, real-V4 ≥0.01 ETH, tax ≥10% | 7.7% [6.5%, 8.9%] |
| 2025 | All-venue tiny floor 10⁻⁹ ETH | 10.2% [9.1%, 11.4%] |
| 2025 | All-venue ≥0.1 ETH floor | 8.7% [7.3%, 10.1%] |
| 2025 | Virtual-V4 ≥0.01 ETH floor (sensitivity) | 7.9% [6.6%, 9.1%] |
| 2025 | Tax >10% | 7.5% [6.3%, 8.7%] |
| 2025 | Tax ≥5% | 9.7% [8.4%, 11.1%] |
| 2025 | Include core LP fee | 7.7% [6.5%, 8.9%] |
| 2025 | Legacy pool +7200; selection also differs | 8.0% [6.2%, 9.8%] |
| 2025 | Observed block-tail CI inflation | 7.7% [6.3%, 9.1%] |
| 2026 | Launch +300, real-V4 ≥0.01 ETH, tax ≥10% | 8.7% [6.4%, 11.2%] |
| 2026 | All-venue tiny floor 10⁻⁹ ETH | 11.7% [10.3%, 13.0%] |
| 2026 | All-venue ≥0.1 ETH floor | 10.0% [7.2%, 13.1%] |
| 2026 | Virtual-V4 ≥0.01 ETH floor (sensitivity) | 6.7% [5.2%, 8.6%] |
| 2026 | Tax >10% | 8.6% [6.4%, 11.1%] |
| 2026 | Tax ≥5% | 9.3% [7.0%, 11.8%] |
| 2026 | Include core LP fee | 9.1% [6.9%, 11.8%] |
| 2026 | Legacy pool +7200; selection also differs | 12.1% [8.8%, 15.4%] |
| 2026 | Observed block-tail CI inflation | 8.7% [5.2%, 12.6%] |

Real-principal versus virtual-V4 floor results are 2025: real 7.7%, virtual 7.9%; 2026: real 8.7%, virtual 6.7%. The 2025–2026 point ordering reverses between these liquidity definitions. The real-principal definition is primary. This sensitivity and the limited 2026 within-venue support prevent interpreting 2025–2026 as a general improvement in launch outcomes.

Figure 2 compares liquidity-floor and observation-policy sensitivities.

![Figure 2. Real-liquidity-floor, virtual-V4, and observation-policy sensitivity; rates with effective support below 30 are withheld. The tiny floor is 10⁻⁹ ETH; the primary floor is 0.01 ETH; the stricter floor is 0.1 ETH, applied across supported venues. The legacy +7200 line also uses the earlier selection/anchor policy. Differences therefore describe policy sensitivity, not causal effects of waiting alone.](figures/sensitivity.png)

A methods finding explains a misleading boundary in the superseded tiny-floor series. At Ethereum block **24,106,378** (2025-12-27), Uniswap V2's factory enables protocol-fee collection. Subsequent provider burns can leave newly minted protocol-owned LP shares in addition to permanently locked minimum liquidity. The remaining quote balance can exceed the old floor despite the provider's exit. Independent parent-state calculations reproduce the fee-mint integer formula in **10** saved receipts. This is real residual liquidity crossing an arbitrary threshold, not evidence that drained pools recovered. The venue-consistent floor and earlier observation address that eligibility artifact; neither alone proves that a pool is safe.

Tiny-floor labels among residual-liquidity pools are an informative-exclusion sensitivity: the excluded pools are not a random subset of launches. Those labels are noisier near integer/route limits and are not promoted into the headline.

| Year | Sampled residual-liquidity pools | Noisy sale/tax-harm ratio [approx. 95%] |
| --- | --- | --- |
| 2024 | 163 | 26.8% [20.4%, 34.4%] |
| 2025 | 836 | 15.6% [13.0%, 18.0%] |
| 2026 | 1855 | 13.0% [11.4%, 14.7%] |

The all-venue ≥0.1 ETH floor sensitivity in Table 12 differs from the V1/V2-only ≥0.1 ETH tradable stratum. The latter's 2026 rate is withheld because effective support is below 30.

Figure 3 shows monthly variation where effective support reaches 30; other cells are gaps.

![Figure 3. Monthly Ethereum launch-state series. Rates and intervals with effective support below 30 are withheld; annual and sufficiently supported quarterly results carry the interpretation. The band is approximate sampling uncertainty and excludes classification error. Missing values are gaps, not zero observations.](figures/monthly_series.png)

### Delayed restrictions and within-day outcomes

This saved matched-analysis sensitivity retains the eligibility and labels frozen before the real-V4-floor rebuild, including virtual V4 eligibility; its aggregate is not a same-domain update of the headline. Matching the same selected pool at first liquidity +300 and exactly +7,200 blocks (approximately one day) tests whether harm simply activates later. Initially normal means sellable, low-tax states labelled normal or owner-controlled. Within-day harm counts launch harm or subsequent above-floor harm among initially normal pools, retaining late depleted pools in the launch-measurable denominator. In this matched subset, census-weighted launch-time → within-day harm is **19.7% → 20.2%** in 2024, **6.6% → 7.0%** in 2025, and **6.3% → 7.7%** in January–September 2026. The decline from 2024 persists; the small later year-to-year point decline does not. These matched estimates differ from the full annual headline.

The surviving initially normal V1/V2 sample contains 7/438 delayed-harm observations in 2024, 7/303 in 2025, and 19/110 in 2026. Weighted rates and intervals are withheld because positive effective support is below 30 (2026: 19 positive / 94 denominator Kish observations). The counts indicate a delayed-restriction signal that needs a larger sample. Separately, **53.1%, 72.2%, and 69.1%** of initially normal V1/V2 weight with known late liquidity is below the floor in 2024–2026. This is neither a trade-failure label nor attributed liquidity withdrawal. Exact followup covers 82.8%, 92.1%, and 93.7% of initially normal aggregate weight in 2024–2026; matching is selective, and intervals exclude matching-selection and classification error.

Purposive manual review of **24** transitions finds confiscatory fee changes, external restrictions, and code installed at previously empty fee recipients, including **9 EIP-7702 delegations**. These are observed mechanisms, not population mechanism frequencies or intent attribution. Fresh-buyer simulations at two states do not establish an earlier holder's overnight experience, and restrictions beyond the followup remain unobserved. A launch-time label is not a safety grade.

### Liquidity withdrawal remains a different outcome

The separate historical liquidity audit reconstructs LP principal withdrawals, swaps, fee collection, and possible migrations. It requires at least 80% of peak quote liquidity to be withdrawn within 30 days for the withdrawal outcome, and a creator/deployer link for the narrower insider-linked outcome. A falling reserve or price alone is insufficient. In its 60-case enriched blind sample, the final reconstruction finds 21 insider-linked removals, 5 selling-driven declines, 8 surviving-liquidity cases, and 26 undetermined cases. These are sample verdicts, not annual prevalence estimates.

The old liquidity screen and second-pass sample were conditioned on a different, later trade domain and miss some pools already drained before measurement. We do not transport their conditional annual ranges to the new early-liquidity domain or add them to the trade headline. A token can fail to exit early and later lose liquidity; the outcomes overlap. The absence of a population rug point estimate is a coverage limitation, not evidence that rug pulls are rare. The first-liquidity removal descriptions above answer a different, shorter-horizon question and do not inherit an insider attribution from this audit. Below-floor and unresolved outcomes remain prominent for that reason.

## Validation and independent checks

### Current-state provider consistency

The fresh comparison re-queries 770 existing chain/address rows; the original preparation supplied 628 simulator pairs. The main Ethereum frame contains 640 tokens, with task-specific paired-complete denominators below. It runs the revised classifier with the uniform floor. The main validation scope is **current-state Ethereum V2/V3 only**. The launch-state series additionally marks known inconsistent buy replays unknown; this provider comparison does not independently validate that historical diagnostic. Provider-internal execution blocks and caches are not disclosed; observed RPC heads must not be presented as provider block pins. Fresh provider/simulator batches satisfy the saved alignment envelope, while earlier observations include multi-hour lags. Provider documentation also defines different fields, tax coverage, and failure modes [@honeypot_api; @goplus_api].

Table 13. Fresh aligned comparisons under the saved post-fix liquidity and tax rules. Matrix order is `[[both negative, first negative/second positive], [first positive/second negative, both positive]]`. Positive and negative *specific agreement* are symmetric overlap measures, not directional sensitivity or specificity. PABAK is twice raw agreement minus one. κ intervals are bootstrap intervals; raw agreement intervals use Wilson's method. All condition on observed labels and exclude classification error.

| Comparison | Task | n | [[00,01],[10,11]] | Agreement [Wilson 95%] | κ [bootstrap 95%] | Positive specific | Negative specific | PABAK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Simulator / honeypot.is | honeypot | 157 | [[139,8],[0,10]] | 94.9% [90.3%, 97.4%] | 0.689 [0.464, 0.861] | 0.714 | 0.972 | 0.898 |
| Simulator / honeypot.is | high tax | 147 | [[143,0],[0,4]] | 100.0% [97.5%, 100.0%] | 1.000 [1.000, 1.000] | 1.000 | 1.000 | 1.000 |
| Simulator / honeypot.is | harm | 157 | [[135,8],[0,14]] | 94.9% [90.3%, 97.4%] | 0.751 [0.586, 0.898] | 0.778 | 0.971 | 0.898 |
| Simulator / GoPlus | honeypot | 124 | [[122,1],[1,0]] | 98.4% [94.3%, 99.6%] | -0.008 [-0.020, 0.000] | 0.000 | 0.992 | 0.968 |
| Simulator / GoPlus | high tax | 146 | [[142,0],[0,4]] | 100.0% [97.4%, 100.0%] | 1.000 [1.000, 1.000] | 1.000 | 1.000 | 1.000 |
| Simulator / GoPlus | harm | 123 | [[118,1],[0,4]] | 99.2% [95.5%, 99.9%] | 0.885 [0.494, 1.000] | 0.889 | 0.996 | 0.984 |
| honeypot.is / GoPlus | honeypot | 444 | [[416,3],[18,7]] | 95.3% [92.9%, 96.9%] | 0.380 [0.153, 0.573] | 0.400 | 0.975 | 0.905 |
| honeypot.is / GoPlus | high tax | 525 | [[501,4],[5,15]] | 98.3% [96.8%, 99.1%] | 0.760 [0.582, 0.896] | 0.769 | 0.991 | 0.966 |
| honeypot.is / GoPlus | harm | 437 | [[396,5],[13,23]] | 95.9% [93.6%, 97.4%] | 0.697 [0.552, 0.816] | 0.719 | 0.978 | 0.918 |

For simulator–GoPlus honeypot labels, **122/124** paired rows are jointly negative and **0** jointly positive. Thus 98.4% raw agreement and κ=-0.008 are consistent: high agreement is driven by negatives, while positive specific agreement is 0.000. The liquid-stratum results below also contain essentially no joint honeypot positives. All 8 simulator–honeypot.is honeypot disagreements are provider-positive/simulator-negative, with 0 in the reverse direction. The simulator is the more lenient caller; holder-test positives and simulator under-detection are both compatible with that direction, motivating the separate classification-error scenario. Perfect high-tax agreement rests on very few positives; an empirical bootstrap cannot invent unobserved disagreements, so degenerate κ intervals do not prove perfect performance.

Table 14. Honeypot agreement by original selection stratum. The original “dust”/“thin”/“liquid” names describe selection, not the revised floor at the new observation state. Structurally unavailable paired labels remain NA.

| Stratum | Comparison | n | 00/01/10/11 | Agreement | κ [95%] | Positive specific |
| --- | --- | --- | --- | --- | --- | --- |
| liquid | Simulator / honeypot.is | 66 | 60/4/0/2 | 93.9% | 0.476 [0.000, 0.849] | 0.500 |
| liquid | Simulator / GoPlus | 56 | 55/0/1/0 | 98.2% | 0.000 [0.000, 0.000] | 0.000 |
| thin | Simulator / honeypot.is | 57 | 47/3/0/7 | 94.7% | 0.794 [0.524, 1.000] | 0.824 |
| thin | Simulator / GoPlus | 38 | 38/0/0/0 | 100.0% | NA [NA, NA] | NA |
| dust | Simulator / honeypot.is | 2 | 1/0/0/1 | Not reported (n<30) | Not reported (n<30) | Not reported (n<30) |
| dust | Simulator / GoPlus | 1 | 1/0/0/0 | Not reported (n<30) | Not reported (n<30) | Not reported (n<30) |
| top | Simulator / honeypot.is | 32 | 31/1/0/0 | 96.9% | 0.000 [0.000, 0.000] | 0.000 |
| top | Simulator / GoPlus | 29 | 28/1/0/0 | Not reported (n<30) | Not reported (n<30) | Not reported (n<30) |

The candidate-frame weighting is a descriptive calibration of the original new-token liquidity strata; top-token and supplementary selections are excluded. It does not identify population agreement because the age-filtered sample and candidate frame differ, and the above-floor paired support covers only a small fraction of candidate-frame weight. Saved weighted counts, coverage, Kish support, and bootstrap intervals accompany the aggregate tables. Neither provider is ground truth; honeypot.is is itself a simulator, so agreement shows consistency rather than independent accuracy.

| Comparison | Paired n | Kish support | Weighted agreement [bootstrap 95%] | κ [bootstrap 95%] | Positive specific | Frame-weight coverage |
| --- | --- | --- | --- | --- | --- | --- |
| Simulator / honeypot.is | 93 | 92.5 | 96.9% [92.9%, 100.0%] | 0.652 [0.000, 1.000] | 0.667 | 1.7% |
| Simulator / GoPlus | 75 | 74.6 | 98.6% [95.8%, 100.0%] | 0.000 [0.000, 0.000] | 0.000 | 1.4% |
| honeypot.is / GoPlus | 233 | 156.9 | 94.7% [90.8%, 98.0%] | 0.313 [-0.012, 0.647] | 0.334 | 79.4% |

Table 15. Earlier versus fresh observations, with timing strata and the revised rules. Comparisons across time can reflect real state changes as well as alignment; only matched identities support a paired difference calculation.

| Observation / rules | Pair lag | Comparison | n | Agreement | κ |
| --- | --- | --- | --- | --- | --- |
| Earlier classifier | ≤30 min | Simulator / honeypot.is | 59 | 98.3% | 0.792 |
| Earlier classifier | ≤30 min | Simulator / GoPlus | 165 | 98.8% | 0.744 |
| Earlier classifier | >2 h | Simulator / honeypot.is | 306 | 94.8% | 0.608 |
| Earlier classifier | >2 h | Simulator / GoPlus | 260 | 96.5% | 0.386 |
| Earlier state, revised rules | ≤30 min | Simulator / honeypot.is | 2 | Not reported (n<30) | Not reported (n<30) |
| Earlier state, revised rules | ≤30 min | Simulator / GoPlus | 15 | Not reported (n<30) | Not reported (n<30) |
| Earlier state, revised rules | >2 h | Simulator / honeypot.is | 128 | 96.9% | 0.586 |
| Earlier state, revised rules | >2 h | Simulator / GoPlus | 105 | 98.1% | -0.010 |
| Fresh state, revised rules | ≤30 min | Simulator / honeypot.is | 157 | 94.9% | 0.689 |
| Fresh state, revised rules | ≤30 min | Simulator / GoPlus | 124 | 98.4% | -0.008 |
| Fresh state, revised rules | >2 h | Simulator / honeypot.is | 0 | Not reported (n<30) | Not reported (n<30) |
| Fresh state, revised rules | >2 h | Simulator / GoPlus | 0 | Not reported (n<30) | Not reported (n<30) |

Two independent adjudications of earlier disagreement scopes agree on **30/82 (36.6%)**. This low agreement supports qualitative explanations—holder-sample failures versus fresh-buyer exits, rounding, and static capabilities versus effects—rather than a headline accuracy score after adjudication.

### Blind router replays and classifier failure modes

Independent implementations construct canonical-router buys and sales without production simulator labels. Earlier V4 checks exposed a zero-output-buy bug: an EVM-successful purchase acquired nothing, and a subsequent attempt to sell nothing was incorrectly called a honeypot. The corrected rule requires positive acquired balance and a meaningful exit attempt. Agreement between the independent verifier's two routes is not a post-fix simulator accuracy result.

The seeded diagnostic replay of 50 generic transfer-from wrapper failures records failure origin separately; a wrapper string alone is not a token-level explanation. Pool, router, dependency, gas, and unresolved paths remain distinct. This selected failure audit does not identify the error rate of every honeypot label.

| Diagnostic failure origin | Sampled wrapper-revert cases |
| --- | --- |
| token | 46 |
| token dependency | 3 |
| token returned false | 1 |

The legacy V3/V4 blind sample has five confirmed selected V3 honeypot positives and no missed honeypot among 60 sampled V3 normal rows; the old-label-stratified selection excludes owner-controlled rows and does not identify venue-population recall. The legacy honeypot-label task has no V4 honeypot positive; the separate honeypot-or-high-tax task includes two V4 high-tax positives. These are different tasks. The unweighted V3 case-control recall row is omitted.

Table 16. Honeypot-label task: independent V3/V4 sample precision and NPV before restricting to the revised liquidity domain. These selected cases were tested at legacy states with adaptive amounts; they cannot validate historical launch-state classification across every era.

| Venue | Honeypot-label task: sample metric | Success / trials | Estimate | Wilson 95% |
| --- | --- | --- | --- | --- |
| V3 | precision | 5/5 | Not reported (trials <30) | Not reported (trials <30) |
| V3 | negative predictive value | 60/60 | 100.0% | [94.0%, 100.0%] |
| V4 | precision | 0/0 | Not reported (trials <30) | Not reported (trials <30) |
| V4 | negative predictive value | 30/30 | 100.0% | [88.6%, 100.0%] |

### Independent launch-state blind replay

The independent harness freezes 376 verdicts before opening predicted labels. For creation years 2024–2026, a seeded draw takes 30 per available year × venue × predicted-harm/not-harm cell, or all when fewer exist; 2024 V4 is structurally absent. The pinned predictions precede the real-liquidity rebuild. Every buy starts with 0.01 ETH at archive parent first liquidity +300; buy/approval/sale share synthetic block +301, unlike production’s later-block sell ladder. V4 real principal is reconstructed independently from all position events. Known-outcome performance excludes no-liquidity, failed-buy and unresolved states; their counts remain explicit. Sampling weights are original census weight × source prediction-cell N/replay n. Precision and NPV condition on those predicted strata; sensitivity uses the weighted confusion totals and an approximate Bonferroni-Wilson component envelope that preserves uncertainty about unseen missed positives. These intervals omit original census uncertainty, launch dependence and classification error. Owner-control capabilities, predictions outside the binary frame, alternative callers, and later exits are outside scope. Rates below 30 effective trials are withheld.

| Year | Venue | Known / real-floor sampled | TP/FP/FN/TN | Sample precision [Wilson 95%] | Sample NPV [Wilson 95%] | No liquidity / buy failed / unknown |
| --- | --- | --- | --- | --- | --- | --- |
| 2024 | V1/V2 | 59/60 | 29/0/1/29 | Not reported (trials <30) | 96.7% [83.3%, 99.4%] | 0/1/0 |
| 2024 | V3 | 32/33 | 3/0/0/29 | Not reported (trials <30) | Not reported (trials <30) | 0/1/0 |
| 2024 | V4 | 0/0 | 0/0/0/0 | NA (no trials) | NA (no trials) | 0/0/0 |
| 2025 | V1/V2 | 57/60 | 26/1/0/30 | Not reported (trials <30) | 100.0% [88.6%, 100.0%] | 0/3/0 |
| 2025 | V3 | 32/32 | 2/0/0/30 | Not reported (trials <30) | 100.0% [88.6%, 100.0%] | 0/0/0 |
| 2025 | V4 | 30/30 | 9/0/0/21 | Not reported (trials <30) | Not reported (trials <30) | 0/0/0 |
| 2026 | V1/V2 | 59/60 | 28/2/0/29 | 93.3% [78.7%, 98.2%] | Not reported (trials <30) | 0/1/0 |
| 2026 | V3 | 34/34 | 4/0/0/30 | Not reported (trials <30) | 100.0% [88.6%, 100.0%] | 0/0/0 |
| 2026 | V4 | 32/32 | 16/0/0/16 | Not reported (trials <30) | Not reported (trials <30) | 0/0/0 |
| 2024 | all | 91/93 | 32/0/1/58 | 100.0% [89.3%, 100.0%] | 98.3% [91.0%, 99.7%] | 0/2/0 |
| 2025 | all | 119/122 | 37/1/0/81 | 97.4% [86.5%, 99.5%] | 100.0% [95.5%, 100.0%] | 0/3/0 |
| 2026 | all | 125/126 | 48/2/0/75 | 96.0% [86.5%, 98.9%] | 100.0% [95.1%, 100.0%] | 0/1/0 |
| all | all | 335/341 | 117/3/1/214 | 97.5% [92.9%, 99.1%] | 99.5% [97.4%, 99.9%] | 0/6/0 |

| Year | Venue | Weighted precision [approx. 95%] | Weighted NPV [approx. 95%] | Weighted sensitivity [approx. 95% envelope] | Kish predicted harm / not-harm |
| --- | --- | --- | --- | --- | --- |
| 2024 | V1/V2 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | 26.4/27.4 |
| 2024 | V3 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | 3.0/25.5 |
| 2024 | V4 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | 0.0/0.0 |
| 2025 | V1/V2 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | 24.4/26.9 |
| 2025 | V3 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | 2.0/28.7 |
| 2025 | V4 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | 8.1/19.0 |
| 2026 | V1/V2 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | 25.5/24.5 |
| 2026 | V3 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | 3.7/28.9 |
| 2026 | V4 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) | 15.0/14.1 |
| 2024 | all | Not reported (effective support <30) | 97.3% [84.4%, 99.6%] | Not reported (effective support <30) | 26.7/30.2 |
| 2025 | all | Not reported (effective support <30) | 100.0% [92.4%, 100.0%] | Not reported (effective support <30) | 28.0/46.5 |
| 2026 | all | 95.6% [85.0%, 98.8%] | 100.0% [92.0%, 100.0%] | 100.0% [19.1%, 100.0%] | 43.7/43.9 |
| all | all | 99.1% [90.9%, 99.9%] | 98.9% [94.4%, 99.8%] | 94.6% [34.6%, 99.6%] | 46.7/102.6 |

Across the replayed real-floor domain, 335/341 states have binary outcomes, with TP/FP/FN/TN = 117/3/1/214. Pooled unweighted precision and NPV describe the balanced verification sample, not the launch population. The tax-boundary sensitivity allows one base unit of rounding per measured side: its TP/FP/FN/TN is 120/0/1/214. The strict false positives in this real-floor comparison disappear under nominal rounding; they reflect the 10% boundary rather than tax-free trading. The missed V2 case is a same-block sale restriction after a positive buy; later-block exits were not independently tested. Zero observed misses in other sampled normal strata do not establish 100% population recall. The all-sample and virtual-floor summaries remain saved diagnostics; the paper uses the real-floor domain.

For harmonized scoring, a verifier sale with token tax ≥10% becomes high tax rather than unrestricted sellability; core LP fees remain separate. Appendix B names the **honeypot-or-high-tax** task and reports sample-conditional metrics after the real-liquidity floor and state deduplication. Precision and NPV condition on sampled predicted labels. Unweighted case-control recall is not population sensitivity. The new replay weights sampling cells for sensitivity and retains uncertainty about unobserved misses. Unsupported strata remain undefined. Appendix D retains the strict and conditional classification envelopes; the main text reports coverage and the pooled-assumption scenario.

## Limitations, evidence access, and ethics

The snapshot is ranked, includes correlated deployments, and has unequal chain depth. Its intervals are descriptive, and market-cap values do not measure local chain float. Securities and bridge tags are incomplete. The behavior battery's golden expectations match **107/114 (93.9%)**; the remaining mismatches include changing token implementations, source-label errors, and harness limitations. This is a selected regression set, not population accuracy. Fresh derived holders and calibrated storage overrides cannot exercise every address-specific permission, checkpoint, or economic state. Non-Ethereum historical drift often cannot be queried through the available endpoints.

The Ethereum deployment series misses some non-clone proxy tokens and late first-pool strata. Tokens outside supported factories/quotes, a later-only pool, unavailable historical state, and unmeasured quote conversions remain unknown. Pool quote balances can overstate in-range liquidity, while one-sided concentrated liquidity can allow a round trip despite a low pre-buy quote balance. The V4 headline uses reconstructed real principal; its virtual-amount sensitivity is especially far from an execution-depth equivalence. A single test can miss delayed restrictions, privileged exemptions, amount-dependent taxes, transfer-only behaviors, or a later rug pull.

The bootstrap and census-count propagation do not establish nominal coverage under launch bursts. In the saved finite-population stress exercise, rare large clusters cause severe undercoverage in both domains; these synthetic rates do not estimate coverage on Ethereum.

| Synthetic scenario | Domain | First-stage replicates | Bootstrap draws | Empirical coverage |
| --- | --- | --- | --- | --- |
| ordinary clusters | all | 200 | 300 | 93.0% |
| ordinary clusters | measurable | 200 | 300 | 93.0% |
| rare bursty clusters | all | 200 | 300 | 63.5% |
| rare bursty clusters | measurable | 200 | 300 | 63.5% |

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

| Quarter | Measurable share [approx. 95%] | Effective positive / denominator n |
| --- | --- | --- |
| 2020Q3 | 21.4% [17.7%, 25.7%] | 76 / 397 |
| 2020Q4 | 33.5% [29.3%, 37.9%] | 147 / 448 |
| 2021Q1 | 33.5% [30.1%, 37.1%] | 186 / 561 |
| 2021Q2 | 47.7% [43.1%, 52.3%] | 189 / 412 |
| 2021Q3 | 51.2% [46.6%, 55.8%] | 209 / 410 |
| 2021Q4 | 64.4% [60.4%, 68.2%] | 341 / 529 |
| 2022Q1 | 74.2% [70.2%, 77.9%] | 322 / 440 |
| 2022Q2 | 44.2% [39.8%, 48.8%] | 190 / 436 |
| 2022Q3 | 25.8% [21.6%, 30.4%] | 94 / 362 |
| 2022Q4 | 25.1% [20.8%, 29.9%] | 85 / 338 |
| 2023Q1 | 34.5% [30.4%, 38.9%] | 158 / 471 |
| 2023Q2 | 22.6% [18.8%, 26.9%] | 92 / 399 |
| 2023Q3 | 25.1% [21.2%, 29.5%] | 103 / 404 |
| 2023Q4 | 22.6% [18.8%, 27.0%] | 90 / 396 |
| 2024Q1 | 22.5% [18.9%, 26.4%] | 103 / 461 |
| 2024Q2 | 21.9% [18.3%, 25.9%] | 96 / 431 |
| 2024Q3 | 25.6% [21.4%, 30.3%] | 91 / 355 |
| 2024Q4 | 22.3% [18.4%, 26.8%] | 78 / 378 |
| 2025Q1 | 9.4% [7.1%, 11.6%] | 51 / 543 |
| 2025Q2 | 8.5% [6.2%, 11.0%] | 44 / 503 |

Convenience extras do not enter the main prevalence tables or rank-matched tests.

| Behavior / capability | Extras only: yes / measured (rate) |
| --- | --- |
| Missing transfer return | 1/539 (0.2%) |
| Observed transfer fee | 0/518 (0.0%) |
| Share capability | 13/539 (2.4%) |
| Observed drift | 0/138 (0.0%) |
| Blocklist union | 22/542 (4.1%) |
| EOA upgrade path | 12/535 (2.2%) |

The following diagnostic preserves sample counts from the superseded later-state series. It uses the old tiny floor and selection policy, whose floor coverage differed by venue, and is not the early-liquidity headline or a causal attribution of rug pulls. Rates are withheld because this saved table lacks effective-support diagnostics. The 2026 eligibility discontinuity is explained by protocol-fee residuals above.

| Year | Old tiny-floor measurable sample | Old sale/tax-positive sample |
| --- | --- | --- |
| 2017 | 229 | 2 |
| 2018 | 603 | 6 |
| 2019 | 828 | 12 |
| 2020 | 1270 | 70 |
| 2021 | 1434 | 565 |
| 2022 | 1292 | 562 |
| 2023 | 1401 | 286 |
| 2024 | 1145 | 250 |
| 2025 | 1730 | 354 |
| 2026 | 2746 | 312 |

## Appendix B: era × venue blind support

The task here is honeypot **or high tax ≥10%**, unlike the honeypot-only task in Table 16. These are sample-conditional harm metrics pooling jointly above-floor, decidable legacy and +300 checks after state deduplication. Unknown/outside-floor counts remain visible. They do not inherit the probability-weighted scope of the separately reported +300 replay. A selected normal stratum does not by itself identify sensitivity among all true positives; no-positive cells have undefined precision or recall.

| Era | Venue | Honeypot-or-high-tax task: sample metric | Success / trials | Estimate [Wilson 95%] | Outside floor / unknown |
| --- | --- | --- | --- | --- | --- |
| 2017-19 | V1/V2 | precision | 0/0 | Not reported (trials <30) | 2 |
| 2017-19 | V1/V2 | sample recall (unweighted) | 0/0 | Not reported (trials <30) | 2 |
| 2017-19 | V1/V2 | negative predictive value | 3/3 | Not reported (trials <30) | 2 |
| 2017-19 | V1/V2 | sample specificity (unweighted) | 3/3 | Not reported (trials <30) | 2 |
| 2017-19 | V4 | precision | 0/0 | Not reported (trials <30) | 6 |
| 2017-19 | V4 | sample recall (unweighted) | 0/0 | Not reported (trials <30) | 6 |
| 2017-19 | V4 | negative predictive value | 0/0 | Not reported (trials <30) | 6 |
| 2017-19 | V4 | sample specificity (unweighted) | 0/0 | Not reported (trials <30) | 6 |
| 2020-22 | V1/V2 | precision | 7/7 | Not reported (trials <30) | 17 |
| 2020-22 | V1/V2 | sample recall (unweighted) | 7/7 | Not reported (trials <30) | 17 |
| 2020-22 | V1/V2 | negative predictive value | 1/1 | Not reported (trials <30) | 17 |
| 2020-22 | V1/V2 | sample specificity (unweighted) | 1/1 | Not reported (trials <30) | 17 |
| 2020-22 | V4 | precision | 0/0 | Not reported (trials <30) | 2 |
| 2020-22 | V4 | sample recall (unweighted) | 0/0 | Not reported (trials <30) | 2 |
| 2020-22 | V4 | negative predictive value | 0/0 | Not reported (trials <30) | 2 |
| 2020-22 | V4 | sample specificity (unweighted) | 0/0 | Not reported (trials <30) | 2 |
| 2023-24 | V1/V2 | precision | 31/32 | 96.9% [84.3%, 99.4%] | 16 |
| 2023-24 | V1/V2 | sample recall (unweighted) | 31/32 | 96.9% [84.3%, 99.4%] | 16 |
| 2023-24 | V1/V2 | negative predictive value | 30/31 | 96.8% [83.8%, 99.4%] | 16 |
| 2023-24 | V1/V2 | sample specificity (unweighted) | 30/31 | 96.8% [83.8%, 99.4%] | 16 |
| 2023-24 | V3 | precision | 3/3 | Not reported (trials <30) | 1 |
| 2023-24 | V3 | sample recall (unweighted) | 3/3 | Not reported (trials <30) | 1 |
| 2023-24 | V3 | negative predictive value | 29/29 | Not reported (trials <30) | 1 |
| 2023-24 | V3 | sample specificity (unweighted) | 29/29 | Not reported (trials <30) | 1 |
| 2025-26 | V1/V2 | precision | 55/58 | 94.8% [85.9%, 98.2%] | 17 |
| 2025-26 | V1/V2 | sample recall (unweighted) | 55/55 | 100.0% [93.5%, 100.0%] | 17 |
| 2025-26 | V1/V2 | negative predictive value | 59/59 | 100.0% [93.9%, 100.0%] | 17 |
| 2025-26 | V1/V2 | sample specificity (unweighted) | 59/62 | 95.2% [86.7%, 98.3%] | 17 |
| 2025-26 | V3 | precision | 10/10 | Not reported (trials <30) | 40 |
| 2025-26 | V3 | sample recall (unweighted) | 10/10 | Not reported (trials <30) | 40 |
| 2025-26 | V3 | negative predictive value | 83/83 | 100.0% [95.6%, 100.0%] | 40 |
| 2025-26 | V3 | sample specificity (unweighted) | 83/83 | 100.0% [95.6%, 100.0%] | 40 |
| 2025-26 | V4 | precision | 26/26 | Not reported (trials <30) | 94 |
| 2025-26 | V4 | sample recall (unweighted) | 26/26 | Not reported (trials <30) | 94 |
| 2025-26 | V4 | negative predictive value | 48/48 | 100.0% [92.6%, 100.0%] | 94 |
| 2025-26 | V4 | sample specificity (unweighted) | 48/48 | 100.0% [92.6%, 100.0%] | 94 |

## Appendix C: matched-pool timing transitions

Only the same selected pool with a strictly later, liquidity-known legacy state enters this diagnostic. Shares are descriptive weighted transitions within the observed subset; they do not identify population withdrawal rates or isolate the effect of waiting alone.

| Year | Comparable same-pool observations | Early-harm observations | Later below floor / early harm | Effective transition support | Early-harm weight later below floor |
| --- | --- | --- | --- | --- | --- |
| 2017 | 0 | 0 | 0/0 | 0.0 | Not reported (effective support <30) |
| 2018 | 19 | 0 | 0/0 | 0.0 | Not reported (effective support <30) |
| 2019 | 500 | 0 | 0/0 | 0.0 | Not reported (effective support <30) |
| 2020 | 2211 | 252 | 200/252 | 177.9 | 80.1% |
| 2021 | 2792 | 1019 | 508/1019 | 370.6 | 53.0% |
| 2022 | 2744 | 732 | 317/732 | 226.6 | 48.4% |
| 2023 | 3046 | 464 | 235/464 | 194.0 | 52.1% |
| 2024 | 2811 | 379 | 239/379 | 216.1 | 63.3% |
| 2025 | 4323 | 143 | 81/143 | 72.0 | 55.4% |
| 2026 | 3185 | 54 | 27/54 | 24.3 | Not reported (effective support <30) |

## Appendix D: effective support and classification envelopes

The following table preserves the effective positive/denominator sizes and floor diagnostics. Rates and intervals below effective support 30 are withheld. The conditional envelope includes unrestricted error in unsupported cells; it is not a 95% interval and must not be substituted for the pooled-assumption scenario. Without transport restrictions the strict classification range is 0–100%.

| Year | Effective positive / denominator n | Tiny floor 10⁻⁹ ETH | All-venue ≥0.1 ETH floor | Conditional classification envelope |
| --- | --- | --- | --- | --- |
| 2017 | 0 / 0 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) |
| 2018 | 0 / 15 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) |
| 2019 | 1 / 339 | Not reported (effective support <30) | Not reported (effective support <30) | Not reported (effective support <30) |
| 2020 | 221 / 930 | 25.6% | 29.4% | 0.0%–100.0% |
| 2021 | 773 / 1543 | 49.4% | 53.2% | 0.0%–100.0% |
| 2022 | 519 / 1227 | 35.6% | 35.9% | 0.0%–100.0% |
| 2023 | 390 / 1491 | 25.2% | 25.7% | 19.3%–49.7% |
| 2024 | 350 / 1542 | 23.6% | 23.3% | 17.9%–47.4% |
| 2025 | 129 / 1615 | 10.2% | 8.7% | 6.2%–20.3% |
| 2026 | 48 / 674 | 11.7% | 10.0% | 7.0%–26.1% |

## References
