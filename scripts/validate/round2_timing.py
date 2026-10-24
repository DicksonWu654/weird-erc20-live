#!/usr/bin/env python3
"""Write the per-token block/time alignment table for round 2."""
import csv, json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; OUT = ROOT / "data/validate/round2"
sys.path.insert(0, str(ROOT / "scripts/validate")); import normalize as N

def main():
    sim = {}
    for l in (OUT / "sim-latest/results.jsonl").open():
        r=json.loads(l); sim[(1, r["token"].lower())] = r
    rows=[]
    for l in (OUT / "subset.jsonl").open():
        r=json.loads(l); c,t=int(r["chain"]),r["token"].lower(); sr=sim.get((c, t))
        hp=N.get_honeypot(c,t); gp=N.get_goplus(c,t)
        rows.append({"chain":c,"token":t,"part":r.get("part"),"stratum":r.get("stratum"),
            "sim_block":sr.get("block") if sr else None,"sim_retrieved_at":sr.get("retrieved_at") if sr else None,
            "sim_raw":((sr.get("evidence") or {}).get("raw") if sr and isinstance(sr.get("evidence"),dict) else None),
            "sim_sha256":((sr.get("evidence") or {}).get("sha256") if sr and isinstance(sr.get("evidence"),dict) else None),
            "honeypot_retrieved_at":hp.get("retrieved_at") if hp else None,"honeypot_raw":hp.get("path") if hp else None,
            "honeypot_sha256":hp.get("sha256") if hp else None,
            "goplus_retrieved_at":gp.get("retrieved_at") if gp else None,"goplus_raw":gp.get("path") if gp else None,
            "goplus_sha256":gp.get("sha256") if gp else None})
    with (OUT / "timing.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print("wrote",len(rows),"timing rows")
if __name__ == "__main__": main()
