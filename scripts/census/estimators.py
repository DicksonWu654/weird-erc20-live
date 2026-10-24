"""Block-sampling estimators of monthly totals (shared by the simulation and the production estimate).

y: per-sampled-block counts, N: number of blocks in the month, x (optional): auxiliary per-block counts with KNOWN monthly
total Xtot (e.g. V1/V2/Sushi pair-creation events, an exact log scan).
 srs:  N * mean(y); normal CI with normal quantile and finite-population correction.
 boot: percentile bootstrap of N*mean(y) (B resamples), robust to skew.
 reg:  regression (difference) estimator  N*(ybar + b*(Xbar - xbar)), b = OLS slope; residual-based variance.
"""
import numpy as np
from statistics import NormalDist


def _z(alpha):
    return NormalDist().inv_cdf(1 - alpha / 2)


def srs(y, N, alpha=0.05):
    n = len(y); m = y.mean(); v = y.var(ddof=1) if n > 1 else 0.0
    se = N * np.sqrt((1 - n / N) * v / n)
    t = _z(alpha)  # n >= several hundred: t ~ z
    return N * m, N * m - t * se, N * m + t * se


def boot(y, N, B=500, alpha=0.05, rng=None):
    rng = rng or np.random.default_rng(0)
    n = len(y)
    means = y[rng.integers(0, n, (B, n))].mean(1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return N * y.mean(), N * lo, N * hi


def reg(y, x, N, Xtot, alpha=0.05):
    n = len(y); xb, yb = x.mean(), y.mean()
    sxx = ((x - xb) ** 2).sum()
    b = ((x - xb) * (y - yb)).sum() / sxx if sxx > 0 else 0.0
    e = (y - yb) - b * (x - xb)
    s2 = (e ** 2).sum() / max(n - 2, 1)
    est = N * (yb + b * (Xtot / N - xb))
    se = N * np.sqrt((1 - n / N) * s2 / n)
    t = _z(alpha)
    return est, est - t * se, est + t * se
