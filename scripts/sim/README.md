# scripts/sim: buy/sell simulator (Tier-2 engine, workstream W5)

Given `(token, DEX pool, block)`: simulate a buy and then sells **from a plain EOA through the canonical router**, measure
taxes / reverts / transfer behaviour, add owner-control and liquidity-pull signals, and classify. Read-only: only
`eth_simulateV1`, `eth_call`, `eth_getBalance` (+ Etherscan `getLogs`); nothing is ever broadcast.

## Run

```
.venv/bin/python scripts/sim/run.py --pairs pairs.csv --mode historical --offset-blocks 7200 --out data/sim/<run>/
.venv/bin/python scripts/sim/run.py --pairs pairs.csv --mode latest     --out data/sim/<run>/ [--rug on]
.venv/bin/python scripts/sim/run.py ... --backend anvil     # local anvil fork, real mined txs (slow; cross-check subset)
.venv/bin/python scripts/sim/run.py ... --dust-floor-wei 1000000000  # default; 0 disables
.venv/bin/python scripts/sim/golden_eval.py                  # golden set: run + compare -> data/sim-golden/observed.csv
.venv/bin/python scripts/sim/recent_pairs.py --days 30 --n 36 --out data/sim-golden/recent_pairs.csv   # smoke-test sample
```

`pairs` = CSV (with header) or JSONL: `token, pool [, dex, created_block, block, ...extra]`. Extra columns are copied to
`extra` in the output. `dex` = `uniswap_v2 | sushiswap | uniswap_v3 | uniswap_v1 | uniswap_v4` (optional; auto-detected from
`pool.factory()/fee()/tokenAddress()` and re-planned if the guess was wrong). Historical block = `created_block +
offset-blocks` (7200 ~ 24h) unless a `block` column is given; latest = pinned head - 3 (recorded in `run_meta.json`).
Resumable: `results.jsonl` is append-only and finished `(token, pool, block)` are skipped on restart. `--shard i/n`
splits a pairs file across processes. Endpoints: `--archive-url` (default eth.drpc.org: batch 3, 4 req/s) and
`--fresh-url` (publicnode, batch 8, 3 req/s; used first in latest mode, no archive). Etherscan 2 req/s, cached.

Output per pair (`results.jsonl`): `token, pool, dex, block, mode, buy{}, transfer{}, approve{}, sells[], taxes{},
meta{decimals,symbol,owner,...}, control{}, rug{screen,detail,verdict}, flags[], primary_label, label_reason,
unprobeable, evidence{raw,sha256}, rules`. Raw evidence: `data/raw/sim/<run>/xx/<token>_<pool>_<block>.json.gz` = list of
every JSON-RPC request + response (and Etherscan request without key + response) with `retrieved_at`; `sha256` is of the
uncompressed JSON. (Raw dirs are gitignored.)

## What is simulated (eth_simulateV1, same block, state carried across calls and simulated blocks)

Phase 1 (probe + buy; 1 request): pool/token metadata (`factory, token0/1, getReserves, fee, liquidity, tokenAddress`,
token `decimals/symbol/name/totalSupply/owner/getOwner/balanceOf(dead)`, runtime bytecode via an injected helper),
router quote, then the **buy** of 0.01 ETH by EOA `BUYER` (fresh derived address, balance override 100 ETH). If the buy
reverts, retry once with 0.0005 ETH (max-tx / max-wallet caps), unless the quote says "no liquidity".
Phase 2 (full; 1 request, exact amounts from phase 1; replay consistency is checked):
1. block B+1: buy; **transfer 10%** to a second EOA (`xfer`: ok? returns false? tax = 1 - received/sent, sender over-debit); `approve(max)`.
2. block B+2 (next block): **sell ladder** 40%, 5%, 0.5% of received tokens (each: quote -> sell -> ETH delta).
3. block B+27 (+25 blocks ~ 5 min, via block-number override with auto-filled gap blocks): sell ladder 20%, 1% ("delayed" sells).
Routes: V2 `swapExactETHForTokensSupportingFeeOnTransferTokens` / `swapExactTokensForETHSupportingFeeOnTransferTokens`
via Router02 (Uniswap) or Sushi router (quote via `getAmountsOut`; non-WETH quote pools are routed WETH->quote->token);
V3 `SwapRouter.exactInputSingle` (value = ETH, WETH out) with `Quoter` expectation; V1 exchange `ethToTokenSwapInput /
tokenToEthSwapInput` with `getEthToTokenInputPrice` expectation. Taxes: `tax = max(0, 1 - received/expected)` where
expected = router/quoter output computed immediately before the swap, so price impact cancels and only token-side
leakage remains. Anything below 0.5% is treated as 0 (rounding / swap-back noise).

## Labels (flags overlap; primary label by precedence)

| primary (in order) | rule |
|---|---|
| `unprobeable` | no meaningful buy/sell evidence, a zero-output buy, no positive sell quote, or a known native ETH/WETH quote reserve below the configured dust floor; other reasons include `no_pool_at_block`, `no_liquidity`, `buy_reverts:<reason>`, `router_not_deployed_at_block`, `unsupported_factory:<addr>`, and RPC/simulation errors |
| `honeypot` | buy acquires >0 tokens, a positive-quote sell is attempted, and every such sell reverts for a token-side reason or sell/buy tax >=95% |
| `rugged` | rug verdict `rug_lp_pull` / `rug_lp_pull_unattributed` (see below), also when the sim itself was impossible because the LP is already gone |
| `high_tax` | max(buy tax, sell tax) >= 10%, or transfer tax >= 10% |
| `owner_controlled` | owner() live (EOA or contract, not 0x0/0xdead) or owner() unreadable, AND the runtime bytecode dispatcher contains a risky admin selector (mint, tax/fee setters, blacklist/bots, max-tx/wallet limits, trading switch, pause, pair setter, upgrade/proxy admin) |
| `normal` | none of the above |

Useful flags: `sell_delay_gate` (first sell reverts, +25-block sell works), `sell_size_limit`, `transfer_blocked`,
`transfer_tax_high`, `transfer_returns_false`, `transfer_overdebit`, `buy_tax_high`, `sell_tax_high`, `extreme_tax` (>=50%),
`v3_fot_incompatible` (V3 pools cannot sell fee-on-transfer tokens: pool balance check fails), `died_by_sells`,
`lp_pulled_late`, `admin_fn_without_owner`, `proxy_like`, `fn:<category>`, `owner:<state>`, `gas_heavy`.
Thresholds are constants in `classify.py` (`RULES`) and are copied into every output line. The 10% line for `high_tax`:
plain legit fee-on-transfer designs (RFI-style reflection 1-2%, FLOKI/CULT 0.3-3%) are far below it; the retained golden cases are in `data/sim-golden/expected.csv`; this public subset does not reproduce the full historical evaluation.

The default dust floor is 1e9 wei of a saved native ETH/WETH quote reserve at the simulation block (currently V2). The CLI accepts
`--dust-floor-wei 0` to disable it and `--latest-block N` for pinned regression runs. V3/V4 do not currently provide
a comparable per-pool reserve in output, so this reserve floor does not infer one from liquidity or singleton balances.
V4 sells settle through the canonical Universal Router V4_SWAP/Permit2 path. V4 token/hook tax uses the user-initiated
PoolManager Swap event as the core baseline, excluding core LP fees; `core_swap` and wallet deltas are retained.

## Rug vs died (rug.py)
Level 1 screen (1 batched archive request): quote-side liquidity at created+1 block, created+50400 (~7d), head, plus
the sim block. peak = max of those; `ratio_final_peak = final/peak`. Level 2 (only if ratio <= 0.15; Etherscan getLogs of
the pair: Mint, Burn, LP-token Transfer; V3: Burn; V1: Add/RemoveLiquidity): insider = first LP-token recipient plus
addresses it forwarded LP tokens to; each Burn is attributed through the LP Transfer into the pair in the same tx.
* `rug_lp_pull`: ratio <= 0.1 AND insiders removed >= 50% of peak quote liquidity AND that happened within 30 days of pool creation
* `lp_pulled_late`: same but later than 30 days (legit migrations look like this; flag only)
* `died_by_sells`: ratio <= 0.1 and insiders removed < 20% of peak (price collapsed through swaps: quote reserve fell without LP pulls)
* `partial_rug_lp_pull`, `liquidity_declined_mixed`, `liquidity_declined_screen_only` otherwise; V3 has no attribution (`rug_lp_pull_unattributed`).
Limits: "insider dumping" (deployer sells their tokens into the pool) is indistinguishable from organic death here;
V3 positions are held by the NonfungiblePositionManager so owners are unknown; Etherscan pages are capped at 2x1000 logs per topic
(`truncated` is recorded); the Etherscan end block is rounded down to a multiple of 2000 for caching.

## Fidelity notes: eth_simulateV1 (drpc) vs eth_call harness vs anvil
* **eth_simulateV1 on eth.drpc.org** works at every historical block tested (blocks 1M .. head), multi-block with state
  carried over, state overrides (balance, code), gap-filling blocks. Quirks handled: pre-London blocks reject default
  type-2 calls -> every call carries `gasPrice: 0`; historical block gas limits (8-10M) are exhausted by INVALID-opcode
  calls -> block gas limit overridden to 200M and non-swap calls get a 400k gas cap; call to a not-yet-deployed contract
  "succeeds" with empty data (detected: `router_not_deployed_at_block`, `no_pool_at_block`).
* Caveats of simulateV1: `gasPrice = 0` and base fee 0 (tokens that gate on `tx.gasprice` / base fee are not exercised);
  `tx.origin = msg.sender = EOA` (good: many honeypots key on origin/extcodesize); block timestamps advance by 12 s per
  block from the historical parent; `block.coinbase/prevrandao` are those of the base header; the EVM rules are those of
  the historical block (correct for the era). Contracts that behave differently when `block.timestamp` is far after
  launch are tested at B, not at "launch + N" (choose B accordingly).
* **anvil fork** (`--backend anvil`): real signed-less (impersonated) txs mined one per block, real gas price, receipts;
  anvil uses the hardfork of the fork block (checked: MuirGlacier at 11.8M); gas fees are refunded to the buyer after each tx so ETH deltas measure proceeds only; every storage read is a lazy upstream request
  (100-600 requests per pair), so it is only used as an independent cross-check on a subset (see the local-fork method above).
* An in-`eth_call` harness contract (as W1 does for Tier 1) was **not** used for sells: the caller would be a contract,
  which is exactly what honeypots discriminate against. It remains the fallback if a future endpoint lacks simulateV1.

## Throughput / rate limits
drpc free plan: JSON-RPC batch <= 3, `eth_getLogs` <= ~100 blocks (so logs come from Etherscan), archive OK. A pair costs
2 simulateV1 requests (1 if the buy reverts) + 1 batched reserve-sample request (historical) + 0..3 Etherscan calls
(only declined pools). Latest mode on publicnode batches several sims per HTTP request: ~3.4 pairs/s measured.
Historical: ~1 pair/s with Etherscan skipped, ~0.3 pairs/s when most pools need the rug detail.
