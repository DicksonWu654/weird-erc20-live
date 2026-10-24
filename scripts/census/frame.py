"""Build the per-month stratified random sample frame of new ERC-20 tokens, with DEX linkage and design weights.

Population = ERC-20-like contracts (static strict definition) EXCLUDING LP/pool shares (kind lp_*); vault/receipt/wrapped kinds are kept
and labelled.  Sources (column `source`, `frame_basis`):
  paradigm_complete : creation blocks < 16.8M      (complete enumeration, Paradigm)   month = creation month
  zellic_complete   : 16.8M <= block < 21.85M      (complete enumeration, Zellic)     month = creation month
      strata A = token has a V1/V2/Sushi/V3/V4 pool creation event ever (complete scans), B = never; stratified SRS (seeded): A capped
      at nA, B fills up to nTotal; weight = N_h / n_h  (sum of weights per month/stratum = exact population count).
  pool_linked       : months >= 2025-02            (two-phase sample of tokens FIRST pooled in the month, 'new' = created <= 30 d
      before the pool)  month = first-pool month; stratum A_pool_first_in_month; weight = M_m / n_cand (pool_frame.py).
  blocksample       : creation blocks >= 21.85M, tokens with NO pool event ever, found in the random block sample (stratum B_no_pool_blocksample);
      weight = N_blocks / n_blocks (cluster-sample expansion).
Creation opcode for factory-created Paradigm tokens is INFERRED (CREATE iff address == keccak(rlp[creator, nonce<=NMAX]), else CREATE2);
accuracy against RPC creationMethod ground truth is reported in README/validation.
Output: data/census/sample_frame.parquet
  .venv/bin/python scripts/census/frame.py [--nA 250 --nTotal 400]  (stratum A capped at nA; stratum B fills up to nTotal)
"""
import argparse, pathlib, sys
import duckdb, numpy as np, pandas as pd
from Crypto.Hash import keccak
sys.path.insert(0, str(pathlib.Path(__file__).parent))
ROOT = pathlib.Path(__file__).resolve().parents[2]
C = ROOT / "data/census/cache"
LP = ("lp_v2", "lp_v1", "lp_balancer")
NMAX = 2048


def k256(b):
    h = keccak.new(digest_bits=256); h.update(b); return h.digest()


def rlp_nonce(n):
    if n == 0: return b"\x80"
    if n < 128: return bytes([n])
    nb = n.to_bytes((n.bit_length() + 7) // 8, "big"); return bytes([0x80 + len(nb)]) + nb


def create_nonce(creator_hex, addr_hex):
    c = bytes.fromhex(creator_hex[2:]); target = bytes.fromhex(addr_hex[2:])
    for n in range(0, NMAX):
        payload = b"\x94" + c + rlp_nonce(n)
        if k256(bytes([0xC0 + len(payload)]) + payload)[12:] == target:
            return n
    return None


def pool_links(dex_pools):
    p = dex_pools.reset_index(drop=True)
    long = pd.concat([
        p.assign(token=p.token0, other=p.token1), p.assign(token=p.token1, other=p.token0)], ignore_index=True)
    long = long[long.token != "0x" + "0" * 40]
    long["quoted"] = long.quote_token.notna()
    long = long.sort_values(["token", "block", "log_index"])
    g = long.groupby("token")
    first = g.head(1).set_index("token")
    fq = long[long.quoted].groupby("token").head(1).set_index("token")
    agg = g.agg(n_pools=("pool_address", "size"), pool_dexes=("dex", lambda s: ",".join(sorted(set(s)))))
    out = agg.join(first[["dex", "pool_address", "block", "other", "quote_symbol", "fee", "hooks"]].add_prefix("first_pool_"))
    out = out.join(fq[["dex", "pool_address", "block", "quote_token", "quote_symbol"]].add_prefix("first_quoted_pool_"))
    cnt30 = long.merge(first[["block"]].rename(columns={"block": "b0"}), left_on="token", right_index=True)
    cnt30 = cnt30[cnt30.block <= cnt30.b0 + 216_000].groupby("token").size().rename("n_pools_first_30d")
    return out.join(cnt30)


def enrich_creation(df, rps=2.0):
    """fill creator/tx/opcode for rows lacking them (Zellic tokens carry only address+block) via trace_block(creation block); cached."""
    import json
    from rpc import RPC
    cache_p = C / "creation_cache.jsonl"
    cache = {}
    if cache_p.exists():
        for line in open(cache_p):
            j = json.loads(line); cache[j["block"]] = j["creates"]
    need = sorted(set(int(b) for b in df.loc[df.creator.isna(), "block"]) - set(cache))
    if need:
        import concurrent.futures as cf, threading
        rpc = RPC(rps=rps, stream="frame_creation_trace")
        fh = open(cache_p, "a"); lock = threading.Lock(); cnt = [0]
        def one(b):
            tr = rpc.call("trace_block", [hex(b)], store=lambda x: {"n_traces": len(x)})
            by = {}
            for t in tr:
                if t.get("transactionHash"): by.setdefault(t["transactionHash"], []).append(t)
            cr = {}
            for h, ts in by.items():
                failed = {tuple(t["traceAddress"]) for t in ts if t.get("error")}
                root = next((u["action"]["from"] for u in ts if u["traceAddress"] == []), None)
                for t in ts:
                    res = t.get("result") or {}
                    if t["type"] == "create" and not t.get("error") and "address" in res:
                        ta = tuple(t["traceAddress"])
                        if any(ta[:k] in failed for k in range(len(ta))): continue
                        cr[res["address"].lower()] = dict(tx=h, creator=t["action"]["from"], tx_sender=root, opcode=t["action"].get("creationMethod"), ta=list(ta))
            with lock:
                cache[b] = cr; fh.write(json.dumps(dict(block=b, creates=cr)) + "\n"); fh.flush(); cnt[0] += 1
                if cnt[0] % 200 == 0: print("creation trace", cnt[0], "/", len(need), flush=True)
        with cf.ThreadPoolExecutor(6) as ex:
            list(ex.map(one, need))
        fh.close(); rpc.close()
    for i, r in df[df.creator.isna()].iterrows():
        d = cache.get(int(r.block), {}).get(r.address)
        if d:
            df.at[i, "creator"] = d["creator"]; df.at[i, "tx_hash"] = d["tx"]; df.at[i, "tx_from"] = d["tx_sender"]
            df.at[i, "creation_opcode"] = d["opcode"]; df.at[i, "direct_deploy"] = (d["ta"] == [])
    return df


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--nA", type=int, default=250); ap.add_argument("--nTotal", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20261021); ap.add_argument("--rps", type=float, default=2.0); ap.add_argument("--no-enrich", action="store_true")
    a = ap.parse_args()
    bm = pd.read_csv(ROOT / "data/census/blockmap.csv"); bmi = bm.set_index("month")
    dex = pd.read_parquet(ROOT / "data/census/dex_pools.parquet")
    links = pool_links(dex)
    con = duckdb.connect()
    par = con.execute(f"""SELECT month, block_number AS block, lower('0x'||hex(transaction_hash)) AS tx_hash, lower('0x'||hex(contract_address)) AS address,
        lower('0x'||hex(factory)) AS creator, lower('0x'||hex(deployer)) AS tx_from, direct_deploy, kind, is_clone, full_erc20,
        lower('0x'||hex(code_hash)) AS code_hash FROM read_parquet('{C}/paradigm_tokens.parquet')
        WHERE kind NOT IN ('lp_v2','lp_v1','lp_balancer') AND block_number < 16800000""").df()
    par["source"] = "paradigm_complete"; par["creation_opcode"] = np.where(par.direct_deploy, "create", None)
    zt = pd.read_parquet(C / "zellic_tokens.parquet")
    zt = zt[~zt.kind.isin(LP) & (zt.block < 21_850_000)].copy()
    zt = zt.rename(columns={"bytecode_hash": "code_hash"}); zt["source"] = "zellic_complete"
    for col in ("tx_hash", "creator", "tx_from", "creation_opcode"): zt[col] = None
    zt["direct_deploy"] = None
    out = []
    # ---- complete-enumeration sources: stratified SRS by (month, pool_ever)
    for src, df in (("paradigm_complete", par), ("zellic_complete", zt)):
        df = df.merge(links, left_on="address", right_index=True, how="left"); df["linked"] = df.n_pools.notna()
        df = df[df.month <= "2025-01"]
        for m, g in df.groupby("month"):
            rng = np.random.default_rng([a.seed, int(m.replace("-", "")), 0 if src == "paradigm_complete" else 1])
            MA = int(g.linked.sum()); nA_m = min(MA, a.nA)
            for st, cap in (("A_has_dex_pool_ever", a.nA), ("B_no_pool_ever", a.nTotal - nA_m)):
                h = g[g.linked] if st.startswith("A") else g[~g.linked]
                M = len(h)
                if M == 0: continue
                n = min(M, cap)
                pick = h.iloc[np.sort(rng.choice(M, n, replace=False))].copy()
                pick["stratum"] = st; pick["N_stratum"] = M; pick["n_stratum_sampled"] = n; pick["weight"] = M / n
                pick["frame_basis"] = "creation_month"
                out.append(pick)
    # ---- pool-linked two-phase sample (months >= 2025-02)
    pf = C / "poolframe_tokens.parquet"
    if pf.exists():
        p = pd.read_parquet(pf)
        p = p[p.month >= "2025-02"].copy()
        p = p[~p.kind.isin(LP)]
        p = p.rename(columns={"token": "address", "creation_tx": "tx_hash", "tx_sender": "tx_from", "creation_block": "block"})
        p["source"] = "pool_linked"; p["stratum"] = "A_pool_first_in_month"; p["N_stratum"] = p.M_population; p["n_stratum_sampled"] = p.n_candidates_checked
        p["frame_basis"] = "first_pool_month"; p["is_clone"] = p.clone_impl.notna(); p["linked"] = True
        # keep the linkage event the sampler used for audit, then take the canonical first_pool_* columns from `links` (same dex_pools source)
        p = p.rename(columns={"first_pool_block": "sampler_first_pool_block", "first_pool_dex": "sampler_first_pool_dex", "first_pool_address": "sampler_first_pool_address"})
        p = p.merge(links, left_on="address", right_index=True, how="left")
        out.append(p)
    # ---- block-sample, tokens never pooled (months >= 2025-02)
    stp, sbp = C / "sampled_tokens.parquet", C / "sampled_blocks.parquet"
    if stp.exists():
        smp = pd.read_parquet(stp); sb = pd.read_parquet(sbp)
        smp = smp[(smp.block >= 21_850_000) & ~smp.kind.isin(LP)].copy()
        nblk = sb[sb.block >= 21_850_000].groupby("month").size()
        smp = smp.merge(links, left_on="address", right_index=True, how="left")
        smp = smp[smp.n_pools.isna()]
        smp = smp.rename(columns={"code_hash": "code_hash"})
        smp["source"] = "blocksample"; smp["stratum"] = "B_no_pool_blocksample"; smp["frame_basis"] = "creation_month"
        for m, g in smp.groupby("month"):
            lo = max(int(bmi.loc[m, "first_block"]), 21_850_000); Nb = int(bmi.loc[m, "last_block"]) - lo + 1
            g = g.copy(); g["weight"] = Nb / nblk[m]; g["N_stratum"] = None; g["n_stratum_sampled"] = len(g); out.append(g)
    fr = pd.concat(out, ignore_index=True)
    fr["pool_ever"] = fr.n_pools.notna()
    if not a.no_enrich:
        fr = enrich_creation(fr, a.rps)
    # creation opcode inference for factory-created paradigm tokens whose opcode is unknown
    need = fr.creation_opcode.isna() & fr.creator.notna() & (fr.direct_deploy == False)
    fr.loc[need, "creation_opcode"] = [("create" if create_nonce(c, ad) is not None else "create2_inferred")
                                       for c, ad in zip(fr.loc[need, "creator"], fr.loc[need, "address"])]
    fr["creation_method"] = np.where(fr.direct_deploy == True, "direct_deploy", np.where(fr.direct_deploy == False, "factory", "unknown"))
    keep = ["month", "frame_basis", "source", "stratum", "weight", "N_stratum", "n_stratum_sampled", "address", "block", "tx_hash", "creator", "tx_from",
            "creation_method", "creation_opcode", "kind", "is_clone", "code_hash", "pool_ever", "n_pools", "pool_dexes", "n_pools_first_30d"] + \
           [c for c in fr.columns if c.startswith("first_pool_") or c.startswith("first_quoted_pool_")]
    keep = list(dict.fromkeys(c for c in keep if c in fr.columns))
    fr = fr[keep].rename(columns={"block": "creation_block", "tx_hash": "creation_tx", "creator": "creator_contract_or_eoa", "tx_from": "tx_sender"})
    fr = fr.sort_values(["month", "stratum", "creation_block"]).reset_index(drop=True)
    fr.to_parquet(ROOT / "data/census/sample_frame.parquet", index=False, compression="zstd")
    print(fr.groupby(["source", "stratum"]).size()); print(fr.groupby("month").size().describe())
    print(fr.groupby(["source", "stratum"]).weight.sum())


if __name__ == "__main__":
    main()
