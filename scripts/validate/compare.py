#!/usr/bin/env python
"""Agreement analysis for the Weird ERC-20 validation (W6).

Subcommands
  sim        our simulator JSONL  vs honeypot.is and vs GoPlus   -> "agrees with X% (n=..., 95% CI)"
  providers  honeypot.is vs GoPlus (provider-to-provider baseline), no simulator needed
  tier1      our Tier-1 / control results JSONL vs GoPlus token-security fields (bool + owner address agreement)

Sim row schema (W5, scripts/sim): {token, pool, block, buy{ok,tax}, sell{ok,tax,revert_reason}, flags[], primary_label}
  (+ optional chain; default --chain 1, or inferred from the subset by token address).  --sim-tax-unit fraction|percent
Provider answers come from the raw cache (data/raw/validate/**), normalized by normalize.py; by default the LATEST
usable envelope per token is used, or --at <ISO time> picks the one closest to that moment (for drift control).

Outputs (data/validate/reports/ by default): report_<mode>.md + report_<mode>.json (metrics, confusion matrices, coverage /
exclusion counts), disagreements_<mode>_<task>.csv (links to saved raw evidence on both sides + blank `adjudication`
column).  Metrics: agreement, Cohen's kappa, percentile bootstrap 95% CI (resampling tokens, --boot 2000, seeded),
Wilson 95% CI for agreement, per-label positive/negative agreement.  Excluded rows are never silently dropped: every
task reports how many tokens were excluded and why.
"""
import argparse, calendar, csv, json, math, sys, time
from collections import Counter, defaultdict
import numpy as np
from common import ROOT, VAL, RAW
import normalize as N

OUT = VAL / "reports"
CHAIN_SLUG = {1: "ethereum", 56: "bsc", 8453: "base"}
HIGH_TAX_PCT = 10.0
OWNER_FIELDS_GP = ["is_mintable", "is_blacklisted", "slippage_modifiable", "personal_slippage_modifiable",
                   "owner_change_balance", "hidden_owner", "can_take_back_ownership", "transfer_pausable"]
ZERO = "0x0000000000000000000000000000000000000000"


# ----------------------------------------------------------------------------- metrics
def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def kappa_idx(a, b, k):
    """a,b int arrays in [0,k). Returns Cohen's kappa or nan."""
    n = len(a)
    if n == 0:
        return float("nan")
    po = np.mean(a == b)
    ca = np.bincount(a, minlength=k) / n
    cb = np.bincount(b, minlength=k) / n
    pe = float(np.dot(ca, cb))
    return float("nan") if pe >= 1 else float((po - pe) / (1 - pe))


def metrics(pairs, labels=None, boot=2000, seed=7):
    """pairs: list of (labelA, labelB). Returns dict with confusion matrix and CIs."""
    n = len(pairs)
    labels = labels or sorted({x for p in pairs for x in p})
    ix = {l: i for i, l in enumerate(labels)}
    k = len(labels)
    cm = np.zeros((k, k), int)
    for x, y in pairs:
        cm[ix[x], ix[y]] += 1
    out = {"n": n, "labels": labels, "confusion_rowsA_colsB": cm.tolist()}
    if n == 0:
        return {**out, "agreement": None, "kappa": None}
    a = np.array([ix[x] for x, _ in pairs])
    b = np.array([ix[y] for _, y in pairs])
    agree = float(np.mean(a == b))
    kap = kappa_idx(a, b, k)
    rng = np.random.default_rng(seed)
    ag_b, kp_b = [], []
    for _ in range(boot):
        s = rng.integers(0, n, n)
        ag_b.append(np.mean(a[s] == b[s]))
        kp_b.append(kappa_idx(a[s], b[s], k))
    kp_b = [x for x in kp_b if not math.isnan(x)]
    pct = lambda v: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] if len(v) > 20 else [None, None]
    lo, hi = wilson(int(round(agree * n)), n)
    out.update(agreement=agree, agreement_ci95_boot=pct(ag_b), agreement_ci95_wilson=[lo, hi],
               kappa=None if math.isnan(kap) else kap, kappa_ci95_boot=pct(kp_b))
    per = {}
    for l in labels:
        i = ix[l]
        tp = int(cm[i, i]); fa = int(cm[i].sum() - tp); fb = int(cm[:, i].sum() - tp)
        tn = int(n - tp - fa - fb)
        per[l] = {"both": tp, "only_A": fa, "only_B": fb, "neither": tn,
                  "positive_agreement": (2 * tp / (2 * tp + fa + fb)) if (2 * tp + fa + fb) else None,
                  "negative_agreement": (2 * tn / (2 * tn + fa + fb)) if (2 * tn + fa + fb) else None}
    out["per_label"] = per
    return out


# ----------------------------------------------------------------------------- views
def _tax_pct(x, unit):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x * 100 if unit == "fraction" else x


def sim_view(row, unit, thr):
    """W5 results.jsonl row (scripts/sim/README.md): primary_label, taxes{buy,sell,transfer,sell_max} (fractions),
    buy{ok,tax}, sells[], unprobeable (reason or null), evidence{raw,sha256}.  Older flat schema
    {buy{ok,tax}, sell{ok,tax}} is also accepted."""
    lab = (row.get("primary_label") or "").strip().lower().replace("-", "_").replace(" ", "_")
    buy = row.get("buy") or {}
    sells = row.get("sells") or []
    flat_sell = row.get("sell") or {}
    taxes = row.get("taxes") or {}
    bt = _tax_pct(taxes.get("buy", buy.get("tax")), unit)
    st = _tax_pct(taxes.get("sell", flat_sell.get("tax")), unit)
    sell_ok = (any(x.get("ok") for x in sells) if sells else flat_sell.get("ok"))
    v = {"raw_label": lab, "why": lab if lab != "unprobeable" else "unprobeable:" + str(row.get("unprobeable") or "?").split(":")[0],
         "buy_tax": bt, "sell_tax": st, "buy_ok": buy.get("ok"), "sell_ok": sell_ok,
         "flags": row.get("flags") or [], "label_primary": lab}
    determinable = lab in ("honeypot", "high_tax", "owner_controlled", "normal", "rugged")
    v["covered"] = determinable
    v["honeypot"] = (lab == "honeypot") if determinable and lab != "rugged" else None
    taxes_known = bt is not None and st is not None and buy.get("ok") is not False
    v["high_tax"] = (max(bt, st) >= thr) if taxes_known else None
    if lab == "honeypot":
        v["label3"] = "honeypot"
    elif lab in ("high_tax", "owner_controlled", "normal"):
        v["label3"] = "high_tax" if lab == "high_tax" else "benign"
    else:
        v["label3"] = None
    v["label4"] = lab if lab in ("honeypot", "high_tax", "owner_controlled", "normal") else None
    v["label_full"] = lab or None
    ev = row.get("evidence")
    v["evidence"] = ev.get("raw") if isinstance(ev, dict) else ev
    return v


def hp_view(n, thr):
    if n is None:
        return {"covered": False, "why": "not_queried"}
    v = {"covered": n["coverage"] in ("ok", "sim_failed"), "why": n["coverage"], "buy_tax": n["buy_tax"],
         "sell_tax": n["sell_tax"], "honeypot": n["is_honeypot"], "norm": n}
    tx = n["buy_tax"] is not None and n["sell_tax"] is not None
    v["high_tax"] = (max(n["buy_tax"], n["sell_tax"]) >= thr) if tx else None
    if n["is_honeypot"]:
        v["label3"] = "honeypot"
    elif n["is_honeypot"] is False and tx:
        v["label3"] = "high_tax" if v["high_tax"] else "benign"
    else:
        v["label3"] = None
    v["label4"] = None  # honeypot.is exposes no owner-control fields
    v["label_full"] = ("honeypot" if n["is_honeypot"] else "high_tax" if v["label3"] == "high_tax"
                       else "benign" if v["label3"] == "benign" else
                       ("buy_failed" if n.get("buy_failed") else "no_liquidity" if n.get("no_liquidity") else
                        "no_simulation" if n["coverage"] == "sim_failed" else None))
    return v


def gp_view(n, thr, owner_active=False):
    if n is None:
        return {"covered": False, "why": "not_queried"}
    v = {"covered": n["coverage"] == "ok", "why": n["coverage"], "buy_tax": n["buy_tax"], "sell_tax": n["sell_tax"],
         "honeypot": n["is_honeypot"], "norm": n}
    tx = n["buy_tax"] is not None and n["sell_tax"] is not None
    v["high_tax"] = (max(n["buy_tax"], n["sell_tax"]) >= thr) if tx else None
    own = None
    if n["coverage"] == "ok":
        vals = [n.get(f) for f in OWNER_FIELDS_GP]
        if any(x for x in vals):
            own = True
        elif all(x is not None for x in vals):
            own = False
        if own and owner_active:
            oa = (n.get("owner_address") or "").lower()
            own = bool(oa) and oa != ZERO
    v["owner_controlled"] = own
    if n["is_honeypot"]:
        v["label3"] = "honeypot"
    elif n["is_honeypot"] is False and tx:
        v["label3"] = "high_tax" if v["high_tax"] else "benign"
    else:
        v["label3"] = None
    if v["label3"] == "honeypot":
        v["label4"] = "honeypot"
    elif v["label3"] == "high_tax":
        v["label4"] = "high_tax"
    elif v["label3"] == "benign" and own is not None:
        v["label4"] = "owner_controlled" if own else "normal"
    else:
        v["label4"] = None
    nodex = n.get("is_in_dex") is False
    v["label_full"] = ("no_dex" if nodex and v["label3"] is None else v["label4"] or v["label3"])
    return v


# ----------------------------------------------------------------------------- links
def links(chain, tok):
    hp = f"https://honeypot.is/{CHAIN_SLUG[chain]}?address={tok}" if chain in CHAIN_SLUG else ""
    return hp, f"https://gopluslabs.io/token-security/{chain}/{tok}"


# ----------------------------------------------------------------------------- loading
def load_subset():
    p = VAL / "subset.jsonl"
    rows = [json.loads(l) for l in open(p)] if p.exists() else []
    return {(r["chain"], r["token"].lower()): r for r in rows}


def iso_to_ms(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000


def filter_keys(keys, subset, a):
    inc = set(a.strata.split(",")) if getattr(a, "strata", None) else None
    exc = set(a.exclude_strata.split(",")) if getattr(a, "exclude_strata", None) else set()
    out = []
    for k in keys:
        st = (subset.get(k) or {}).get("stratum", "unknown")
        if (inc is None or st in inc) and st not in exc:
            out.append(k)
    return out


def provider_views(keys, thr, at_ms, owner_active):
    hp, gp = {}, {}
    for (c, t) in keys:
        hp[(c, t)] = hp_view(N.get_honeypot(c, t, at_ms) if c in (1, 56, 8453) else
                             {"coverage": "unsupported_chain", "is_honeypot": None, "buy_tax": None, "sell_tax": None,
                              "path": None, "retrieved_at": None, "http_status": None}, thr)
        gp[(c, t)] = gp_view(N.get_goplus(c, t, at_ms), thr, owner_active)
    return hp, gp


# ----------------------------------------------------------------------------- tasks
def run_task(name, keys, A, B, nameA, nameB, field, meta, boot, disagree_rows, labels=None, binary=None, extra=None):
    """field: key in the views. Compare A[k][field] vs B[k][field] where both are not None."""
    used, excl = [], Counter()
    for k in keys:
        a, b = A.get(k), B.get(k)
        if a is None:
            excl[f"{nameA}:missing"] += 1; continue
        if not a.get("covered") and a.get("covered") is not None and field != "tax":
            excl[f"{nameA}:{a.get('why') or 'not_covered'}"] += 1; continue
        if not b.get("covered"):
            excl[f"{nameB}:{b.get('why') or 'not_covered'}"] += 1; continue
        x, y = a.get(field), b.get(field)
        if x is None:
            excl[f"{nameA}:{field}_undetermined"] += 1; continue
        if y is None:
            excl[f"{nameB}:{field}_undetermined"] += 1; continue
        if binary:
            x, y = ("yes" if x else "no"), ("yes" if y else "no")
        used.append((k, x, y))
    m = metrics([(x, y) for _, x, y in used], labels=labels or (["no", "yes"] if binary else None), boot=boot)
    m.update(task=name, A=nameA, B=nameB, universe=len(keys), excluded=dict(excl), n_excluded=len(keys) - len(used))
    for k, x, y in used:
        if x != y:
            disagree_rows.append((name, k, x, y))
    return m, used


def tax_task(keys, A, B, nameA, nameB, tol_pp=1.0):
    """Numeric tax agreement: share with |dBuy|<=tol and |dSell|<=tol, MAE, among tokens where all four taxes known."""
    d = []
    for k in keys:
        a, b = A.get(k), B.get(k)
        if not a or not b or not a.get("covered") or not b.get("covered"):
            continue
        if None in (a.get("buy_tax"), a.get("sell_tax"), b.get("buy_tax"), b.get("sell_tax")):
            continue
        d.append((k, a["buy_tax"] - b["buy_tax"], a["sell_tax"] - b["sell_tax"]))
    if not d:
        return {"n": 0}
    arr = np.array([[x[1], x[2]] for x in d])
    within = np.mean((np.abs(arr[:, 0]) <= tol_pp) & (np.abs(arr[:, 1]) <= tol_pp))
    return {"n": len(d), "tolerance_pp": tol_pp, "share_within_tol_buy_and_sell": float(within),
            "wilson95": wilson(int(round(within * len(d))), len(d)),
            "mae_buy_pp": float(np.mean(np.abs(arr[:, 0]))), "mae_sell_pp": float(np.mean(np.abs(arr[:, 1]))),
            "median_abs_sell_diff_pp": float(np.median(np.abs(arr[:, 1]))),
            "n_taxed_either_gt_1pp": int(np.sum((np.abs(arr) > 1).any(axis=1)))}


def write_disagreements(mode, disagree_rows, A, B, nameA, nameB, subset, sim_rows):
    by_task = defaultdict(list)
    for t, k, x, y in disagree_rows:
        by_task[t].append((k, x, y))
    OUT.mkdir(parents=True, exist_ok=True)
    for t, lst in by_task.items():
        p = OUT / f"disagreements_{mode}_{t}.csv"
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["chain", "token", "stratum", "pool", f"{nameA}_value", f"{nameB}_value", f"{nameA}_label_full",
                        f"{nameB}_label_full", f"{nameA}_buy_tax_pct", f"{nameA}_sell_tax_pct", f"{nameB}_buy_tax_pct",
                        f"{nameB}_sell_tax_pct", "sim_evidence", "honeypot_raw", "goplus_raw", "honeypot_is_url",
                        "goplus_url", "honeypot_retrieved_at", "goplus_retrieved_at", "adjudication", "notes"])
            for (c, tok), x, y in lst:
                a, b = A[(c, tok)], B[(c, tok)]
                hpn = (a if nameA == "honeypot" else b if nameB == "honeypot" else {}).get("norm") or {}
                gpn = (a if nameA == "goplus" else b if nameB == "goplus" else {}).get("norm") or {}
                sr = sim_rows.get((c, tok), {}) if sim_rows else {}
                hu, gu = links(c, tok)
                w.writerow([c, tok, (subset.get((c, tok)) or {}).get("stratum", ""), sr.get("pool") or (subset.get((c, tok)) or {}).get("pool", ""),
                            x, y, a.get("label_full"), b.get("label_full"), a.get("buy_tax"), a.get("sell_tax"),
                            b.get("buy_tax"), b.get("sell_tax"),
                            ((sr.get("evidence") or {}).get("raw") if isinstance(sr.get("evidence"), dict) else (sr.get("evidence") or "")),
                            hpn.get("path", ""), gpn.get("path", ""), hu, gu, hpn.get("retrieved_at", ""),
                            gpn.get("retrieved_at", ""), "", ""])


def fmt(m):
    if not m.get("n"):
        return "n=0"
    ci = m.get("agreement_ci95_boot") or [None, None]
    k = m.get("kappa")
    kc = m.get("kappa_ci95_boot") or [None, None]
    f = lambda x: "NA" if x is None else f"{x:.3f}"
    return (f"agreement {100 * m['agreement']:.1f}% (n={m['n']}, 95% CI {f(ci[0])}-{f(ci[1])}; Wilson "
            f"{f(m['agreement_ci95_wilson'][0])}-{f(m['agreement_ci95_wilson'][1])}); kappa {f(k)} "
            f"(95% CI {f(kc[0])}-{f(kc[1])})")


def md_cm(m):
    L = m["labels"]
    lines = ["| %s \\ %s | " % (m["A"], m["B"]) + " | ".join(L) + " |", "|---|" + "---|" * len(L)]
    for i, l in enumerate(L):
        lines.append(f"| {l} | " + " | ".join(str(x) for x in m["confusion_rowsA_colsB"][i]) + " |")
    return "\n".join(lines)


def report(mode, results, header):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"report_{mode}.json").write_text(json.dumps(results, indent=1, default=str))
    L = [f"# Validation report: {mode}", "", header, ""]
    for r in results["tasks"]:
        L += [f"## {r['title']}", "", fmt(r) if r.get("kind") != "tax" else json.dumps(r), ""]
        if r.get("kind") != "tax" and r.get("n"):
            L += [md_cm(r), ""]
            if r.get("per_label"):
                L += ["| label | both | only " + r["A"] + " | only " + r["B"] + " | PPA | NPA |", "|---|---|---|---|---|---|"]
                for l, p in r["per_label"].items():
                    g = lambda x: "NA" if x is None else f"{x:.2f}"
                    L.append(f"| {l} | {p['both']} | {p['only_A']} | {p['only_B']} | {g(p['positive_agreement'])} | {g(p['negative_agreement'])} |")
                L.append("")
        if r.get("excluded"):
            L += [f"Excluded {r['n_excluded']} of {r['universe']}: " + ", ".join(f"{k}={v}" for k, v in sorted(r['excluded'].items())), ""]
    (OUT / f"report_{mode}.md").write_text("\n".join(L))
    print("\n".join(L))


# ----------------------------------------------------------------------------- modes
def _tag(a):
    return ("_" + a.tag) if getattr(a, "tag", "") else ""


def sim_mode(a):
    thr = a.tax_threshold
    subset = load_subset()
    sim_rows = {}
    for line in open(a.sim):
        if not line.strip():
            continue
        r = json.loads(line)
        tok = (r.get("token") or "").lower()
        chain = r.get("chain") or r.get("chain_id")
        if chain is None:
            ch = [c for (c, t) in subset if t == tok]
            chain = ch[0] if len(ch) == 1 else a.chain
        sim_rows[(int(chain), tok)] = r
    keys = filter_keys(sorted(sim_rows), subset, a)
    at_ms = iso_to_ms(a.at) if a.at else None
    hp, gp = provider_views(keys, thr, at_ms, a.owner_active)
    S = {k: sim_view(r, a.sim_tax_unit, thr) for k, r in sim_rows.items()}
    return run_modes("sim" + _tag(a), keys, S, hp, gp, subset, sim_rows, a, thr)


def providers_mode(a):
    thr = a.tax_threshold
    subset = load_subset()
    keys = filter_keys(sorted(subset), subset, a)
    at_ms = iso_to_ms(a.at) if a.at else None
    hp, gp = provider_views(keys, thr, at_ms, a.owner_active)
    return providers_run(keys, hp, gp, subset, a, thr)


def run_modes(mode, keys, S, hp, gp, subset, sim_rows, a, thr):
    tasks, dis = [], []
    t0 = time.time()
    for pname, P in (("honeypot", hp), ("goplus", gp)):
        for tname, field, binary, labels, title in (
                ("honeypot", "honeypot", True, None, f"sim vs {pname}: honeypot (binary)"),
                ("high_tax", "high_tax", True, None, f"sim vs {pname}: high tax >= {thr:g}% (binary, from tax values)"),
                ("label3", "label3", False, ["benign", "high_tax", "honeypot"], f"sim vs {pname}: 3-class (benign/high_tax/honeypot)")):
            m, _ = run_task(f"{pname}_{tname}", keys, S, P, "sim", pname, field, subset, a.boot, dis, labels, binary)
            m["title"] = title
            tasks.append(m)
        tx = tax_task(keys, S, P, "sim", pname)
        tasks.append({"kind": "tax", "title": f"sim vs {pname}: tax value agreement", **tx})
    m, _ = run_task("goplus_label4", keys, S, gp, "sim", "goplus", "label4", subset, a.boot, dis,
                    ["normal", "owner_controlled", "high_tax", "honeypot"],
                    None)
    m["title"] = "sim vs goplus: 4-class incl. owner_controlled (GoPlus-derived, see mapping.md)"
    tasks.append(m)
    # slices
    for field in ("stratum", "chain"):
        groups = defaultdict(list)
        for k in keys:
            groups[(subset.get(k) or {}).get(field, k[0] if field == "chain" else "unknown")].append(k)
        for g, ks in sorted(groups.items(), key=lambda x: str(x[0])):
            for pname, P in (("honeypot", hp), ("goplus", gp)):
                m, _ = run_task(f"slice_{field}_{g}_{pname}_honeypot", ks, S, P, "sim", pname, "honeypot", subset, a.boot, [], None, True)
                m["title"] = f"[slice {field}={g}] sim vs {pname}: honeypot (binary)"
                tasks.append(m)
    # tax threshold sensitivity (high-tax)
    # full crosstab of sim primary label x provider label_full
    cross = {}
    for pname, P in (("honeypot", hp), ("goplus", gp)):
        c = Counter()
        for k in keys:
            c[(S[k].get("label_full") or "none", (P[k].get("label_full") if P.get(k) else None) or f"unavailable:{P[k].get('why')}")] += 1
        cross[pname] = {f"{x} | {y}": n for (x, y), n in sorted(c.items())}
    res = {"mode": mode, "tax_threshold_pct": thr, "n_tokens": len(keys), "tasks": tasks, "crosstab_sim_label_x_provider_label": cross}
    # per-provider disagreement files
    for pname, P in (("honeypot", hp), ("goplus", gp)):
        d2 = [(t, k, x, y) for t, k, x, y in dis if t.startswith(pname)]
        write_disagreements(mode, d2, S, P, "sim", pname, subset, sim_rows)
    report(mode, res, f"Tokens: {len(keys)}. Provider answers: " + (f"nearest to {a.at}" if a.at else "latest usable envelope") +
           f". High-tax threshold {thr:g}%. Sim tax unit: {a.sim_tax_unit}.")


def providers_run(keys, hp, gp, subset, a, thr):
    tasks, dis = [], []
    for tname, field, binary, labels, title in (
            ("honeypot", "honeypot", True, None, "honeypot.is vs GoPlus: honeypot (binary)"),
            ("high_tax", "high_tax", True, None, f"honeypot.is vs GoPlus: high tax >= {thr:g}% (binary)"),
            ("label3", "label3", False, ["benign", "high_tax", "honeypot"], "honeypot.is vs GoPlus: 3-class")):
        m, _ = run_task(tname, keys, hp, gp, "honeypot", "goplus", field, subset, a.boot, dis, labels, binary)
        m["title"] = title
        tasks.append(m)
    for t in (5.0, 10.0, 20.0, 50.0):
        hp2 = {k: dict(v, high_tax=(None if v.get("buy_tax") is None or v.get("sell_tax") is None else max(v["buy_tax"], v["sell_tax"]) >= t)) for k, v in hp.items()}
        gp2 = {k: dict(v, high_tax=(None if v.get("buy_tax") is None or v.get("sell_tax") is None else max(v["buy_tax"], v["sell_tax"]) >= t)) for k, v in gp.items()}
        m, _ = run_task(f"high_tax_{t:g}", keys, hp2, gp2, "honeypot", "goplus", "high_tax", subset, a.boot, [], None, True)
        m["title"] = f"sensitivity: high tax >= {t:g}%"
        tasks.append(m)
    tasks.append({"kind": "tax", "title": "honeypot.is vs GoPlus: tax value agreement", **tax_task(keys, hp, gp, "honeypot", "goplus")})
    for field in ("stratum", "chain"):
        groups = defaultdict(list)
        for k in keys:
            groups[(subset.get(k) or {}).get(field, k[0] if field == "chain" else "unknown")].append(k)
        for g, ks in sorted(groups.items(), key=lambda x: str(x[0])):
            m, _ = run_task(f"slice_{field}_{g}_honeypot", ks, hp, gp, "honeypot", "goplus", "honeypot", subset, a.boot, [], None, True)
            m["title"] = f"[slice {field}={g}] honeypot.is vs GoPlus: honeypot (binary)"
            tasks.append(m)
    cov = Counter()
    for k in keys:
        cov[(k[0], hp[k].get("why"), gp[k].get("why"))] += 1
    res = {"mode": "providers", "n_tokens": len(keys), "tasks": tasks,
           "coverage_chain_honeypotis_goplus": {f"{c} | hp={h} | gp={g}": n for (c, h, g), n in sorted(cov.items(), key=str)}}
    write_disagreements("providers" + _tag(a), dis, hp, gp, "honeypot", "goplus", subset, None)
    report("providers" + _tag(a), res, f"Tokens in subset: {len(keys)}. Provider answers: " + (f"nearest to {a.at}" if a.at else "latest usable envelope"))


# ----------------------------------------------------------------------------- tier1 vs GoPlus
DEFAULT_T1_MAP = {
    # our test name(s) -> GoPlus field, kind. Edit data/validate/tier1_map.json (or --map) once W1/W3 schemas settle.
    "is_proxy": {"aliases": ["is_proxy", "proxy", "upgradeable", "is_upgradeable"], "goplus": "is_proxy", "kind": "bool"},
    "transfer_pausable": {"aliases": ["transfer_pausable", "pausable", "is_pausable", "paused_transfers"], "goplus": "transfer_pausable", "kind": "bool"},
    "is_blacklisted": {"aliases": ["is_blacklisted", "blacklist", "has_blacklist", "blocklist", "has_blocklist"], "goplus": "is_blacklisted", "kind": "bool"},
    "trading_cooldown": {"aliases": ["trading_cooldown", "cooldown"], "goplus": "trading_cooldown", "kind": "bool"},
    "is_mintable": {"aliases": ["is_mintable", "mintable", "has_mint"], "goplus": "is_mintable", "kind": "bool"},
    "owner_address": {"aliases": ["owner_address", "owner"], "goplus": "owner_address", "kind": "address"},
    "selfdestruct": {"aliases": ["selfdestruct"], "goplus": "selfdestruct", "kind": "bool"},
    "owner_change_balance": {"aliases": ["owner_change_balance"], "goplus": "owner_change_balance", "kind": "bool"},
    "hidden_owner": {"aliases": ["hidden_owner"], "goplus": "hidden_owner", "kind": "bool"},
}
TRUE_S = {"true", "yes", "1", "present", "quirk", "paused", "pausable", "proxy", "upgradeable"}
FALSE_S = {"false", "no", "0", "absent", "none", "pass", "not_proxy", "immutable"}


def to_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in TRUE_S:
            return True
        if s in FALSE_S:
            return False
    if isinstance(v, dict):
        for k in ("value", "present", "is_proxy", "enabled", "result"):
            if k in v:
                return to_bool(v[k])
    return None


def to_addr(v):
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("address") or v.get("owner") or v.get("value")
    if v is None:
        return "none"
    s = str(v).strip().lower()
    return "none" if s in ("", ZERO, "none", "renounced", "null") else s


CHAIN_IDS = {"ethereum": 1, "eth": 1, "bsc": 56, "base": 8453, "arbitrum": 42161, "polygon": 137, "optimism": 10}

# W1 probe tests (scripts/probe README): status quirk/fail = the machinery is present (fail on `pause` = paused now).
PROBE_MAP = {  # test -> (goplus field, note)
    "pause": ("transfer_pausable", "exact concept: token can be paused"),
    "blocklist": ("is_blacklisted", "exact concept: blocklist machinery present"),
    "same_block_repeat": ("trading_cooldown", "approximate: second transfer in same block reverts"),
    "anti_bot_limits": ("is_anti_whale", "approximate: max-tx/max-wallet/delay accessors present"),
    "fee_parameters": ("slippage_modifiable", "approximate: fee/tax accessor exists (may be immutable)"),
}
# W3 control rows: capability_hints are selector-presence labels
CONTROL_HINTS = {"mint": "is_mintable", "pause": "transfer_pausable", "blacklist": "is_blacklisted",
                 "fee_or_tax_setter": "slippage_modifiable"}


def _chain_id(c):
    return int(c) if str(c).isdigit() else CHAIN_IDS[str(c).lower()]


def obs_from_probe(path):
    """W1 results.jsonl -> observations (source, key, goplus_field, kind, value|None, reason, evidence, chain, token)."""
    out = []
    for l in open(path):
        if not l.strip():
            continue
        r = json.loads(l)
        if r["test"] not in PROBE_MAP:
            continue
        f, _ = PROBE_MAP[r["test"]]
        st = r.get("status")
        out.append(("probe", r["test"], f, "bool", None if st in ("unprobeable", "error") else st in ("quirk", "fail"),
                    f"ours:{st}" if st in ("unprobeable", "error") else None, r.get("evidence"),
                    _chain_id(r["chain"]), r["token"].lower()))
    return out


def obs_from_control(path):
    out = []
    for l in open(path):
        if not l.strip():
            continue
        r = json.loads(l)
        c, t = _chain_id(r["chain"]), r["token"].lower()
        ev = (r.get("evidence") or [None])[0]
        if r.get("status") != "ok":
            continue
        out.append(("control", "is_proxy", "is_proxy", "bool", r.get("is_proxy"),
                    None if r.get("is_proxy") is not None else "ours:is_proxy_null", ev, c, t))
        hints = r.get("capability_hints") or {}
        for h, f in CONTROL_HINTS.items():
            out.append(("control", "hint:" + h, f, "bool", h in hints,
                        "ours:partial_probe" if r.get("completeness") != "complete" else None, ev, c, t))
        if (r.get("code") or {}).get("has_selfdestruct") is not None:
            out.append(("control", "has_selfdestruct", "selfdestruct", "bool", bool(r["code"]["has_selfdestruct"]), None, ev, c, t))
        addrs = [p["addresses"][0] for p in (r.get("owner_paths") or []) if p.get("addresses")]
        kinds = r.get("owner_terminal_kinds") or []
        ow = addrs[0].lower() if addrs else ("none" if (not r.get("owner_chain") or "renounced" in kinds) else None)
        out.append(("control", "owner", "owner_address", "address", ow, None if ow else "ours:owner_unclear", ev, c, t))
    return out


def tier1_mode(a):
    obs = []
    for p in a.probe or []:
        obs += obs_from_probe(p)
    for p in a.control or []:
        obs += obs_from_control(p)
    for p in a.results or []:  # generic PLAN-schema rows with alias mapping
        tmap = json.load(open(a.map)) if a.map else DEFAULT_T1_MAP
        alias = {al: (k, spec) for k, spec in tmap.items() for al in spec["aliases"]}
        for l in open(p):
            if not l.strip():
                continue
            r = json.loads(l)
            if (r.get("test") or "").lower() not in alias:
                continue
            key, spec = alias[r["test"].lower()]
            v = to_bool(r.get("value")) if spec["kind"] == "bool" else to_addr(r.get("value"))
            obs.append(("generic", key, spec["goplus"], spec["kind"], v, None if v is not None else "ours:undetermined",
                        r.get("evidence"), _chain_id(r.get("chain", a.chain)), r["token"].lower()))
    at_ms = iso_to_ms(a.at) if a.at else None
    subset = load_subset()
    gp_cache, per, excl, dis = {}, defaultdict(list), defaultdict(Counter), []
    for src, name, gfield, kind, ov, why, ev, chain, tok in obs:
        if a.subset_only and (chain, tok) not in subset:
            continue
        key = f"{src}:{name}~{gfield}"
        if (chain, tok) not in gp_cache:
            gp_cache[(chain, tok)] = gp_view(N.get_goplus(chain, tok, at_ms), HIGH_TAX_PCT)
        g = gp_cache[(chain, tok)]
        if not g["covered"]:
            excl[key][f"goplus:{g.get('why')}"] += 1; continue
        if ov is None:
            excl[key][why or "ours:undetermined"] += 1; continue
        gv = g["norm"].get(gfield)
        if kind == "bool":
            if gv is None:
                excl[key]["goplus:field_unknown"] += 1; continue
            per[key].append(("yes" if ov else "no", "yes" if gv else "no", (chain, tok, ev, ov, gv)))
        else:
            if not g["norm"].get("owner_address_present"):
                excl[key]["goplus:owner_unknown"] += 1; continue
            oo, gg = to_addr(ov), to_addr(gv)
            per[key].append(("none" if oo == "none" else "addr", "none" if gg == "none" else "addr", (chain, tok, ev, oo, gg)))
            if oo != "none" and gg != "none":
                per[key + ":exact"].append(("same" if oo == gg else "different", "same", (chain, tok, ev, oo, gg)))
    tasks = []
    for key, lst in sorted(per.items()):
        labels = ["no", "yes"] if "address" not in key and "owner" not in key else (["different", "same"] if key.endswith(":exact") else ["none", "addr"])
        m = metrics([(x, y) for x, y, _ in lst], labels, boot=a.boot)
        base = key.replace(":exact", "")
        m.update(task=key, A="ours", B="goplus", title=f"Tier-1/control vs GoPlus: {key}", universe=len(lst) + sum(excl.get(base, {}).values()),
                 excluded=dict(excl.get(base, {})), n_excluded=sum(excl.get(base, {}).values()))
        tasks.append(m)
        for x, y, (c, t, ev, ox, gy) in lst:
            if x != y:
                dis.append((key, c, t, ev, ox, gy))
    OUT.mkdir(parents=True, exist_ok=True)
    tag = _tag(a)
    with open(OUT / f"disagreements_tier1{tag}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["field", "chain", "token", "ours", "goplus", "our_evidence", "goplus_raw", "goplus_url", "adjudication", "notes"])
        for key, c, t, ev, ox, gy in dis:
            g = gp_cache[(c, t)]["norm"]
            w.writerow([key, c, t, ox, gy, ev, g.get("path"), links(c, t)[1], "", ""])
    only_empty = [k for k, v in excl.items() if k not in per]
    report("tier1" + tag, {"mode": "tier1", "tasks": tasks, "fully_excluded": {k: dict(excl[k]) for k in only_empty}},
           f"Observations: {len(obs)}; compared tokens with GoPlus answers: {len(gp_cache)}. Fields with every row excluded: {only_empty}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    for n in ("sim", "providers", "tier1"):
        p = sub.add_parser(n)
        p.add_argument("--boot", type=int, default=2000)
        p.add_argument("--at", help="ISO UTC time: use provider answers closest to it (default: latest)")
        p.add_argument("--chain", type=int, default=1)
        p.add_argument("--tax-threshold", type=float, default=HIGH_TAX_PCT, help="percent; high-tax if max(buy,sell) >= it")
        p.add_argument("--strata", help="comma list of subset strata to keep (e.g. liquid,thin,dust,top); default all")
        p.add_argument("--exclude-strata", help="comma list of strata to drop (e.g. td_trapdoor,td_nonmal)")
        p.add_argument("--tag", default="", help="suffix for output file names")
        p.add_argument("--owner-active", action="store_true", help="GoPlus owner_controlled requires non-zero owner_address")
        if n == "sim":
            p.add_argument("sim")
            p.add_argument("--sim-tax-unit", choices=["fraction", "percent"], default="fraction")
        if n == "tier1":
            p.add_argument("--probe", nargs="*", help="W1 data/probe/<chain>/results.jsonl")
            p.add_argument("--control", nargs="*", help="W3 data/control/<chain>/control.jsonl")
            p.add_argument("--results", nargs="*", help="generic {chain,token,test,status,value,evidence} rows")
            p.add_argument("--map", help="JSON alias map for --results (see DEFAULT_T1_MAP)")
            p.add_argument("--subset-only", action="store_true", help="only tokens in the validation subset (those with GoPlus answers)")
    a = ap.parse_args()
    {"sim": sim_mode, "providers": providers_mode, "tier1": tier1_mode}[a.mode](a)


if __name__ == "__main__":
    main()
