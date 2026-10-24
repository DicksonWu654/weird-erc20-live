"""Independent recall checks of the static classifier using DEX events as ground truth (complete event scans):
 (1) every Uniswap-V2/Sushi pair created before block 16.8M should be classified lp_v2 (pair contracts are ERC-20 LP tokens);
 (2) every non-quote token that has a V2/Sushi/V3 pool should be classified ERC-20 (any kind) unless it is a proxy / pre-2017.
Writes data/census/evidence/validate_recall.json."""
import json, pathlib, duckdb
ROOT = pathlib.Path(__file__).resolve().parents[2]
C = ROOT / "data/census/cache"
con = duckdb.connect()
con.execute("PRAGMA memory_limit='10GB'")
con.execute(f"""CREATE TABLE pt AS SELECT lower('0x'||hex(contract_address)) a, kind, month_ok FROM (
   SELECT t.*, TRUE month_ok FROM read_parquet('{C}/paradigm_tokens.parquet') t)""")
con.execute(f"""CREATE TABLE cand AS SELECT lower('0x'||hex(contract_address)) a, block_number, proxy_slot, clone_impl IS NOT NULL AS is_clone, c20_core, code_len
                FROM read_parquet('{C}/paradigm_slim/cand_*.parquet')""")
con.execute(f"""CREATE TABLE allc AS SELECT lower('0x'||hex(contract_address)) a, block_number FROM read_parquet('{C}/paradigm_slim/cand_*.parquet') WHERE FALSE""")
con.execute(f"CREATE TABLE pools AS SELECT * FROM read_parquet('{ROOT}/data/census/dex_pools.parquet') WHERE block < 16800000")
out = {}
# (1) pair recall
r = con.execute("""SELECT count(*) n, count(pt.a) found, count(*) FILTER (WHERE pt.kind='lp_v2') lp_v2
                   FROM pools p LEFT JOIN pt ON pt.a = p.pool_address WHERE p.dex IN ('uniswap_v2','sushiswap_v2')""").fetchone()
out["v2_pairs_before_16.8M"] = dict(n=r[0], classified_erc20=r[1], classified_lp_v2=r[2])
# (2) token recall: distinct non-quote tokens in V2/Sushi/V3 pools (pool created < 16.8M); creation unknown for tokens older than data -> use existence of ANY creation row
con.execute("""CREATE TABLE ptoks AS SELECT DISTINCT tok FROM (
   SELECT base_token tok FROM pools WHERE dex IN ('uniswap_v2','sushiswap_v2','uniswap_v3') AND quote_token IS NOT NULL
   UNION SELECT token0 FROM pools WHERE dex IN ('uniswap_v2','sushiswap_v2','uniswap_v3') AND quote_token IS NULL
   UNION SELECT token1 FROM pools WHERE dex IN ('uniswap_v2','sushiswap_v2','uniswap_v3') AND quote_token IS NULL) WHERE tok IS NOT NULL""")
r = con.execute("""SELECT count(*) n, count(pt.a) found_erc20, count(*) FILTER (WHERE pt.a IS NULL AND c.a IS NOT NULL AND c.proxy_slot) as proxy_slot_unresolved,
                   count(*) FILTER (WHERE pt.a IS NULL AND c.a IS NOT NULL AND c.is_clone) clone_unresolved
                   FROM ptoks LEFT JOIN pt ON pt.a = ptoks.tok LEFT JOIN cand c ON c.a = ptoks.tok""").fetchone()
out["tokens_with_v2_sushi_v3_pool_before_16.8M"] = dict(n=r[0], classified_erc20=r[1], proxy_slot_unresolved=r[2], clone_unresolved=r[3])
print(json.dumps(out, indent=1))
(ROOT / "data/census/evidence/validate_recall.json").write_text(json.dumps(out, indent=1))
