"""Stream the Paradigm Data Portal `ethereum_contracts` dataset (CC0, contract create traces to block 16.8M) and
reduce it to (a) per-block creation counts and (b) a slim table of candidate token contracts with static-code flags.

Each source parquet (<=~900MB) is downloaded to data/census/cache/paradigm_dl, reduced with DuckDB, then deleted, so
disk use stays ~1GB. Source file sha256 + url + size are written to data/census/evidence/paradigm_files.jsonl.

Usage: .venv/bin/python scripts/census/paradigm_ingest.py [file_index ...]   (1-based, default all; resumable)
"""
import hashlib, json, os, pathlib, subprocess, sys, time
import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[2]
DL = ROOT / "data/census/cache/paradigm_dl"
OUT = ROOT / "data/census/cache/paradigm_slim"
EVID = ROOT / "data/census/evidence"
BASE = "https://datasets.paradigm.xyz/datasets/ethereum_contracts/ethereum_contracts__v1_0_0__"
RANGES = [(i * 1000000, i * 1000000 + 999999) for i in range(16)] + [(16000000, 16799999)]

SEL = dict(totalSupply="18160ddd", balanceOf="70a08231", transfer="a9059cbb", approve="095ea7b3",
           allowance="dd62ed3e", transferFrom="23b872dd", getReserves="0902f1ac", token0="0dfe1681", token1="d21220a7",
           v1_tokenAddress="9d76ea58", v1_addLiquidity="422f1043", minter="07546172", bind="e4e1e538",
           getCurrentTokens="cc77828d", getPoolId="38fff2d0", aave_underlying="b16a19de", ctoken_rate="182df0f5",
           asset="38d52e0f", convertToShares="c6e6f592", pricePerShare="99530b06", deposit="d0e30db0",
           withdraw="2e1a7d4d", decimals="313ce567")
TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SLOTS = ["360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc",
         "7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3",
         "a3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50",
         "c5f16f0fcc639fa48a6947836d9850f504798523bf8c9a3a87d5876cf622bcf7"]


def has(h):
    # DuckDB has no BLOB substring search; the VARCHAR rendering of a BLOB is a deterministic byte-wise escaping,
    # so searching the rendering of the pattern is exact (up to literal backslash-x text inside bytecode).
    return f"contains(cv, from_hex('{h}')::VARCHAR)"


TAILS = ["14"] + [f"{d:02x}14" for d in range(0x80, 0x85)] + ["60005114"] + [f"{d:02x}18" for d in range(0x80, 0x85)]


def dispatch(sel):
    """`PUSH4 sel` followed by a dispatcher comparison (see classify.py)."""
    return "(" + " OR ".join(has("63" + SEL[sel] + t) for t in TAILS) + ")"


def reduce_file(path, idx):
    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='10GB'; PRAGMA threads=4;")
    flags = ",\n".join(f"{has(h)} AS s_{k}" for k, h in SEL.items())
    slots = " OR ".join(has(h) for h in SLOTS)
    # EIP-1167 families: classic (45B), PUSH0 variant (45B), solady/0age variant (44-ish B). impl address offsets differ.
    con.execute(f"""
    CREATE TABLE uf AS SELECT code_hash, octet_length(code) AS code_len,
      {has(TOPIC)} AS t_transfer, {flags},
      ({slots}) AS proxy_slot,
      {dispatch("totalSupply")} AS d_totalSupply, {dispatch("balanceOf")} AS d_balanceOf, {dispatch("transfer")} AS d_transfer,
      CASE WHEN octet_length(code)=45 AND code[1:10]=from_hex('363d3d373d3d3d363d73') THEN code[11:30]
           WHEN octet_length(code)=45 AND code[1:9]=from_hex('365f5f375f5f365f73') THEN code[10:29]
           WHEN octet_length(code)=44 AND code[1:11]=from_hex('3d3d3d3d363d3d37363d73') THEN code[12:31]
      END AS clone_impl
    FROM (SELECT code_hash, code, code::VARCHAR AS cv FROM (SELECT code_hash, any_value(code) AS code FROM read_parquet('{path}') GROUP BY code_hash))""")
    con.execute(f"""CREATE TABLE base AS SELECT r.block_number, r.create_index, r.transaction_hash, r.contract_address, r.deployer, r.factory,
      r.code_hash, coalesce(uf.code_len, 0) AS code_len, uf.* EXCLUDE (code_hash, code_len)
      FROM read_parquet('{path}') r LEFT JOIN uf ON r.code_hash = uf.code_hash""")
    con.execute(f"""COPY (SELECT block_number, count(*) AS n_create, count(*) FILTER (WHERE code_len>0) AS n_with_code
                 FROM base GROUP BY 1 ORDER BY 1) TO '{OUT}/blocks_{idx:02d}.parquet' (FORMAT parquet, COMPRESSION zstd)""")
    con.execute(f"""COPY (SELECT *, (d_totalSupply AND d_balanceOf AND d_transfer AND t_transfer) AS c20_core,
                        (s_totalSupply AND s_balanceOf AND s_transfer AND t_transfer) AS c20_loose
                 FROM base WHERE code_len>0 AND ((s_totalSupply AND s_balanceOf AND s_transfer AND t_transfer)
                      OR clone_impl IS NOT NULL OR proxy_slot)
                 ORDER BY block_number, create_index) TO '{OUT}/cand_{idx:02d}.parquet' (FORMAT parquet, COMPRESSION zstd)""")
    n, = con.execute("select count(*) from base").fetchone()
    return n


def main():
    DL.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True); EVID.mkdir(parents=True, exist_ok=True)
    todo = [int(a) for a in sys.argv[1:]] or list(range(1, len(RANGES) + 1))
    for i in todo:
        if (OUT / f"cand_{i:02d}.parquet").exists():
            print("skip", i); continue
        lo, hi = RANGES[i - 1]
        url = f"{BASE}{lo:08d}_to_{hi:08d}.parquet"
        p = DL / f"f{i:02d}.parquet"
        t = time.time()
        subprocess.run(["curl", "-sSL", "--fail", "--retry", "5", "-C", "-", "-o", str(p), url], check=True)
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                h.update(chunk)
        n = reduce_file(str(p), i)
        rec = dict(idx=i, url=url, bytes=p.stat().st_size, sha256=h.hexdigest(), rows=n,
                   retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), license="CC0")
        with open(EVID / "paradigm_files.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        p.unlink()
        print(f"file {i} done rows={n} in {time.time()-t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
