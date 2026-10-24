#!/usr/bin/env python3
"""Golden-set evaluation: expected.csv (what published sources say each token does) vs the harness results.

    .venv/bin/python scripts/probe/golden.py --results data/probe-golden/run/ethereum [--run]

--run first probes every token in expected.csv (grouped by chain) with run.py into <results>.
Writes data/probe-golden/report.md and report.csv. Every mismatch must be explained in
data/probe-golden/mismatch_notes.csv (chain,token,test,verdict,explanation); unexplained mismatches are listed as such.
verdict in: harness_bug | token_changed | source_wrong | harness_limit | note
"""
import argparse
import collections
import csv
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
GOLD = ROOT / "data" / "probe-golden"


def load_expected():
    return list(csv.DictReader(open(GOLD / "expected.csv", newline="")))


def load_results(d):
    out = {}
    p = pathlib.Path(d) / "results.final.jsonl"
    if not p.exists():
        p = pathlib.Path(d) / "results.jsonl"
    for line in p.read_text().splitlines():
        r = json.loads(line)
        out[(r["token"].lower(), r["test"])] = r
    return out


def vstr(v):
    return v if isinstance(v, str) else json.dumps(v, separators=(",", ":"), sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(GOLD / "run"), help="dir containing <chain>/results.jsonl")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--rpc-args", default="", help="extra args for run.py")
    ap.add_argument("--out", type=pathlib.Path, default=GOLD, help="report destination (does not change expectations or inputs)")
    args = ap.parse_args()
    exp = load_expected()
    chains = sorted({e["chain"] for e in exp})
    base = pathlib.Path(args.results)
    if args.run:
        for c in chains:
            toks = sorted({e["token"] for e in exp if e["chain"] == c})
            subprocess.run([sys.executable, str(ROOT / "scripts/probe/run.py"), "--chain", c, "--tokens", ",".join(toks),
                            "--out", str(base / c)] + args.rpc_args.split(), check=True)
    notes = {}
    npath = GOLD / "mismatch_notes.csv"
    if npath.exists():
        for r in csv.DictReader(open(npath, newline="")):
            notes[(r["token"].lower(), r["test"])] = r
    res = {c: load_results(base / c) for c in chains}
    rows = []
    for e in exp:
        r = res[e["chain"]].get((e["token"].lower(), e["test"]))
        obs_status = r["status"] if r else "missing"
        obs_value = vstr(r["value"]) if r else ""
        st_ok = obs_status in e["expect_status"].split("|")
        rx = e["expect_value_regex"]
        val_ok = (re.search(rx, obs_value, re.I) is not None) if rx else True
        match = st_ok and val_ok
        n = notes.get((e["token"].lower(), e["test"]))
        rows.append({**e, "observed_status": obs_status, "observed_value": obs_value[:300], "match": match,
                     "verdict": ("match" if match else (n["verdict"] if n else "UNEXPLAINED")),
                     "explanation": (n["explanation"] if (n and not match) else "")})
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "report.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    # accuracy tables
    by_test = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        by_test[r["test"]][1] += 1
        by_test[r["test"]][0] += r["match"]
    tot_m = sum(r["match"] for r in rows)
    verd = collections.Counter(r["verdict"] for r in rows)
    L = []
    L.append("# Golden-set report: expected (published sources) vs observed (harness)\n")
    L.append(f"Tokens: {len({(r['chain'], r['token'].lower()) for r in rows})}; expectations: {len(rows)}; "
             f"exact match (status and value pattern): **{tot_m}/{len(rows)} = {100 * tot_m / len(rows):.1f}%**.\n")
    L.append("Mismatch verdicts: " + ", ".join(f"{k}={v}" for k, v in sorted(verd.items())) + "\n")
    by_phase = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        by_phase[r.get("phase", "")][1] += 1
        by_phase[r.get("phase", "")][0] += r["match"]
    L.append("\n## Agreement by expectation phase\n\n| phase | matched | total |\n|---|---|---|")
    for p_, (m, n) in sorted(by_phase.items()):
        L.append(f"| {p_} | {m} | {n} |")
    L.append("\n## Agreement by test\n\n| test | matched | total |\n|---|---|---|")
    for t, (m, n) in sorted(by_test.items()):
        L.append(f"| {t} | {m} | {n} |")
    L.append("\n## Every expectation\n\n| token | test | expected | observed | verdict |\n|---|---|---|---|---|")
    for r in rows:
        exp_s = r["expect_status"] + (f" /{r['expect_value_regex']}/" if r["expect_value_regex"] else "")
        obs_s = f"{r['observed_status']} {r['observed_value'][:90]}".replace("|", "\\|")
        L.append(f"| {r['symbol']} `{r['token'][:10]}` | {r['test']} | {exp_s.replace('|', chr(92) + '|')} | {obs_s} | {r['verdict']} |")
    L.append("\n## Mismatches and explanations\n")
    for r in rows:
        if not r["match"]:
            L.append(f"- **{r['symbol']} {r['token']} / {r['test']}**: expected `{r['expect_status']}` `{r['expect_value_regex']}`"
                     f" (source: {r['source']}); observed `{r['observed_status']}` `{r['observed_value'][:160]}`. "
                     f"**{r['verdict']}**: {r['explanation'] or 'no explanation recorded yet'}")
    (args.out / "report.md").write_text("\n".join(L) + "\n")
    print(f"{tot_m}/{len(rows)} match; verdicts {dict(verd)}")


if __name__ == "__main__":
    main()
