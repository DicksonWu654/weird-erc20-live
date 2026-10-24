#!/usr/bin/env python3
"""Finite-population coverage exercise for the revised two-phase intervals."""
import numpy as np
import pandas as pd

from reaggregate_revision import base_interval, kish, wilson, ROOT, OUT


def one_population(rng, harm_rate, rug_rate):
    # 80 blocks with varying token counts (including empty blocks), plus a
    # 600-token pool-linked candidate stratum.  The block draw samples PSUs.
    block_effect = rng.beta(harm_rate*8, (1-harm_rate)*8, 80)
    block_sizes = rng.poisson(5, 80)
    harm_a = rng.binomial(1, np.repeat(block_effect, block_sizes))
    harm_b = rng.binomial(1, harm_rate, 600)
    harm = np.r_[harm_a, harm_b]
    measurable = rng.binomial(1, .6, len(harm)).astype(bool)
    eligible = rng.binomial(1, .45, len(harm)).astype(bool)
    rug = np.zeros(len(harm), dtype=bool)
    rug[eligible] = rng.binomial(1, rug_rate, eligible.sum()).astype(bool)
    return harm, measurable, eligible, rug, block_sizes


def simulate(nrep=500):
    rng = np.random.default_rng(20261022)
    counts = pd.DataFrame({"n_blocks_sampled": [30]}, index=["2026-04"])
    blocks = pd.DataFrame({"first_block": [21_850_000], "last_block": [21_850_079]}, index=["2026-04"])
    rows = []
    for harm_rate, rug_rate in ((.08, .04), (.35, .25)):
        harm, measurable, eligible, rug, block_sizes = one_population(rng, harm_rate, rug_rate)
        offsets = np.r_[0, np.cumsum(block_sizes)]
        truth_h, truth_d, truth_r = harm.mean(), harm[measurable].mean(), rug.mean()
        covered_h = covered_d = covered_r = 0
        missing_r = 0
        for _ in range(nrep):
            bs = rng.choice(80, 30, replace=False)
            aidx = np.concatenate([np.arange(offsets[b], offsets[b+1]) for b in bs])
            bidx = offsets[-1] + rng.choice(600, 200, replace=False)
            ids = np.r_[aidx, bidx]
            w = np.r_[np.full(len(aidx), 80/30), np.full(len(bidx), 3.)]
            d = pd.DataFrame({"month": "2026-04", "source": np.r_[np.repeat("blocksample", len(aidx)),
                                                                     np.repeat("pool_linked", len(bidx))],
                              "stratum": np.r_[np.repeat("B", len(aidx)), np.repeat("A", len(bidx))],
                              "n_stratum_sampled": np.r_[np.repeat(30, len(aidx)), np.repeat(200, len(bidx))],
                              "N_stratum": np.r_[np.repeat(80, len(aidx)), np.repeat(600, len(bidx))],
                              "creation_block": np.r_[21_850_000 + np.repeat(bs, block_sizes[bs]), np.arange(len(bidx))],
                              "weight": w})
            h = harm[ids].astype(float)
            hp = float(w  @  h / w.sum())
            hlo, hhi, _ = base_interval(d, h, hp, blocks, counts)
            covered_h += hlo <= truth_h <= hhi
            domain = measurable[ids]
            domain_h = h[domain]
            domain_w = w[domain]
            domain_p = float(domain_w  @  domain_h/domain_w.sum())
            dlo, dhi, _ = base_interval(d.loc[domain], domain_h, domain_p,
                                        blocks, counts, frame=d)
            covered_d += dlo <= truth_d <= dhi
            eidx = np.flatnonzero(eligible[ids])
            if len(eidx) == 0:
                missing_r += 1
                continue
            n2 = min(40, len(eidx))
            pick = rng.choice(eidx, n2, replace=False)
            pi = n2/len(eidx)
            rp = float(w[pick]  @  rug[ids[pick]].astype(float)/pi/w.sum())
            a = np.zeros(len(d))
            a[pick] = rug[ids[pick]].astype(float)/pi
            p1lo, p1hi, _ = base_interval(d, a, rp, blocks, counts, .025)
            ww = w[pick]
            rate = float(ww  @  rug[ids[pick]].astype(float)/ww.sum())
            rlo, rhi = wilson(rate, kish(ww), .025)
            em = float(w[eidx].sum()/w.sum())
            lo, hi = max(0., p1lo-em*(rate-rlo)), min(1., p1hi+em*(rhi-rate))
            covered_r += lo <= truth_r <= hi
        rows.extend([{"scenario_harm": harm_rate, "scenario_rug": rug_rate, "outcome": "headline_trade_harm",
                      "replicates": nrep, "identified_replicates": nrep, "empirical_coverage": covered_h/nrep,
                      "true_share": truth_h},
                     {"scenario_harm": harm_rate, "scenario_rug": rug_rate, "outcome": "headline_measurable_domain",
                      "replicates": nrep, "identified_replicates": nrep, "empirical_coverage": covered_d/nrep,
                      "true_share": truth_d},
                     {"scenario_harm": harm_rate, "scenario_rug": rug_rate, "outcome": "liquidity_pulled",
                      "replicates": nrep, "identified_replicates": nrep-missing_r,
                      "empirical_coverage": covered_r/(nrep-missing_r), "true_share": truth_r}])
    pd.DataFrame(rows).to_csv(OUT / "coverage_check.csv", index=False)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    simulate()
