"""Stream the Zellic 'all-ethereum-contracts' dataset (Hugging Face Zellic/all-ethereum-contracts, snapshot at block 21,850,000;
contracts.csv = address,bytecode_hash,blocknum; bytecodes.csv = bytecode_hash,bytecode) WITHOUT storing the raw files
(curl | funzip | reducer; sha256 of each zip recorded).  No licence is declared on the dataset card: we use it only to derive
aggregate counts / classification flags and redistribute none of its contents.

Stage 1 (bytecodes): flags per unique bytecode (same static classifier as classify.py) -> cache/zellic_flagged_hashes.parquet
Stage 2 (contracts): per-block creation counts for ALL contracts (cache/zellic_blocks.parquet) and the (address, hash, block)
  rows whose hash is flagged (block >= 14M) or that are clone implementations (cache/zellic_cand.parquet).
  .venv/bin/python scripts/census/zellic_ingest.py [stage1|stage2]
"""
import csv, hashlib, io, json, pathlib, subprocess, sys, time
import pandas as pd
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from classify import flags, SEL
ROOT = pathlib.Path(__file__).resolve().parents[2]
C = ROOT / "data/census/cache"; EV = ROOT / "data/census/evidence"
BASE = "https://huggingface.co/datasets/Zellic/all-ethereum-contracts/resolve/main/"
csv.field_size_limit(1 << 30)
KEEP_FROM = 14_000_000


def stream(name):
    """yield binary lines of the first member of <name>.zip, hashing the zip bytes as they pass."""
    curl = subprocess.Popen(["curl", "-sSL", "--fail", "--retry", "5", BASE + name], stdout=subprocess.PIPE)
    fz = subprocess.Popen(["funzip"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    h = hashlib.sha256(); nbytes = [0]
    import threading
    def pump():
        for chunk in iter(lambda: curl.stdout.read(1 << 20), b""):
            h.update(chunk); nbytes[0] += len(chunk)
            try: fz.stdin.write(chunk)
            except BrokenPipeError: break
        try: fz.stdin.close()
        except Exception: pass
    t = threading.Thread(target=pump, daemon=True); t.start()
    return fz.stdout, h, nbytes, t, curl


def stage1():
    out, h, nb, t, curl = stream("bytecodes.zip")
    rd = io.TextIOWrapper(out, encoding="utf-8", newline="")
    next(rd)  # header
    rows, n = [], 0
    t0 = time.time()
    for line in rd:
        line = line.rstrip("\r\n")
        if not line:
            continue
        i = line.index(",")
        hh, bc = line[:i].lower(), line[i + 1:]
        if not bc.startswith("\\x") or len(bc) <= 2:
            n += 1; continue
        code = bytes.fromhex(bc[2:])
        f = flags(code)
        if f["c20_loose"] or f["clone_impl"] or f["proxy_slot"]:
            f["bytecode_hash"] = hh; rows.append(f)
        n += 1
        if n % 100000 == 0:
            print("bytecodes", n, len(rows), f"{time.time()-t0:.0f}s", flush=True)
    t.join()
    pd.DataFrame(rows).to_parquet(C / "zellic_flagged_hashes.parquet")
    rec = dict(file="bytecodes.zip", bytes=nb[0], sha256=h.hexdigest(), n_bytecodes=n, flagged=len(rows), retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), url=BASE + "bytecodes.zip")
    with open(EV / "zellic_files.jsonl", "a") as f: f.write(json.dumps(rec) + "\n")
    print(rec)


def stage2():
    fl = pd.read_parquet(C / "zellic_flagged_hashes.parquet")
    flagged = set(fl.bytecode_hash)
    impls = {x for x in fl.clone_impl.dropna()}
    print("flagged", len(flagged), "impl addrs", len(impls), flush=True)
    out, h, nb, t, curl = stream("contracts.zip")
    rd = io.TextIOWrapper(out, encoding="utf-8", newline="")
    blocks, keep, n = {}, [], 0
    t0 = time.time()
    for chunk in pd.read_csv(rd, chunksize=2_000_000, dtype={"address": str, "bytecode_hash": str, "blocknum": "Int64"}):
        chunk["blocknum"] = chunk.blocknum.fillna(-1).astype("int64")  # a few rows have no block number (kept as -1)
        chunk["address"] = chunk.address.str.lower(); chunk["bytecode_hash"] = chunk.bytecode_hash.str.lower()
        vc = chunk.blocknum.value_counts()
        for b, c in vc.items():
            blocks[b] = blocks.get(b, 0) + c
        # also count non-empty-code creations per block (empty bytecode hash = keccak(''))
        m = (chunk.bytecode_hash.isin(flagged) & (chunk.blocknum >= KEEP_FROM)) | chunk.address.isin(impls)
        keep.append(chunk[m])
        n += len(chunk)
        print("contracts", n, f"{time.time()-t0:.0f}s", flush=True)
    t.join()
    pd.DataFrame(sorted(blocks.items()), columns=["block", "n_rows"]).to_parquet(C / "zellic_blocks.parquet")
    pd.concat(keep).to_parquet(C / "zellic_cand.parquet")
    rec = dict(file="contracts.zip", bytes=nb[0], sha256=h.hexdigest(), n_contracts=n, retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), url=BASE + "contracts.zip")
    with open(EV / "zellic_files.jsonl", "a") as f: f.write(json.dumps(rec) + "\n")
    print(rec)


if __name__ == "__main__":
    {"stage1": stage1, "stage2": stage2}[sys.argv[1] if len(sys.argv) > 1 else "stage1"]()
