# Probe harness (W1): live ERC-20 behaviour via `eth_call` state overrides

For any ERC-20 on ethereum / base / arbitrum / bsc / polygon / optimism this measures how the token **actually behaves
right now** using only `eth_call` (no transactions, no keys, no real funds) and saves the full raw JSON-RPC request and
response behind every cell.

```
.venv/bin/python scripts/probe/run.py --chain ethereum --tokens tokens.csv --block latest --out data/probe/ethereum/
.venv/bin/python scripts/probe/run.py --chain bsc --tokens 0xAddr1,0xAddr2 --block 31234567 --no-drift
.venv/bin/python scripts/probe/verify.py data/probe/ethereum --sample 500     # evidence integrity check
.venv/bin/python scripts/probe/golden.py --run                                 # golden-set regression (data/probe-golden)
```

`--tokens`: csv (column `address`/`token`/`token_address`/`contract`, else first column), jsonl (strings or objects with
those keys), a text file (any `0x…40` found), a comma list, or `-` for stdin. Options: `--concurrency 6`, `--rps 5`
(max HTTP requests per second **per endpoint**; JSON-RPC batching is used, drpc free tier allows 3 items/batch, publicnode
20 – learned automatically), `--rpc url1,url2`, `--block latest|N`, `--no-drift`, `--limit`, `--redo`, `--pin-lag`.
Output is resumable: re-run the same command and completed tokens are skipped; tokens that hit infrastructure errors are
retried (`done.jsonl` only marks `complete` when no row has status `error`).

## How it works

1. **Harness contract** (`contracts/Probe.sol`, solc 0.8.28, evm paris; runtime bytecode committed in `bytecode.json`
   with sha256). Its *runtime* code is injected at a fixed fake address via the `eth_call` state override, together with
   two tiny actor contracts (`Sink`: accepts everything, records callbacks, acts as spender; `Rejecter`: reverts on
   everything). It is a small interpreter: Python sends *scripts* (lists of call / staticcall ops, as the harness or as the
   Sink); each script runs inside an **isolated self-call that always ends in `revert Done(payload)`**, so one script's
   state changes (transfers, burns, approvals) never leak into the next. The payload holds, per op, `ok`, gas used, full
   return length and the first 320 bytes of return data. All classification is Python-side on raw bytes.
2. **Funding** (`fund.py`) – the harness needs a token balance:
   * `eth_createAccessList(balanceOf(H))` gives the storage slots balanceOf touches (works for proxies and for tokens whose
     balances live in a separate storage contract); candidates are overridden with two probe values and balanceOf(H)
     re-read; the slot→balance ratio is calibrated (secant refinement, ≤1 % error) so **share-based / gons / reflection /
     index-scaled** tokens (stETH, AMPL, SafeMoon, Comet, aTokens) get the target balance too; low 16 bits are cleared for
     scaled slots so `balance<<k | flags` layouts are not given random flags;
   * struct-style balances that need a companion slot (principal × index / snapshot, imBTC) via an adjacent-slot joint probe;
   * fallback: brute-force `keccak(h . slot)` (solidity) and `keccak(slot . h)` (vyper), slots 0..200 + OZ‑v5 ERC‑7201
     namespace, 70 keys per call then bisection;
   * fallback: native-balance override for tokens whose `balanceOf` is `address.balance` (POL on Polygon);
   * for identity-mapped slots the **bit width of the balance field** is measured (probe 2^95 … 2^192) → exposes uint96
     (UNI/COMP), uint104 (AAVE) etc. without needing >2^96 funds.
   * last resort **real holder** (`holder.py`, method `real_holder`): for tokens whose balance cannot be overridden
     (protocol-level / precompile tokens such as Base's `0xB2000…` Coinbase tokenized assets, whose `eth_getCode` is the single
     byte `0xef`; storage-less facades) a real holder is found from recent `Transfer` logs (windows of 200 / 2000 / 9000 blocks,
     free plans cap `eth_getLogs` ranges) and the whole battery runs inside one `eth_simulateV1` block: call 1 = the holder
     transfers to the harness address, call 2 = the harness `run(...)`. Nothing is sent. Slot-dependent measurements
     (balance width, slot ratio, EOA mode, 7-day drift) are not available in this mode (`balance_storage` is `unprobeable`
     with reason `real_holder_funding(slot_unknown)`). Needs a holder that transacted in the recent window.
   The method used is stored in the `funding` row. If nothing works the token is `unprobeable` with the reason (still
   gets the read-only `views` tests: decimals/name/symbol/pause/blocklist/permit/accessors).
3. **EOA mode**: three independent plain `eth_call`s `from` the same fake address *without* code (so it is an EOA) –
   transfer, 5%-of-supply transfer, zero transfer – to compare with the contract-sender result (catches tokens that
   treat contract senders differently, e.g. anti-bot `tx.origin`/`isContract` rules).
4. **Drift test** (rebasing): same raw slot value read at the pinned block and ~7 days earlier (needs an archive endpoint;
   on Ethereum eth.drpc.org). A different balance for the same slot value is direct proof of balance modification outside
   transfers.

Pinned block: `--block latest` re-pins every 60 s to `head - max(3 blocks, 6 s)` (fast L2s answer "Unknown state" for the
newest blocks) and **every row records the exact block**; `--block N` uses N for the whole run (needs archive endpoints for
old N). Reference: eth_simulateV1 is supported on all 12 endpoints (not needed – the harness subsumes it);
`debug_traceCall` only on drpc; `eth_createAccessList` works everywhere but Arbitrum/BSC-dataseed need a funded `from`
(we use a rich Binance address as `from`; it is only the tx sender, balanceOf takes the probe address as an argument).

## Fake accounts (same on every chain; derived as keccak("weird-erc20-live/<name>")[12:])
`H` harness/EOA-mode sender `0x549fa34c6e71ec9c9a3593985d8325293b925d7e`; actors `SINK`, `REJ`, recipient `R`, caller `E`
(addresses in `common.py`, also recorded in `runs.jsonl`). None has any state on the chains.

## Outputs (per `--out` dir)
* `results.jsonl` – one JSON row per (token, test), appended; `results.final.jsonl` – deduplicated (last row wins), written at the end of each run (`run.py --compact` rebuilds it).
* `raw/<token>.json.gz` – everything fetched for that token: `calls[]` with `cid, tag, url, retrieved_at, latency_ms,
  http_status, request` (full JSON-RPC request incl. state overrides), `response` (full JSON-RPC item), `response_sha256`
  (sha256 of the canonical JSON of `response`), plus `transport_failures[]` (retries / failovers) and the pinned `block`
  (number, hash, timestamp). `zcat raw/<token>.json.gz | jq .`
* `done.jsonl`, `runs.jsonl` (endpoints, block mode, harness sha256s, throughput, http request counts).

Row schema:
`chain, token, test, status, value, detail, block, script, evidence (relative path), evidence_calls ([cid]), evidence_sha256
([sha256 of each cited response]), probed_at`. Large integers are decimal strings. For harness tests `script` names the
script inside the cited battery response (the response contains all scripts; decode with `common.decode_run`).
`verify.py` re-checks every cited sha256 and that battery payloads still decode.

## Status semantics
* `pass` – baseline behaviour (or optional feature absent/present with nothing to special-case).
* `quirk` – works but deviates in a way integrators must handle (details in `value`).
* `fail` – a core operation does not do what it must from a freshly funded contract holder (normal transfer / approve /
  transferFrom reverts or lies) or something that must revert succeeds.
* `unprobeable` – not measurable, with the reason (`no_code_at_block`, `code_not_a_normal_contract`, `no_balance_slot…`,
  `balanceOf(address) reverts…`, `script_exceeds_gas_budget`, `node_execution_timeout`, `erc1820_registry_absent…`,
  `normal_transfer_reverts` for derived tests, …). Never guessed.
* `error` – infrastructure failure (RPC, decode); retried on resume.

## Tests (row `test` names)
Amounts: `a = max(10^decimals, 10^4)` capped at 1 % of totalSupply and 10 % of the funded balance; funded balance
`B = totalSupply/2` (≤ 2^90); `amount_used`, `balance`, `target_balance` are in the `funding` row.

| test | what is measured | quirk / fail means |
|---|---|---|
| `funding`, `balance_storage` | method (`access_list` / `brute_force` / `native_balance` / `real_holder_simulateV1`); whether balances live in the token's own storage | external storage = storage-contract / forwarding / double-entry token |
| `bytecode_selectors` | code hash, PUSH4 selector set of token + resolved implementation (EIP-1967, OZ-legacy, EIP-1167, beacon, `implementation()`) | info only (proxy/admin classification is W3's) |
| `decimals` | value; ≤5 or >18 / non-uint8 / reverting / missing = quirk | |
| `name_symbol` | string vs bytes32 vs missing | bytes32/missing = quirk |
| `permit` | `eip2612` / `dai_permit` / none (DOMAIN_SEPARATOR, nonces, PERMIT_TYPEHASH, selectors) | info (always pass) |
| `forwarding` | `delegate()` / `target()` accessors; `implementation()` raw | forwarding token (legacy TUSD, Synthetix proxy) |
| `pause` | `paused()`-like accessors (+ `pausedPublic`, …) and pause selectors in code; current value | quirk = pausable; **fail = paused now** |
| `blocklist` | blocklist views for the probe address and a sanctioned address, blocklist selectors in code, and whether transfer to OFAC-sanctioned addresses reverts while a normal transfer works | quirk = blocklist machinery present |
| `fee_parameters` | fee/tax accessors (`basisPointsRate`, `feeRate`, `_taxFee`, …) | quirk = fee switch exists (nonzero = active) |
| `transfer_return` | normal transfer: returns true / no return value (USDT) / returns false / reverts + recipient balance moved | no_return = quirk; false/reverts/unmoved = fail |
| `fee_on_transfer` | sender debit, recipient credit, totalSupply delta, token-contract delta | any fee / burn / bonus; ≤2 base units = rounding |
| `transfer_insufficient` | transfer(balance+1%) | reverts = pass; returns false = quirk (ZRX, cUSDC); succeeds = fail |
| `transfer_zero_value` | transfer(0) | revert = quirk (BNB, LEND, GYEN) |
| `self_transfer` | transfer to self: balance/supply change | any change or revert = quirk |
| `transfer_to_zero_address`, `transfer_to_dead_address`, `transfer_to_token_contract` | allowed? burn (supply drop) vs tokens lost | zero address allowed = quirk |
| `large_uint96` | revert reason mentions 96 bits / balance field width from the funding probes | uint96 / ≤128-bit balance fields |
| `max_uint_transfer`, `full_balance_transfer` | transfer(uint256.max) (moves whole balance = cUSDCv3 style), transfer(B) | |
| `max_tx_limit` | transfer of 5 % of supply | revert = max-tx / anti-whale |
| `approve_return`, `approve_nonzero_to_nonzero`, `approve_max`, `approve_zero_address`, `approve_zero_value`, `allowance_helpers` | return shape; allowance after; USDT race protection; max approve (uint96 cap); increase/decreaseAllowance/Approval | |
| `transferFrom_return`, `transferFrom_allowance_decrement`, `transferFrom_infinite_allowance`, `transferFrom_no_allowance`, `transferFrom_exceed_allowance`, `transferFrom_by_owner`, `transferFrom_zero_value` | spender = Sink; DSToken/WETH-style "owner needs no allowance"; infinite allowance not decremented | no/over allowance must revert, else fail |
| `erc777_hooks`, `contract_recipient` | ERC-1820 registered hooks (tokensToSend/Received), callbacks into a plain contract recipient (ERC-677/223 style), recipients that reject calls, balance seen inside the callback (reentrancy window) | |
| `gas_transfer` | gas of the first transfer (>120k = quirk) | |
| `same_block_repeat` | 2nd/3rd transfer in the same block | revert = cooldown / limit |
| `eoa_vs_contract` | EOA-mode vs harness-contract sender | differ = quirk |
| `anti_bot_limits` | revert-reason classes (trading not enabled, max tx, max wallet, cooldown, bot, blacklist, paused) from every normal-path call, and limit accessors (`_maxTxAmount`, `tradingOpen`, …) | |
| `rebasing` | evidence types: **slot-calibration ratio ≠ 1** (shares/gons/reflection/index), **accessors** (sharesOf, gonsPerFragment, scaledBalanceOf, `_rTotal`…), **7-day drift** of the same raw slot value | quirk if any positive evidence; pass says which evidence: `slot_identity` + drift status |

`script:<name>` rows (`unprobeable`/`error`) appear when one script could not run (gas bomb, node timeout).

## Known limitations
* Balance discovery fails for tokens where `balanceOf` does not touch a per-account storage slot of any contract in the
  access list (some Synthetix-style `target()` proxies such as sUSD, gated/KYC views) – recorded `unprobeable`.
* Native protocol tokens (code `0xef`, Base `0xB2000…`) are probed through the real-holder mode only; `bytecode_selectors`
  is `quirk native_protocol_token(code=0xef)` (no EVM code to scan); 7702-delegated EOAs (code `0xef0100…`, 23 bytes) are
  `unprobeable code_not_a_normal_contract`.
* Polygon `0x…1010` (native POL facade) is funded through the native balance and its `transfer` returns false in an `eth_call`
  (it needs a value transfer): the rows are real measurements of that facade, not of a normal ERC-20.
* Tokens whose `balanceOf(fresh address)` reverts (legacy TUSD, deprecated contracts) – `unprobeable` with the revert.
* Everything is a point-in-time measurement from a *contract holder* with a fake balance; owner-only paths, real DEX
  behaviour (that is W5), sell taxes, and allowlist-gated tokens that reject unknown senders (reported as `fail` with the
  revert reason) are out of scope. ERC-777 hooks need the ERC-1820 registry (present on all six chains, checked).
* Fast chains keep state only for seconds-minutes on free nodes (Arbitrum ~20-60 blocks, others ~64-128): `--block latest`
  re-pins per token (arbitrum every 2 s) and a token that outlives its window is retried on a fresh pin (up to 3 attempts);
  a run on a fixed old `--block N` needs archive endpoints (only drpc ethereum is known).
* Selector sets are an over-approximate PUSH4 scan and only resolve EIP-1967 / OZ-legacy / 1167 / beacon / `implementation()`.
* Free endpoints: throughput is bounded by rate limits (at most 5 requests per second per endpoint).
