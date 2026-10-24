"""Chart: new ERC-20 deployments per month on Ethereum (all vs excl. LP shares) with 95% CI where sampled. -> data/census/monthly_counts.png"""
import pathlib
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, pandas as pd
ROOT = pathlib.Path(__file__).resolve().parents[2]
d = pd.read_csv(ROOT / "data/census/monthly_counts.csv"); d["t"] = pd.to_datetime(d.month + "-01")
fig, ax = plt.subplots(figsize=(11, 4.6), dpi=130)
for s, c, lab in (("all", "#7a7a7a", "all ERC-20-like (incl. LP shares)"), ("excl_lp", "#1f5fbf", "excluding LP / pool shares")):
    x = d[d.series == s]
    ax.plot(x.t, x.n_est, color=c, lw=1.8, label=lab)
    smp = x[x.n_blocks_sampled > 0]
    ax.fill_between(smp.t, smp.ci_low, smp.ci_high, color=c, alpha=.25, lw=0)
ax.axvline(pd.Timestamp("2025-02-15"), color="k", lw=.6, ls=":"); ax.text(pd.Timestamp("2025-02-25"), ax.get_ylim()[1] * .92, "sampled\n(95% CI)", fontsize=8, va="top")
ax.set_ylabel("new contracts per month"); ax.set_title("Ethereum mainnet: new ERC-20-like contract deployments per month (static definition)", fontsize=10)
ax.legend(frameon=False, fontsize=8, loc="upper left"); ax.spines[["top", "right"]].set_visible(False); ax.grid(axis="y", alpha=.2)
fig.tight_layout(); fig.savefig(ROOT / "data/census/monthly_counts.png")
