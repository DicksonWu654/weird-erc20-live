#!/usr/bin/env python3
"""Recompute 30-day V2 quote peaks from saved Sync and creation-block evidence."""
import hashlib
import json
from pathlib import Path

from rug_second_pass import OUT, ROOT

DEST = OUT / "exact-peaks"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    n = 0
    missing = 0
    for src in sorted((OUT / "sync-peaks").glob("*.json")):
        x = json.loads(src.read_text())
        if not x.get("source"):
            continue
        detail = json.loads((ROOT / x["source"]).read_text())
        sample = detail["sample"]
        block_path = OUT / "creation-blocks" / f"{int(sample['created_block'])}.json"
        head_block = int(sample["screen"]["head"])
        head_path = OUT / "creation-blocks" / f"{head_block - head_block % 2000}.json"
        dest = DEST / src.name
        if not block_path.exists():
            missing += 1
            continue
        block = json.loads(block_path.read_text())
        head = json.loads(head_path.read_text()) if head_path.exists() else {}
        start = block.get("timestamp")
        if start is None or x.get("error") or x.get("truncated"):
            result = {"error": "missing_creation_timestamp_or_incomplete_sync"}
        else:
            quote0 = bool((sample.get("pool_info") or {}).get("quote_is_token0"))
            peak = 0
            point = None
            count = 0
            for env in x.get("raw", []):
                events = (env.get("response") or {}).get("result", [])
                if not isinstance(events, list):
                    continue
                for ev in events:
                    data = ev.get("data", "")
                    if len(data) < 130 or not ev.get("timeStamp"):
                        continue
                    t = int(ev["timeStamp"], 16)
                    if not start <= t <= start + 30*86400:
                        continue
                    q = int(data[2:66], 16) if quote0 else int(data[66:130], 16)
                    loc = (int(ev["blockNumber"], 16), int(ev.get("logIndex") or "0x0", 16))
                    if q > peak:
                        peak, point = q, loc
                    count += 1
            result = {"creation_timestamp": start, "window_end_timestamp": start+30*86400,
                      "head_timestamp": head.get("timestamp"),
                      "peak_quote_30d": peak, "peak_block": point[0] if point else None,
                      "peak_log_index": point[1] if point else None, "n_sync_window": count}
        result.update({"sync_evidence": str(src.relative_to(ROOT)), "sync_sha256": sha(src),
                       "creation_block_evidence": str(block_path.relative_to(ROOT)),
                       "creation_block_sha256": sha(block_path),
                       "head_block_evidence": str(head_path.relative_to(ROOT)) if head_path.exists() else None,
                       "head_block_sha256": sha(head_path) if head_path.exists() else None})
        dest.write_text(json.dumps(result, separators=(",", ":")))
        n += 1
    print(f"exact peaks {n}, missing creation blocks {missing}")


if __name__ == "__main__":
    main()
