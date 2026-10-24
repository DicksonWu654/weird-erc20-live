#!/usr/bin/env python3
"""Round-2 comparison, evidence-linked adjudication, and publishable summary.

The comparison uses the W6 mapping exactly (unknown provider/simulator states
are excluded, never treated as negatives).  It also writes a single
adjudication row for every token involved in a disagreement in either binary
label, including provider-vs-provider disagreements.
"""
import argparse, csv, json, math, sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/validate/round2"
sys.path.insert(0, str(ROOT / "scripts/validate"))
import compare as C  # noqa: E402
import normalize as N  # noqa: E402

C.VAL = OUT
C.OUT = OUT

def args_for(path, tag):
    return argparse.Namespace(sim=str(path), chain=1, at=None, tax_threshold=10.0,
        sim_tax_unit="fraction", boot=2000, strata=None, exclude_strata=None,
        owner_active=False, tag=tag, probe=[], control=[], results=[], map=None)

def load_subset():
    return {(r["chain"], r["token"].lower()): r for r in
            (json.loads(x) for x in (OUT / "subset.jsonl").open() if x.strip())}

def load_sim(path):
    out = {}
    for x in path.open():
        if not x.strip(): continue
        r = json.loads(x); tok = r["token"].lower()
        out[(1, tok)] = r
    return out

def metric(pairs, labels):
    return C.metrics([(x, y) for x, y in pairs], labels=labels, boot=2000)

def views(subset, sim_raw):
    keys = sorted(subset)
    hp, gp = C.provider_views(keys, 10.0, None, False)
    ss = {}
    for k, r in sim_raw.items():
        v = C.sim_view(r, "fraction", 10.0)
        v["_raw"] = r
        ss[k] = v
    return keys, ss, hp, gp

def pairs_for(keys, a, b, field, binary=False):
    out = []
    for k in keys:
        x, y = a.get(k), b.get(k)
        if not x or not y or not x.get("covered") or not y.get("covered"):
            continue
        xv, yv = x.get(field), y.get(field)
        if xv is None or yv is None: continue
        if binary: xv, yv = ("yes" if xv else "no"), ("yes" if yv else "no")
        out.append((k, xv, yv))
    return out

def _pct(x):
    """Simulator taxes are fractions; return a compact percent string."""
    if x is None:
        return "unknown"
    try:
        return f"{float(x) * 100:.6g}%"
    except (TypeError, ValueError):
        return str(x)


def sim_evidence_text(sraw):
    """Token-specific simulator evidence, including every sell attempt."""
    if not sraw:
        return "simulator unavailable"
    buy = sraw.get("buy") or {}
    taxes = sraw.get("taxes") or {}
    sells = sraw.get("sells") or []
    sell_text = []
    for x in sells:
        sell_text.append(
            f"{x.get('id')}:ok={x.get('ok')};tax={_pct(x.get('tax'))};"
            f"revert={x.get('reason')!r};expected_eth={x.get('expected_eth')};"
            f"received_eth={x.get('received_eth')}"
        )
    ev = sraw.get("evidence") or {}
    ev_path = ev.get("raw") if isinstance(ev, dict) else ev
    ev_sha = ev.get("sha256") if isinstance(ev, dict) else ""
    return (
        f"sim label={sraw.get('primary_label')!r}; buy_ok={buy.get('ok')};"
        f"buy_tax={_pct(taxes.get('buy', buy.get('tax')))};"
        f"sell_tax={_pct(taxes.get('sell'))};"
        f"sells=[{', '.join(sell_text) if sell_text else 'none'}];"
        f"unprobeable={sraw.get('unprobeable')!r}; sim_raw={ev_path};"
        f"sim_sha256={ev_sha}"
    )


def provider_evidence_text(pnorm, pname):
    """Include the exact normalized provider fields used for the decision."""
    if not pnorm:
        display = "honeypot.is" if pname == "honeypot" else "GoPlus" if pname == "goplus" else pname
        return f"provider={display}; unavailable"
    display = "honeypot.is" if pname == "honeypot" else "GoPlus" if pname == "goplus" else pname
    fields = [
        f"coverage={pnorm.get('coverage')}",
        f"is_honeypot={pnorm.get('is_honeypot')}",
        f"buy_tax_pct={pnorm.get('buy_tax')}",
        f"sell_tax_pct={pnorm.get('sell_tax')}",
        f"honeypot_reason={pnorm.get('honeypot_reason')!r}",
        f"honeypot_note={pnorm.get('honeypot_note')!r}",
        f"flags={pnorm.get('flags')!r}",
        f"risk={pnorm.get('risk')!r}",
        f"risk_level={pnorm.get('risk_level')!r}",
        f"cannot_sell_all={pnorm.get('cannot_sell_all')!r}",
        f"is_in_dex={pnorm.get('is_in_dex')!r}",
        f"is_open_source={pnorm.get('is_open_source')!r}",
        f"raw={pnorm.get('path')}",
        f"sha256={pnorm.get('sha256')}",
    ]
    return f"provider={display}; " + "; ".join(fields)


def evidence_reason(task, ours, provider, sraw, pnorm, pname):
    """Conservative adjudication using token-specific saved evidence."""
    sim_text = sim_evidence_text(sraw)
    prov_text = provider_evidence_text(pnorm, pname)
    if task == "honeypot":
        sell_ok = [x.get("ok") for x in (sraw.get("sells") or [])]
        sell_fail = bool(sell_ok) and any(x is False for x in sell_ok)
        sell_all_ok = bool(sell_ok) and all(x is True for x in sell_ok)
        reason = f"{sim_text}; {prov_text}"
        if ours and not provider and sell_fail:
            return "ours_right", reason + "; at least one saved simulator sell has ok=False with the listed revert"
        if not ours and provider and sell_all_ok:
            return "definition_gap", reason + "; all listed simulator sells have ok=True while the provider fields are positive"
        if ours and not provider and not sell_fail:
            return "ambiguous", reason + "; the saved simulator sells do not contain a failing sell"
        if not ours and provider and not sell_all_ok:
            return "provider_right", reason + "; the saved simulator evidence includes a failed or missing sell"
    else:
        st = max([float(x) for x in ((sraw.get("taxes") or {}).get("buy"),
                                      (sraw.get("taxes") or {}).get("sell")) if x is not None] or [float("nan")])
        pt = max([float(x) for x in (pnorm.get("buy_tax"), pnorm.get("sell_tax")) if x is not None] or [float("nan")])
        reason = f"{sim_text}; {prov_text}; sim_max_tax_pct={st * 100:.6g}%; provider_max_tax_pct={pt:.6g}%"
        if math.isfinite(st) and math.isfinite(pt):
            if ours and not provider and st >= 0.10 and pt < 10:
                return ("ours_right" if st * 100 - pt >= 5 else "definition_gap"), reason + "; simulator measured at least 5 percentage points more tax than this provider"
            if not ours and provider and pt >= 10 and st < 0.10:
                return ("provider_right" if pt - st * 100 >= 5 else "definition_gap"), reason + "; provider measured at least 5 percentage points more tax than the simulator"
        return "ambiguous", reason + "; one or both token-specific tax measurements are unavailable"
    return "ambiguous", reason

def adjudicate(keys, ss, hp, gp, subset):
    # Gather every disagreement in the two requested labels across all three
    # pairings, then collapse to one token row with provider-specific details.
    specs = [("sim", "honeypot", ss, hp), ("sim", "goplus", ss, gp),
             ("honeypot", "goplus", hp, gp)]
    diffs = defaultdict(list)
    for an, bn, a, b in specs:
        for task, field in (("honeypot", "honeypot"), ("high_tax", "high_tax")):
            for k, x, y in pairs_for(keys, a, b, field, True):
                if x != y: diffs[k].append((an, bn, task, x, y))
    rows = []
    for k, ds in sorted(diffs.items()):
        c, tok = k; sr = (ss.get(k) or {}).get("_raw") or {}
        # Values shown in the required schema; provider None means unknown.
        ours = ss.get(k, {}).get("honeypot")
        hval, gval = hp.get(k, {}).get("honeypot"), gp.get(k, {}).get("honeypot")
        verdicts, reasons, scopes, scope_verdicts = [], [], [], []
        for an, bn, task, x, y in ds:
            if an == "sim": ours_v, prov_v, pname = x == "yes", y == "yes", bn
            else: ours_v, prov_v, pname = None, y == "yes", bn
            if task == "honeypot" and an == "sim":
                pnorm = hp[k].get("norm") if pname == "honeypot" else gp[k].get("norm")
                v, why = evidence_reason(task, ours_v, prov_v, sr, pnorm or {}, pname)
            elif task == "high_tax" and an == "sim":
                pnorm = hp[k].get("norm") if pname == "honeypot" else gp[k].get("norm")
                v, why = evidence_reason(task, ours_v, prov_v, sr, pnorm or {}, pname)
            else:
                # Provider-vs-provider: use a covered simulator result when
                # available. This adjudicates which provider agrees with the
                # saved trade trace; unsupported or unknown simulator rows
                # remain a definition/coverage gap.
                sv = ss.get(k, {}).get(task)
                sl = ("yes" if sv else "no") if sv is not None else None
                base = (
                    f"provider-v-provider disagreement {an}={x}, {bn}={y}; "
                    f"{sim_evidence_text((ss.get(k) or {}).get('_raw') or {})}; "
                    f"{provider_evidence_text(hp[k].get('norm') or {}, 'honeypot.is')}; "
                    f"{provider_evidence_text(gp[k].get('norm') or {}, 'goplus')}"
                )
                if sl is not None and sl == x and sl != y:
                    v, why = "ours_right", base + "; simulator label agrees with the first provider in this scope"
                elif sl is not None and sl == y and sl != x:
                    v, why = "provider_right", base + "; simulator label agrees with the second provider in this scope"
                elif sl is None:
                    v, why = "definition_gap", base + "; simulator unavailable"
                else:
                    v, why = "ambiguous", base + "; simulator does not separate providers"
            scope = f"{an}_vs_{bn}:{task}"
            scopes.append(scope); scope_verdicts.append(f"{scope}={v}")
            verdicts.append(v); reasons.append(why)
        # Prefer a decisive verdict if all decisive evidence agrees; otherwise
        # preserve ambiguity/definition gap rather than silently choosing.
        if all(v == "ours_right" for v in verdicts): verdict = "ours_right"
        elif all(v == "provider_right" for v in verdicts): verdict = "provider_right"
        elif "ambiguous" in verdicts: verdict = "ambiguous"
        else: verdict = "definition_gap"
        rows.append({"chain": c, "token": tok, "stratum": subset[k].get("stratum", ""),
            "ours": "yes" if ours is True else "no" if ours is False else "unknown",
            "honeypot.is": "yes" if hval is True else "no" if hval is False else "unknown",
            "goplus": "yes" if gval is True else "no" if gval is False else "unknown",
            "verdict": verdict, "disagreement_scopes": ";".join(scopes),
            "scope_verdicts": ";".join(scope_verdicts),
            "reason": " || ".join(reasons),
            # ``sim_view`` normalizes the simulator evidence object to its
            # saved path string.  Preserve that path in the adjudication
            # table; the raw gzipped trace contains the full request/response
            # envelope and hash referenced by the simulator result.
            "sim_raw": (sr.get("evidence") if isinstance(sr.get("evidence"), str)
                        else ((sr.get("evidence") or {}).get("raw", "")
                              if isinstance(sr.get("evidence"), dict) else "")),
            "honeypot_raw": hp[k].get("norm", {}).get("path", ""),
            "goplus_raw": gp[k].get("norm", {}).get("path", "")})
    with (OUT / "adjudication.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["chain","token","ours","honeypot.is","goplus","verdict","reason"])
        w.writeheader(); w.writerows(rows)
    return rows, diffs

def main():
    subset = load_subset(); sim_path = OUT / "sim-latest/results.jsonl"
    sim_raw = load_sim(sim_path); keys, ss, hp, gp = views(subset, sim_raw)
    # compare.py reports are reproducible and include exclusion counts/confusion matrices.
    C.sim_mode(args_for(sim_path, "round2"))
    C.providers_mode(args_for(sim_path, "round2"))
    adj, diffs = adjudicate(keys, ss, hp, gp, subset)

    # Publishable table.  ``raw`` preserves the ordinary inter-rater metric.
    # ``adjudicated`` treats agreements as correct for both parties and adds
    # only decisive scope adjudications.  Ambiguous and definition-gap scopes
    # stay in the explicit counts and are removed from the truth denominator.
    out = []
    specs = [
        ("sim", "honeypot", ss, hp, "ours", "honeypot.is"),
        ("sim", "goplus", ss, gp, "ours", "goplus"),
        ("honeypot", "goplus", hp, gp, "honeypot.is", "goplus"),
    ]
    adj_by_key = {(int(r["chain"]), r["token"].lower()): r for r in adj}

    def scope_verdict(row, scope):
        if not row:
            return None
        for item in (row.get("scope_verdicts") or "").split(";"):
            if item.startswith(scope + "="):
                return item.split("=", 1)[1]
        return None

    fields = [
        "comparison", "label", "phase", "party_a", "party_b",
        "n", "agreement", "wilson_ci_low", "wilson_ci_high",
        "raw_n", "resolved_n", "raw_agreement", "raw_ci_low", "raw_ci_high",
        "kappa", "kappa_ci_low", "kappa_ci_high",
        "party_a_accuracy", "party_a_ci_low", "party_a_ci_high",
        "party_b_accuracy", "party_b_ci_low", "party_b_ci_high",
        "ours_accuracy", "ours_ci_low", "ours_ci_high",
        "provider_accuracy", "provider_ci_low", "provider_ci_high",
        "party_a_right", "party_b_right", "ours_right", "provider_right",
        "ambiguous", "definition_gap", "disagreements_before", "adjudication_rows",
    ]
    for an, bn, a, b, party_a, party_b in specs:
        for label, field in (("honeypot", "honeypot"), ("high_tax", "high_tax")):
            pp = pairs_for(keys, a, b, field, True)
            m = metric([(x, y) for _, x, y in pp], ["no", "yes"])
            n = len(pp)
            raw_agreement = m.get("agreement")
            raw_ci = m.get("agreement_ci95_wilson") or [None, None]
            counts = defaultdict(int)
            agreements = sum(1 for _, x, y in pp if x == y)
            scope = f"{an}_vs_{bn}:{label}"
            for k, x, y in pp:
                if x == y:
                    continue
                v = scope_verdict(adj_by_key.get(k), scope)
                if v in ("ours_right", "provider_right", "ambiguous", "definition_gap"):
                    counts[v] += 1
                else:
                    # A missing scope is itself unresolved; this should never
                    # occur, but avoids silently inflating adjudicated accuracy.
                    counts["definition_gap"] += 1
            unresolved = counts["ambiguous"] + counts["definition_gap"]
            resolved_n = n - unresolved
            a_correct = agreements + counts["ours_right"]
            b_correct = agreements + counts["provider_right"]
            a_acc = (a_correct / resolved_n) if resolved_n else None
            b_acc = (b_correct / resolved_n) if resolved_n else None
            a_ci = C.wilson(a_correct, resolved_n)
            b_ci = C.wilson(b_correct, resolved_n)
            base = {
                "comparison": f"{an}_vs_{bn}", "label": label,
                "party_a": party_a, "party_b": party_b,
                "n": n, "agreement": raw_agreement, "wilson_ci_low": raw_ci[0], "wilson_ci_high": raw_ci[1],
                "raw_n": n, "resolved_n": resolved_n,
                "raw_agreement": raw_agreement, "raw_ci_low": raw_ci[0], "raw_ci_high": raw_ci[1],
                "kappa": m.get("kappa"),
                "kappa_ci_low": (m.get("kappa_ci95_boot") or [None, None])[0],
                "kappa_ci_high": (m.get("kappa_ci95_boot") or [None, None])[1],
                "party_a_accuracy": a_acc, "party_a_ci_low": a_ci[0], "party_a_ci_high": a_ci[1],
                "party_b_accuracy": b_acc, "party_b_ci_low": b_ci[0], "party_b_ci_high": b_ci[1],
                # These aliases keep the requested simulator/provider names
                # readable while party_a/b make provider-v-provider explicit.
                "ours_accuracy": a_acc, "ours_ci_low": a_ci[0], "ours_ci_high": a_ci[1],
                "provider_accuracy": b_acc, "provider_ci_low": b_ci[0], "provider_ci_high": b_ci[1],
                "party_a_right": counts["ours_right"], "party_b_right": counts["provider_right"],
                "ours_right": counts["ours_right"], "provider_right": counts["provider_right"],
                "ambiguous": counts["ambiguous"], "definition_gap": counts["definition_gap"],
                "disagreements_before": n - agreements, "adjudication_rows": len(adj),
            }
            out.append({**base, "phase": "raw", "party_a_accuracy": "", "party_a_ci_low": "", "party_a_ci_high": "",
                        "party_b_accuracy": "", "party_b_ci_low": "", "party_b_ci_high": "",
                        "ours_accuracy": "", "ours_ci_low": "", "ours_ci_high": "",
                        "provider_accuracy": "", "provider_ci_low": "", "provider_ci_high": ""})
            out.append({**base, "phase": "adjudicated", "n": resolved_n, "agreement": "",
                        "wilson_ci_low": "", "wilson_ci_high": "", "kappa": "",
                        "kappa_ci_low": "", "kappa_ci_high": ""})
    with (OUT / "summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)
    scope_verdict_counts = defaultdict(int)
    for row in adj:
        for item in row.get("scope_verdicts", "").split(";"):
            if "=" in item:
                scope_verdict_counts[item.rsplit("=", 1)[1]] += 1
    (OUT / "adjudication_meta.json").write_text(json.dumps({
        "n_subset": len(subset), "n_sim_rows": len(sim_raw),
        "n_disagreement_tokens": len(adj), "n_disagreement_scopes": sum(len(v) for v in diffs.values()),
        "scope_counts": {k: sum(1 for v in diffs.values() for d in v if f"{d[0]}_vs_{d[1]}:{d[2]}" == k)
                          for k in sorted({f"{d[0]}_vs_{d[1]}:{d[2]}" for v in diffs.values() for d in v})},
        "scope_verdict_counts": dict(sorted(scope_verdict_counts.items()))}, indent=2))
    print(json.dumps({"subset":len(subset),"sim_rows":len(sim_raw),"adjudication_tokens":len(adj),
                      "disagreement_scopes":sum(len(v) for v in diffs.values())}, indent=2))

if __name__ == "__main__": main()
