# Paper build in the public release

The paper uses completed launch-state estimates in `data/tier2/v2/`, ranked matrix tables, current-state validation in `data/validate/round3/`, and matched delayed outcomes in `data/verify/delayed/`. Every ledger entry records its concrete public input, query and SHA-256. The root README describes reproduction tiers and omitted evidence.

Run from the release root:

```sh
.venv/bin/python article/build_numbers.py
.venv/bin/python article/check_numbers.py
scripts/site_export/build.sh
```

The builder renders `paper.template.md` into `paper.md`, recomputes every ledger entry and regenerates the three figures and their input hashes. Release adapters substitute grouped counts/weight moments for restricted token rows; arithmetic, domain predicates, table rendering and plotting remain in the current article builder. The checker independently verifies weighted points and delayed Kish sizes from these moments. It does not repeat the upstream raw-evidence audit or regenerate empirical bootstrap intervals from private sampling units.

`paper.html` is the standalone embedded article; `data/site-paper.html` is the same standalone article supplied to the website. Both are supplied. Optional HTML regeneration requires Pandoc with citeproc:

```sh
cd article
pandoc paper.md --citeproc --bibliography=refs.bib --standalone --mathml --css=paper.css --embed-resources -o paper.html
```

The bibliography and numeric CSL style ship; third-party bibliographic HTTP bodies do not. The shipped methods describe the completed snapshot and its reproduction limits.
