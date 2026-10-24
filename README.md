# Weird ERC-20 Live

This study measures how ERC-20 tokens behave across six EVM chains. The interactive website tells one continuous story, from top-token behaviour to new launches, with evidence, denominators and limits behind the results.

[Read the study](https://weird-erc20-live.shadow654.chatgpt.site) · [Source repository](https://github.com/DicksonWu654/weird-erc20-live) · [Changes](CHANGELOG.md)

The write-up source lives in `article/` in this repository.

This study was compiled with the help of AI agents; every number links to saved evidence; corrections welcome.

## Headline numbers

These values come from [article/numbers.json](article/numbers.json); each key records its query, source files and SHA-256.

| Measurement | Result | Ledger key |
|---|---|---|
| Chains in the live snapshot | 6 | `n_chains` |
| Ranked token deployments | 4,198 | `n_main` |
| Additional deployments, excluded from prevalence | 542 | `n_extras` |
| Total token deployments | 4,740 | `n_universe` |
| Ethereum census months | 117 | `census_months` |
| Launch-state harm among measurable pooled tokens, 2021 | 51.3% [48.9%, 53.7%] | `v2_2021_measurable_share`, `v2_2021_measurable_ci_low`, `v2_2021_measurable_ci_high` |
| Launch-state harm among measurable pooled tokens, Jan–Sep 2026 | 8.7% [6.4%, 11.2%] | `v2_2026_measurable_share`, `v2_2026_measurable_ci_low`, `v2_2026_measurable_ci_high` |

Harm means a simulated sale failure or tax of 10% or more, measured at first qualifying liquidity plus 300 blocks with at least 0.01 ETH-equivalent quote liquidity. These rates are conditional on venue mix, liquidity and measurability: liquidity depletion rose, so falling launch-state harm does not establish safer launches. Intervals are approximate and exclude classification error; unknown and unprobeable observations remain separate. Capability does not establish intent or off-chain custody arrangements.

Sampling metadata: Powered by [CoinGecko](https://www.coingecko.com/). CoinGecko-derived numeric market caps, prices and volumes are excluded. Attributed historical news prose in the memecoin timeline is retained as context.

Deploy only `site/dist` from this audited release copy. Its builder requires the release metadata and matching publication-input hashes.

## File map

| Path | Contents |
|---|---|
| `site-v5/src/`, `site-v5/tools/` | Launch website source, offline builder and independent displayed-number checker |
| `site/dist/` | Audited deployable website and its downloadable data |
| `article/` | Paper, template, ledger, bibliography, figures, methods and offline checks |
| `data/site/` | Compact public matrix, evidence references and aggregate publication inputs |
| `data/{universe,matrix,census,control,tier2,validate,verify}/` | Public tables, grouped weight moments, aggregate audits and methods READMEs |
| `scripts/`, `contracts/` | Measurement and aggregation code, offline release builder, probe harness and prebuilt bytecode |
| `data/release-projections.json` | Grouped-output digests and upstream input digests |
| `PUBLICATION-SOURCES.csv` | Per-file licence, attribution, byte count and SHA-256 |
| `RELEASE-REPORT.json` | Deployment gate, source projections, row-exclusion checks and ledger comparison |
| `CITATION.cff`, `CHANGELOG.md` | Citation and review history |

## Reproduce

Tested with CPython 3.12 on Linux. From the release root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### (a) Rebuild the website offline

```sh
scripts/site_export/build.sh
.venv/bin/python -m http.server 8000 --directory site/dist
```

Open <http://localhost:8000>. This rebuilds the launch site from shipped data and verifies displayed numbers against the repository ledger and matrix. Source data and release metadata are required; no measurement request is made. Generated output is reproducible byte for byte.

### (b) Recompute the ledger offline

```sh
cp article/numbers.json /tmp/weird-erc20-numbers-before.json
.venv/bin/python article/build_numbers.py
.venv/bin/python article/check_numbers.py
.venv/bin/python - <<'PY'
import json
from pathlib import Path
before = json.loads(Path('/tmp/weird-erc20-numbers-before.json').read_text())
after = json.loads(Path('article/numbers.json').read_text())
assert {k:v['value'] for k,v in before.items()} == {k:v['value'] for k,v in after.items()}
print(f"zero value differences across {len(after)} ledger entries")
PY
scripts/site_export/build.sh
```

Public grouped counts and weight moments reproduce every displayed ledger value, the paper Markdown and figures. Every public ledger source exists and is hashed. See [paper methods](article/README.md), [launch-state methods](data/tier2/v2/README.md), [current-state validation](data/validate/round3/README.md) and [delayed outcomes](data/verify/delayed/README.md).

This compact package cannot regenerate empirical sampling-unit bootstrap intervals, private selection audits or raw measurements. The raw evidence supporting measurements is saved in the research archive; public evidence pointers and upstream digests do not imply that all raw bundles are redistributed. Matched delayed rates are conditional on matching and survival and cannot be added to launch-state rates. Effective support below 30 is withheld. All-new February 2025 is structurally undefined; 2026 covers January–September. Literal Uniswap V2 differs from the V1/V2/Sushi family. V4 uses real position principal; virtual liquidity is a separate sensitivity. The legacy +7200 comparison changes selection as well as timing.

### (c) Remeasure with independently obtained inputs

Obtain RPC/archive access and an Etherscan API key; supply credentials through environment variables. Foundry (`forge`, `cast`, `anvil`) is needed for harness compilation and optional local-fork checks.

```sh
export ETHERSCAN_API_KEY='<your key>'
export SIM_ARCHIVE_URL='<archive RPC URL>'
export SIM_FRESH_URL='<latest RPC URL>'
.venv/bin/python scripts/probe/run.py --help
.venv/bin/python scripts/sim/run.py --help
.venv/bin/python scripts/control/run.py --help
```

Probe endpoints use `--rpc`; simulator endpoints use `SIM_*_URL` or CLI options; control defaults are in `scripts/control/ctl_rpc.py`. Respect limits of 5 requests/s per RPC endpoint and 4 requests/s to Etherscan, with backoff. The clients save evidence without sending real transactions or using private keys. The shared fetcher identifies itself as `weird-erc20-live-research/1.0`; `WEIRD_ERC20_USER_AGENT` overrides it.

The measurement READMEs describe universe construction, census collection, probing, control classification and simulation. Full historical remeasurement needs properly licensed upstream inputs beyond this package. Scanner APIs remain subject to their providers' terms. Per-token provider labels, response bodies and unlicensed source-list rows are excluded; public comparison tables describe agreement, not provider accuracy. Golden cases retain independently sourced expectations and matching observations; their reduced public subset does not reproduce the original full evaluation.

## Licences and citation

| Material | Licence / attribution |
|---|---|
| Project code | MIT; [LICENSE-CODE.txt](LICENSE-CODE.txt) |
| Project-authored paper, measurements and derived aggregates | CC BY 4.0; exceptions in [LICENSE-DATA.txt](LICENSE-DATA.txt) and [per-file inventory](PUBLICATION-SOURCES.csv) |
| Paradigm-derived census aggregates | Upstream CC0; Paradigm Data Portal attribution |
| Zellic-derived census statistics | Project aggregate counts only; no unlicensed per-token source rows |
| Universe identity and sampling bands | Powered by CoinGecko; upstream metadata subject to [API terms](https://www.coingecko.com/en/api_terms) |
| Scanner comparison statistics | Project aggregates attributed to honeypot.is and GoPlus; upstream provider terms apply |
| Historical news and academic references | Project annotations; cited facts retain original attribution |
| Bundled typefaces | Original font licences supplied alongside font files |

Dickson Wu (2026). *Weird ERC-20 Live: ERC-20 behaviour measurement snapshot*. Version 2026-10-22. Machine-readable citation: [CITATION.cff](CITATION.cff). No DOI has been assigned.
