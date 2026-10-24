# W3 — control & admin classifier

Question answered, per ERC-20 and with on-chain evidence: **who can change this token, and how?**

```
.venv/bin/python scripts/control/run.py --chain ethereum --tokens tokens.csv --block latest --out data/control/ethereum/
```

* `--tokens`: CSV with an `address` / `token` / `token_address` / `contract` column (rows are filtered by a `chain` column
  if present) or one address per line.
* `--block`: a block number, or `latest` (= head − 2, to avoid lagging load-balanced nodes). The block is resolved once,
  stored in `<out>/run.json` (number, hash, timestamp, evidence) and reused on resume. Every `eth_call` /
  `eth_getStorageAt` / `eth_getCode` is made at that block. Etherscan log queries use `toBlock = <that block>`.
  Old blocks need archive state: pass `--archive` (drpc is archive on ethereum; on the others it is whatever the free
  plan gives).
* `--rolling`: for long runs on public RPCs that only keep recent state (all non-Ethereum chains): every token is probed at its
  own freshly pinned `head − 2` block (thread-local pin, head cached 3 s). The row's `block` and every evidence line record the
  block actually used; `run.json` only holds the block at start. A row whose probe outlived the node's state window is `partial`
  ("all endpoints refused (archive/state unavailable)"): re-run with `--retry-partial`.
* Resumable: rows already in `<out>/control.jsonl` with `completeness == "complete"` are skipped. `--retry-partial`
  re-probes partial rows. `controllers.jsonl` caches classified controller addresses (same block only).
* Polite: every endpoint has a request limiter (4.5 req/s) **and** a calls/s limiter (15/s, batch items count),
  batches ≤ 3 (drpc free plan) or ≤ 10 (publicnode), Etherscan ≤ 1.8 req/s, 429/5xx back-off, automatic failover
  between the two endpoints per chain; the next request goes to the endpoint whose limiter frees first (load balancing); an endpoint that refuses state for one request is only skipped for that request. Use `--workers` (default 6) for concurrency across tokens.
* Modules: `ctl_rpc.py` (client, rate limiting, evidence), `ctl_abi.py` (selectors, slots, bytecode scan, decoders),
  `ctl_classify.py` (proxy detection, owner/role discovery, controller classification), `golden.py` (golden-set
  harness, see `data/control-golden/`).

## Companion scripts
`summarize.py <dir> --universe <csv> [--top N]` (distribution tables, single-EOA lists, categories incl. the `tokenized_security` tag, B20 section), `verify_eoa_claims.py <dir> [--chain X] --top N` (independent re-check of every single-EOA claim: code + nonce on two endpoints, slot / getter re-read on both, Etherscan AdminChanged log, and a fresh read-only re-simulation of the upgrade from the EOA on both endpoints with a stranger baseline), `golden.py` (golden set), `tests/run_synthetic.py` (22 anvil unit cases).

## Output

`<out>/control.jsonl` — one JSON row per token; `<out>/evidence/<token>.jsonl` and
`<out>/evidence/_ctrl/<controller>.jsonl` — raw evidence; `<out>/run.json`.

### Evidence format
One line per RPC request: `{id, chain, endpoint, block, request, response, retrieved_at, sha256}`.
`sha256` is over the canonical JSON (sorted keys, compact) of the response object exactly as the node returned it for
that id. `eth_getCode` responses > 6 KB are stored as `{result_omitted, result_len, code_keccak}` (disk policy; `sha256` is still the hash of the full response).
Etherscan log queries are stored with the API key redacted. Every claim inside a row carries `ev: ["evidence/<file>.jsonl#e12", …]`
pointing at the exact request/response lines. Unknown ≠ fail: transport errors, rate-limit exhaustion, unavailable logs etc. are listed in the row's
`probe_errors[]` and make the row `completeness: "partial"`; `status` is one of `ok`, `not_a_contract`, `unprobeable` (with `reason`) or `error`.

### Row fields
| field | meaning |
|---|---|
| `chain, token, block, probed_at, status, completeness, probe_errors[]` | identity, pinned block, health of the probe |
| `symbol, decimals, code{size,keccak,has_delegatecall,has_selfdestruct}` | basic facts (selector/size hints from a PUSH-aware bytecode walk) |
| `is_proxy` | `true` / `false` / `null` (bytecode has DELEGATECALL, no standard slot hit and no trace endpoint could confirm) |
| `proxy_type` | `eip1967` (transparent or UUPS), `eip1967_beacon`, `zos_legacy` (`org.zeppelinos.proxy.implementation`; USDC, AAVE v1…), `eip1822_uups`, `aragon_app_proxy`, `eip2535_diamond`, `eip897` (implementation()+proxyType()), `slot0_delegate_proxy` (Safe-style masterCopy at slot 0, confirmed by trace), `custom_delegatecall` (target observed with `debug_traceCall` on `decimals()`), `synthetix_target_proxy`, `implementation_getter` (low confidence: only `implementation()`), `eip1167` / `eip1167_0age` / `eip7511_push0` (immutable clone), `unknown_delegatecall_present`, or `null` |
| `proxy_confidence`, `proxy_detected_via` | `storage_slot`, `getter`, `getter_only`, `bytecode_pattern`, `observed_delegatecall_trace`, … |
| `implementation`, `implementation_chain[]`, `impl_is_proxy` | final implementation, and each hop if the implementation is itself behind another EIP-1967 / zos / 1822 layer (depth ≤ 3). For beacons the beacon's `implementation()`; for diamonds `facets{count,items}` |
| `upgradeable` + `upgradeable_reason` | `yes` (an upgrade authority exists), `no` (not a proxy / clone), `frozen` (every upgrade path ends in 0x0/0xdead), `protocol_only` (Base B20 precompile token: code is changed only by a chain upgrade), `no_known_path`, `unknown` |
| `b20{}` | Base B20 tokens only (see below): `variant`, `supply_cap`, `policies` (policy ids; Seize/Transfer/Mint policies), `paused_features_raw`, `ui_multiplier`; role-holder discovery status is in `access_control.status` |
| `upgrade_authority_basis` | how the authority was found: admin slot, beacon owner, Aragon kernel ACL, diamond owner, or *candidate* (UUPS: `_authorizeUpgrade` is custom code, so owner / UPGRADER_ROLE / DEFAULT_ADMIN holders are only candidates) |
| `upgrade_simulation`, `upgrade_authority_firm` | UUPS (and any candidate-based authority) is **verified by simulation**: read-only `eth_call` of `upgradeTo(currentImpl)` / `upgradeToAndCall(currentImpl,"")` with `from` = each candidate, plus a stranger baseline that must revert. Candidates whose call does not revert are `verified`; a path with no verified candidate keeps `assumed_or_unverified: true` and is excluded from the firm classes of `summarize.py` (reported as `authority_unverified`). `upgrade_authority_firm` is true when the authority was read from a slot/getter or passed the simulation |
| `upgrade_admin_chain[]` | every controller node reached from the upgrade authority: `{address, kind, summary, depth, via, parent, details, ev}` (depth 1 = the direct authority; max depth 4) |
| `upgrade_paths[]` | one entry per path: `addresses[]`, `kinds[]`, `labels[]`, **`terminal_kind`**, `terminal_summary`, `timelock_delay_seconds` (sum of timelock delays on the path), `truncated`, `verified` (simulation passed) / `assumed_or_unverified` |
| `owner_chain[]`, `owner_paths[]` | same for `owner()/getOwner()/_owner()/admin()/governance()/governor()/gov()/authority()`, DAI-style `wards` (RELY logs verified with `wards(x)==1`) and AccessControl `DEFAULT_ADMIN_ROLE` holders |
| `roles{}` | buckets `minter, minter_admin, pauser, blacklister, rescuer, burner, fee_setter, fee_recipient, upgrader, admin, default_admin, other_role:<hash>`; each holder `{address, via, kind, summary, terminal_kinds[], terminals[], ev}`. From named getters (`masterMinter()`, `pauser()`, `blacklister()`, `supplyController()`, `assetProtectionRole()`, `minter()` …) and AccessControl roles |
| `access_control{status, holders}` | `enumerated` (getRoleMember), `logs` (RoleGranted logs from Etherscan, each verified with `hasRole`), or `logs_unavailable…` (Base/BSC/Optimism: free Etherscan key has no access) in which case only discovered candidates are tested — **holders may be missing, flagged in status** |
| `wards{status, holders}` | DAI-style `rely/deny` wards |
| `capability_hints{}` | static *selector presence* labels (mint, burn_from_others, pause, blacklist, fee_or_tax_setter, trading_gate, upgrade, rescue_tokens, …) from PUSH4 constants in token + implementation code: a hint that the function exists, not proof that it works or who can call it |
| `privileged_capabilities[]`, `control_summary` | `immutable` (not a proxy, no controller found, no privileged selector), `controlled`, `unattributed_privileges` (privileged selectors but no controller found through known interfaces: needs manual review) |
| `paused`, `pending_owner` | current `paused()` and `pendingOwner()` if exposed |
| `upgrade_history_probe{}`, `upgrade_access_manager{}`, `blocks_used[]` | how an otherwise unverified upgrade authority was searched for (see Limitations), the AccessManager / canCall authority table, and the blocks a re-pinned probe spanned |
| `controllers{}` | full node table (address → kind, details, next, ev) used by the chains/paths above |
| `evidence[]` | evidence files for this row |

### Controller kinds (`terminal_kind`)
USDT-style `deprecate(address)` forwarding is an upgrade path even when `is_proxy`
is false. The full legacy interface fingerprint (deprecate/deprecated/upgradedAddress
and the three *ByLegacy forwarding selectors) is required. Its authority follows
`owner()/getOwner()`, with a read-only owner/stranger deprecate differential.
`upgrade_path_type = legacy_deprecate_forward` distinguishes it from DELEGATECALL
proxies. The C19 revision overlays saved original-block rows from
`data/control/c19/derived.jsonl`; `revise_legacy_forward.py` reproduces them offline
from its saved raw checks. `--live` fetches uncached targeted checks only, <=1 req/s.

`eoa` (no code; `details.nonce` = tx count), `eoa_7702` (code `0xef0100‖address`: an EOA that delegated to a contract; reported distinctly),
`safe` (`getThreshold/getOwners/VERSION` → `Safe n-of-m vX`), `multisig_legacy` (old Gnosis MultiSigWallet `required()+getOwners()`),
`timelock` (OZ `getMinDelay`; Compound `delay()+admin()`; Aave executor `getDelay()+getAdmin()`; DSPause-like; **not terminal** – the path continues to its
proposers/admin; the delay is added to `timelock_delay_seconds`), `governor` (OZ Governor `COUNTING_MODE`, Governor Bravo, Aragon Voting, DSChief, Aave v3 PayloadsController),
`dao_other` (Aragon app such as Agent / TokenManager: DAO-controlled via ACL, permissions not followed), `aragon_kernel` (non-terminal: followed to the ACL permission manager of `APP_MANAGER_ROLE`), `access_control_contract` (non-terminal: followed to DEFAULT_ADMIN/ADMIN/EXECUTOR/PROPOSER/UPGRADER holders),
`ownable_contract` (non-terminal: followed to its `owner()`; flavour `proxy_admin` when it has `getProxyAdmin`), `other_contract` (nothing recognisable; terminal), `renounced` (0x0 or 0x…dEaD),
`l1_alias` (on Arbitrum / Optimism / Base: address = L1 contract + `0x1111…1111`; it is **not** an EOA — the L1 contract's own classification is nested in `details.l1_node`),
`unclassified` (a controller could not be read after 3 tries; never cached; the row becomes `partial`). A path that hits the depth limit gets `terminal_kind = "truncated_depth>4:<kind>"`.
`immutable` is a token-level verdict (`control_summary`), not a controller kind.

### Base B20 precompile tokens (code == `0xef`)
17 tokens at `0xB2000000…` on Base (Coinbase tokenized stocks, cbZEC, cbHYPE, …) have `eth_getCode == 0xef` yet answer ERC-20 calls: they are
protocol-level (precompile) tokens of Base's B20 standard, not contracts with bytecode and not proxies. They are detected by `MINT_ROLE() == keccak256("MINT_ROLE")` on a
`0xef` account and reported as `proxy_type: b20_precompile`, `is_proxy: false`, `upgradeable: protocol_only`. Control is by role (mint, pause, policy ids, supply cap): the
classifier reads symbol/decimals/`supplyCap`/`pausedFeatures`/`uiMultiplier`/policy ids; role holders are discovered from `RoleGranted` logs (creation window, needs a node with
log/archive access), then from recent-activity candidates verified with `hasRole`, else reported `unresolved` — never guessed.

### Categories
`summarize.py` joins the universe `category_hint` and adds a `tokenized_security` tag by a name/symbol regex (xStock, Ondo, Coinbase tokenized, Backed, tokenized fund/treasury/equity).

## Method summary
1. Round 1 (one or two batched calls per token): `eth_getCode`; storage slots EIP-1967 impl/admin/beacon, EIP-1822 PROXIABLE, zos impl/admin, slot 0;
   views (`implementation()`, `proxyType()`, `kernel()`, `facetAddresses()`, `target()`, `owner()`, `getOwner()`, `admin()`, `governance()`, …).
2. Proxy decision order: EIP-1167 clone bytecode → 1967 impl slot → 1967 beacon slot → zos slot → 1822 slot → Aragon (`implementation()`+`kernel()`) → diamond (`facetAddresses()`) →
   EIP-897 → getters → `target()` → for contracts whose bytecode contains a DELEGATECALL opcode and match nothing above: `debug_traceCall(decimals())` with `callTracer`, take the DELEGATECALL targets
   (only on endpoints that serve `debug_traceCall`: drpc for eth/base/arb/polygon/op; BSC has none → `is_proxy: null`).
3. Implementation code is fetched (and followed through ≤ 3 proxy layers) to get the selector union; role getters are queried only if their selector is in that union (or everything, if proxy status is unresolved).
4. Controllers are classified once per (chain, block) and cached; each classification is ≤ 3 small batches. Cross-chain: L2 EOAs are checked for an L1 alias contract (`ethereum` endpoint, `latest`; its block is recorded in `details.l1_block`).

## Limitations (read before citing a number)
* "EOA" means *no code at that address* — it may be a hardware wallet, an MPC/custody key or a hot key; we cannot tell. EIP-7702 EOAs are reported separately.
* UUPS authority is verified only by read-only upgrade simulation from each candidate (owner / UPGRADER_ROLE / DEFAULT_ADMIN holders); a custom `_authorizeUpgrade` that consults other state (e.g. a registry) can still be missed, and a candidate that passes is proof of ability only at the pinned block.
* Only standard control interfaces are followed; custom gating (hard-coded addresses, `msg.sender == immutable`, factory-minted pairs) shows up as `unattributed_privileges` or as `capability_hints` only.
  Contracts with ward-style control whose only ward is another contract (DAI → DaiJoin) end at that contract (`other_contract`); the Vat's wards are not followed.
* Role holders are complete only when the contract is enumerable (`getRoleMember`) or Etherscan logs are reachable (ethereum, arbitrum, polygon; ≤ 1000 logs; a shared free key may rate-limit — then `logs_unavailable` is recorded).
  On Base / BSC / Optimism non-enumerable AccessControl holders are only found if they are already known candidates (owner, admin, role-getter addresses).
* Safe / multisig signers and timelock proposers are listed but not recursed into, EXCEPT 1-of-1 Safes / legacy multisigs, which are followed to their sole signer (a 1-of-1 wrapper is a single key, or the Safe behind it). Whether n-of-m signers are independent people is not verifiable on chain.
* Selector hints come from PUSH4 constants and can include false positives (and miss Vyper-style or dispatch-table-less code).
* Depth limit 4 hops from the token's direct controller; truncated paths are labelled.
* Pinned block: `latest` = head − 2; L1-alias checks use L1 `latest`. With `--rolling` (all non-Ethereum chains) the block is resolved at *send time* for every HTTP request (sentinel `@BLK`, fresh head − 2, cached 1.5 s), so a probe's reads can span a few blocks; every evidence line records its own block, the row's `block` is the head at the start of the token. Cross-block consistency inside one token is therefore not guaranteed (public RPCs prune state within ~1-2 minutes).
* Authority discovery beyond the plain slot / getter, all verified by read-only `eth_call` simulation (never a transaction): UUPS (`upgradeTo`/`upgradeToAndCall` of the current implementation from each candidate; if the proxy refuses a no-op upgrade the proxy address is used as target); Synthetix-style proxy (`setTarget`); Compound `CErc20Delegator` (`_setImplementation`); OZ AccessManaged tokens (`authority().getTargetFunctionRole` + role members from the manager's `RoleGranted` logs + `canCall` → immediate / delay seconds, delay stored as `access_manager_delay_seconds` and treated as a timelock); Solmate/Spiko-style `authority().canCall` (candidates = every address in the authority's logs); UUPS gated by an AccessControl role that has no direct holder (`role_admin`: holders of the role's admin role that can `grantRole` to themselves, marked "upgrade needs 2 txs"); the caller of the last `Upgraded` event (debug_traceTransaction) is added as a candidate. What was tried is in `upgrade_history_probe`, `upgrade_access_manager`, `upgrade_simulation` (`needed_roles`, `note`).
* Burn-style addresses in the EVM precompile range (0x…0001 … 0x…ffff) are classified `renounced` (no key can exist), e.g. Polygon DAI's proxyOwner.
* Classifier code and run history: rows were produced by successive commits of this tool; rows affected by a behaviour change were re-probed (`--force`, `--retry-partial`, `--reprobe-unverified`). `run.json` holds the block at the start of the run only.
