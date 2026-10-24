"""Post-process block-sample jsonl files: resolve EIP-1167 clone implementations, classify, emit per-block and per-token tables.

  .venv/bin/python scripts/census/build_sampled_tokens.py
Reads  data/census/cache/blocksample/*.jsonl, Paradigm cand parquet (for pre-16.8M implementations), eth_getCode for the rest.
Writes data/census/cache/sampled_blocks.parquet (month, block, n_ok, n_tok_all, n_tok_excl_lp, ...) and
       data/census/cache/sampled_tokens.parquet (one row per ERC-20-like token found in sampled blocks).
"""
import json, pathlib, sys
import duckdb, pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from rpc import RPC, ROOT
from classify import flags, kind as kind_of

C = ROOT / "data/census/cache"
IMPL_CACHE = C / "impl_flags.json"
LP = {"lp_v2", "lp_v1", "lp_balancer"}


def kind_from_list(fl):
    s = set(fl)
    f = {k: (k in s) for k in ["s_getReserves", "s_token0", "s_token1", "s_v1_tokenAddress", "s_v1_addLiquidity", "s_bind",
                               "s_getCurrentTokens", "s_getPoolId", "s_asset", "s_convertToShares", "s_pricePerShare",
                               "s_aave_underlying", "s_ctoken_rate", "s_deposit", "s_withdraw"]}
    return kind_of(f)


def load_blocks():
    for p in sorted(q for q in (C / "blocksample").glob("*.jsonl") if not q.name.endswith(".failed.jsonl")):
        month = p.stem
        for line in open(p):
            j = json.loads(line)
            yield month, j


def main():
    cache = json.loads(IMPL_CACHE.read_text()) if IMPL_CACHE.exists() else {}
    recs = list(load_blocks())
    impls = {c["clone_impl"] for _, j in recs for c in j["creates"] if c["clone_impl"] and not c["c20_core"]}
    need = sorted(i for i in impls if i not in cache)
    print("clone impls", len(impls), "unresolved", len(need), flush=True)
    if need:
        con = duckdb.connect()
        con.execute(f"CREATE TABLE ci AS SELECT lower('0x'||hex(contract_address)) AS a, * FROM read_parquet('{C}/paradigm_slim/cand_*.parquet') WHERE c20_core")
        rpc = RPC(stream="impl_code", rps=4)
        for i in need:
            row = con.execute("select s_getReserves,s_token0,s_token1,s_v1_tokenAddress,s_v1_addLiquidity,s_bind,s_getCurrentTokens,s_getPoolId,s_asset,s_convertToShares,s_pricePerShare,s_aave_underlying,s_ctoken_rate,s_deposit,s_withdraw from ci where a=?", [i]).fetchone()
            if row:
                names = ["s_getReserves", "s_token0", "s_token1", "s_v1_tokenAddress", "s_v1_addLiquidity", "s_bind", "s_getCurrentTokens", "s_getPoolId", "s_asset", "s_convertToShares", "s_pricePerShare", "s_aave_underlying", "s_ctoken_rate", "s_deposit", "s_withdraw"]
                cache[i] = dict(c20=True, kind=kind_from_list([n for n, v in zip(names, row) if v]), src="paradigm")
            else:
                code = rpc.call("eth_getCode", [i, "latest"], store="hash")
                b = bytes.fromhex(code[2:])
                if not b:
                    cache[i] = dict(c20=False, kind=None, src="rpc_empty")
                else:
                    f = flags(b)
                    cache[i] = dict(c20=bool(f["c20_core"]), kind=kind_of(f) if f["c20_core"] else None, src="rpc")
        rpc.close()
        IMPL_CACHE.write_text(json.dumps(cache))
    blocks, toks = [], []
    for month, j in recs:
        n_all = n_nolp = n_plain = n_fac = n_lp = n_proxy = 0
        for c in j["creates"]:
            k = None
            if c["c20_core"]:
                k = kind_from_list(c.get("flags", []))
            elif c["clone_impl"] and not c["c20_core"] and cache.get(c["clone_impl"], {}).get("c20"):
                k = cache[c["clone_impl"]]["kind"]
            elif c["proxy_slot"]:
                n_proxy += 1
            if k is None:
                continue
            tok = dict(month=month, block=j["block"], address=c["address"], creator=c["creator"], tx_hash=c["tx_hash"],
                       tx_from=c["tx_from"], trace_address=c["trace_address"], creation_opcode=c["method"],
                       direct_deploy=(c["trace_address"] == []), kind=k, is_clone=bool(c["clone_impl"] and not c["c20_core"]),
                       code_hash=c["code_hash"], code_len=c["code_len"], clone_impl=c["clone_impl"],
                       full_erc20=all(x in c.get("flags", []) for x in ["s_approve", "s_allowance", "s_transferFrom"]) if c["c20_core"] else None,
                       trace_sha256=j["sha256"])
            toks.append(tok)
            n_all += 1; n_lp += k in LP; n_nolp += k not in LP; n_plain += k in ("plain", "wrapped_native"); n_fac += not tok["direct_deploy"]
        blocks.append(dict(month=month, block=j["block"], n_create_ok=j["n_ok"], n_tok_all=n_all, n_tok_excl_lp=n_nolp,
                           n_tok_plain=n_plain, n_tok_lp=n_lp, n_tok_factory=n_fac, n_proxy_slot=n_proxy, n_traces=j["n_traces"]))
    pd.DataFrame(blocks).to_parquet(C / "sampled_blocks.parquet")
    pd.DataFrame(toks).to_parquet(C / "sampled_tokens.parquet")
    print("blocks", len(blocks), "tokens", len(toks))


if __name__ == "__main__":
    main()
