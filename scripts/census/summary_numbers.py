"""Print the headline numbers used in README/notes (annual totals, coverage, pool-linkage stats)."""
import pathlib, numpy as np, pandas as pd
ROOT = pathlib.Path(__file__).resolve().parents[2]
mc = pd.read_csv(ROOT / "data/census/monthly_counts.csv")
mc["year"] = mc.month.str[:4]
piv = mc.pivot_table(index="year", columns="series", values="n_est", aggfunc="sum").round(0)
print(piv.to_string())
print("\nmonths covered:", mc.month.nunique(), mc.month.min(), mc.month.max(), "| by method:\n", mc[mc.series == "excl_lp"].method.str.replace(r"\+exact.*", "", regex=True).value_counts().to_string())
x = mc[mc.series == "excl_lp"].copy(); x["rel_hw"] = ((x.ci_high - x.ci_low) / 2 / x.n_est).round(3)
print("\nsampled months excl_lp (n_est, ci, rel half-width, blocks):\n", x[x.n_blocks_sampled > 0][["month", "n_est", "ci_low", "ci_high", "rel_hw", "n_blocks_sampled", "n_tokens_in_sample"]].round(0).to_string(index=False))
fr = pd.read_parquet(ROOT / "data/census/sample_frame.parquet")
print("\nframe rows", len(fr), "months", fr.month.nunique(), "| per month min/median/max", fr.groupby("month").size().min(), fr.groupby("month").size().median(), fr.groupby("month").size().max())
print(fr.groupby(["source", "stratum"]).agg(rows=("address", "size"), weight_sum=("weight", "sum")).round(0).to_string())
fr["year"] = fr.month.str[:4]
fr["w"] = fr.weight
g = fr[fr.source.isin(["paradigm_complete", "zellic_complete"])].groupby("year").apply(lambda d: pd.Series(dict(pool_ever_share=np.average(d.pool_ever, weights=d.w), factory_share=np.average(d.creation_method == "factory", weights=d.w), clone_share=np.average(d.is_clone.astype(bool), weights=d.w))))
print("\nweighted shares (complete-enumeration months):\n", g.round(3).to_string())
