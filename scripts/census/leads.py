"""Research leads from the exact enumerations: deployment spikes, mass-deployed code hashes, factory waves.
Writes data/census/leads_*.csv"""
import pathlib, duckdb, pandas as pd
ROOT = pathlib.Path(__file__).resolve().parents[2]; C = ROOT / "data/census/cache"
con = duckdb.connect()
con.execute(f"""CREATE TABLE p AS SELECT month, lower('0x'||hex(contract_address)) address, lower('0x'||hex(code_hash)) code_hash, lower('0x'||hex(factory)) creator, lower('0x'||hex(deployer)) tx_sender,
   kind, is_clone, direct_deploy FROM read_parquet('{C}/paradigm_tokens.parquet') WHERE kind NOT IN ('lp_v2','lp_v1','lp_balancer') AND block_number < 16800000""")
con.execute(f"""CREATE TABLE z AS SELECT month, address, bytecode_hash code_hash, NULL::VARCHAR creator, NULL::VARCHAR tx_sender, kind, is_clone, NULL::BOOLEAN direct_deploy
   FROM read_parquet('{C}/zellic_tokens.parquet') WHERE kind NOT IN ('lp_v2','lp_v1','lp_balancer') AND block < 21850000""")
con.execute("CREATE TABLE t AS SELECT * FROM p UNION ALL SELECT * FROM z")
con.execute(f"""COPY (SELECT code_hash, count(*) n_tokens, min(month) first_month, max(month) last_month, count(distinct month) months_active, any_value(kind) kind, any_value(address) example_address,
   any_value(is_clone) is_clone FROM t GROUP BY 1 ORDER BY n_tokens DESC LIMIT 200) TO '{ROOT}/data/census/leads_top_code_hashes.csv' (HEADER)""")
con.execute(f"""COPY (SELECT creator, count(*) n_tokens, min(month) first_month, max(month) last_month, count(distinct code_hash) distinct_codes FROM p WHERE NOT direct_deploy
   GROUP BY 1 ORDER BY n_tokens DESC LIMIT 200) TO '{ROOT}/data/census/leads_top_factories_to_2023-03.csv' (HEADER)""")
con.execute(f"""COPY (SELECT tx_sender, count(*) n_tokens, min(month) first_month, max(month) last_month, count(distinct code_hash) distinct_codes FROM p
   GROUP BY 1 ORDER BY n_tokens DESC LIMIT 200) TO '{ROOT}/data/census/leads_top_deployer_eoas_to_2023-03.csv' (HEADER)""")
# per-month: total excl-LP tokens, share of the single biggest code hash, share created by the biggest factory
con.execute(f"""COPY (SELECT m.month, m.n, m.top_hash_n, round(m.top_hash_n::DOUBLE/m.n,3) top_hash_share FROM (
   SELECT month, count(*) n, max(c) top_hash_n FROM (SELECT month, code_hash, count(*) OVER (PARTITION BY month, code_hash) c FROM t) GROUP BY month) m ORDER BY month)
   TO '{ROOT}/data/census/leads_monthly_concentration.csv' (HEADER)""")
d = pd.read_csv(ROOT / "data/census/leads_monthly_concentration.csv")
d["trailing6_median"] = d.n.shift(1).rolling(6).median(); d["spike_ratio"] = (d.n / d.trailing6_median).round(2)
d.sort_values("spike_ratio", ascending=False).head(15).to_csv(ROOT / "data/census/leads_spike_months.csv", index=False)
print(d.sort_values("spike_ratio", ascending=False).head(12).to_string(index=False))
print(pd.read_csv(ROOT / "data/census/leads_top_code_hashes.csv").head(10).to_string(index=False))
