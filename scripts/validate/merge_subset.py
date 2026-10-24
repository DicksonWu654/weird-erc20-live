"""Concatenate subset_new_eth + subset_top + subset_trapdoor -> data/validate/subset.jsonl (dedupe on chain+token,
first occurrence wins; order = new tokens first so time-sensitive providers answer them first).
subset_trapdoor.jsonl is derived from the unlicensed Huynh et al. dataset: keep local, do not commit."""
import json
from common import VAL
seen, out = set(), []
for name in ("subset_new_eth", "subset_top", "subset_trapdoor"):
    p = VAL / f"{name}.jsonl"
    if not p.exists():
        continue
    for l in open(p):
        r = json.loads(l)
        k = (r["chain"], r["token"].lower())
        if k not in seen:
            seen.add(k)
            out.append(l if l.endswith("\n") else l + "\n")
open(VAL / "subset.jsonl", "w").writelines(out)
print(len(out), "rows")
