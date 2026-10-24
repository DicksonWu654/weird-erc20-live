"""Exact (complete-enumeration) monthly ERC-20 deployment counts to block 16.8M from the reduced Paradigm tables.

Reads data/census/cache/paradigm_slim/{blocks,cand}_*.parquet + data/census/blockmap.csv.
Writes data/census/cache/paradigm_tokens.parquet (every ERC-20-like contract incl. resolved EIP-1167 clones) and
data/census/paradigm_monthly.csv (month, n_create_all, n_erc20_all, n_erc20_excl_lp, n_erc20_plain, n_proxy_slot_unresolved, ...).
"""
import pathlib, duckdb
ROOT = pathlib.Path(__file__).resolve().parents[2]
C = ROOT / "data/census/cache"

KIND = """CASE WHEN s_getReserves AND s_token0 AND s_token1 THEN 'lp_v2'
 WHEN s_v1_tokenAddress AND s_v1_addLiquidity THEN 'lp_v1'
 WHEN s_bind OR s_getCurrentTokens OR s_getPoolId THEN 'lp_balancer'
 WHEN (s_asset AND s_convertToShares) OR s_pricePerShare THEN 'vault'
 WHEN s_aave_underlying OR s_ctoken_rate THEN 'receipt'
 WHEN s_deposit AND s_withdraw THEN 'wrapped_native'
 ELSE 'plain' END"""

con = duckdb.connect()
con.execute("PRAGMA memory_limit='12GB'")
con.execute(f"CREATE TABLE bm AS SELECT * FROM read_csv('{ROOT}/data/census/blockmap.csv')")
con.execute(f"CREATE VIEW cand AS SELECT * FROM read_parquet('{C}/paradigm_slim/cand_*.parquet')")
con.execute(f"CREATE VIEW blocks AS SELECT * FROM read_parquet('{C}/paradigm_slim/blocks_*.parquet')")
con.execute(f"""CREATE TABLE tokens AS
WITH direct AS (SELECT *, {KIND} AS kind, FALSE AS is_clone, NULL::BLOB AS impl FROM cand WHERE c20_core),
clones AS (SELECT c.block_number, c.create_index, c.transaction_hash, c.contract_address, c.deployer, c.factory, c.code_hash,
      c.code_len, c.t_transfer, i.s_totalSupply, i.s_balanceOf, i.s_transfer, i.s_approve, i.s_allowance, i.s_transferFrom,
      i.s_getReserves, i.s_token0, i.s_token1, i.s_v1_tokenAddress, i.s_v1_addLiquidity, i.s_minter, i.s_bind,
      i.s_getCurrentTokens, i.s_getPoolId, i.s_aave_underlying, i.s_ctoken_rate, i.s_asset, i.s_convertToShares,
      i.s_pricePerShare, i.s_deposit, i.s_withdraw, i.s_decimals, c.proxy_slot, c.clone_impl, TRUE AS c20_core
    FROM cand c JOIN (SELECT DISTINCT ON (contract_address) * FROM cand WHERE c20_core) i ON c.clone_impl = i.contract_address
    WHERE NOT c.c20_core)
SELECT block_number, create_index, transaction_hash, contract_address, deployer, factory, code_hash, code_len, kind, FALSE AS is_clone,
       s_approve AND s_allowance AND s_transferFrom AS full_erc20, s_decimals FROM direct
UNION ALL
SELECT block_number, create_index, transaction_hash, contract_address, deployer, factory, code_hash, code_len, {KIND} AS kind, TRUE,
       s_approve AND s_allowance AND s_transferFrom, s_decimals FROM clones
UNION ALL
-- Uniswap V1 exchanges are Vyper create_with_code_of proxies (not EIP-1167): take them from the complete NewExchange event scan
SELECT block, log_index, from_hex(substr(tx_hash,3)), from_hex(substr(pool_address,3)), NULL::BLOB, from_hex(substr(factory_or_manager,3)), NULL::BLOB, NULL::BIGINT, 'lp_v1', TRUE, TRUE, TRUE
FROM read_parquet('{ROOT}/data/census/dex_pools.parquet') WHERE dex='uniswap_v1' AND block < 16800000""")
con.execute("""CREATE TABLE tokens_m AS SELECT t.*, b.month, (factory IS NOT DISTINCT FROM deployer) AS direct_deploy
               FROM tokens t JOIN bm b ON t.block_number BETWEEN b.first_block AND b.last_block""")
con.execute("""CREATE TABLE loose_m AS SELECT b.month, count(*) AS n_loose_only FROM cand c JOIN bm b ON c.block_number BETWEEN b.first_block AND b.last_block
               WHERE c.c20_loose AND NOT c.c20_core AND c.clone_impl IS NULL GROUP BY 1""")
con.execute(f"COPY tokens_m TO '{C}/paradigm_tokens.parquet' (FORMAT parquet, COMPRESSION zstd)")
con.execute(f"""COPY (
 SELECT b.month,
   (SELECT sum(n_create) FROM blocks x WHERE x.block_number BETWEEN b.first_block AND b.last_block) AS n_create_all,
   (SELECT sum(n_with_code) FROM blocks x WHERE x.block_number BETWEEN b.first_block AND b.last_block) AS n_create_with_code,
   count(t.month) AS n_erc20_all,
   count(*) FILTER (WHERE t.kind NOT IN ('lp_v2','lp_v1','lp_balancer')) AS n_erc20_excl_lp,
   count(*) FILTER (WHERE t.kind IN ('plain','wrapped_native')) AS n_erc20_excl_lp_vault_receipt,
   count(*) FILTER (WHERE t.kind IN ('lp_v2','lp_v1','lp_balancer')) AS n_lp,
   count(*) FILTER (WHERE t.kind IN ('vault','receipt')) AS n_vault_receipt,
   count(*) FILTER (WHERE t.is_clone) AS n_clones,
   count(*) FILTER (WHERE NOT t.direct_deploy) AS n_factory_created,
   count(*) FILTER (WHERE t.full_erc20) AS n_full_erc20,
   any_value(l.n_loose_only) AS n_loose_selector_only_not_dispatched
 FROM bm b LEFT JOIN tokens_m t ON t.month = b.month LEFT JOIN loose_m l ON l.month = b.month
 WHERE b.first_block < 16800000
 GROUP BY b.month, b.first_block, b.last_block ORDER BY b.month) TO '{ROOT}/data/census/paradigm_monthly.csv' (HEADER)""")
print(con.sql(f"select * from read_csv('{ROOT}/data/census/paradigm_monthly.csv')").df().to_string())
