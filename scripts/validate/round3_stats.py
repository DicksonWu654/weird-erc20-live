#!/usr/bin/env python3
"""C18 inter-rater statistics; no adjudication or truth/accuracy estimator.

Uses the exact provider envelopes named by round-2 timing.csv, never a mutable
latest-cache lookup. Writes aggregates plus evidence-linked local row inputs.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'data/validate/round3'
OLD = ROOT/'data/validate/round2'
sys.path.insert(0, str(ROOT/'scripts/validate'))
import normalize as N

PAIRS = [('sim', 'honeypot'), ('sim', 'goplus'), ('honeypot', 'goplus')]
LABELS = ['honeypot', 'high_tax', 'harm']
STRATA = ['all', 'liquid', 'thin', 'dust', 'top', 'newer', 'w6_new', 'w6_top',
          'newer_liquid', 'newer_thin', 'newer_dust', 'v2', 'v3', 'v4']
LAGS = ['all', 'pair_aligned_le30m', 'pair_mid_30m_2h', 'pair_stale_gt2h',
        'threeway_aligned_le30m', 'threeway_mid_30m_2h', 'threeway_stale_gt2h',
        'old_stale_gt2h_matched']


def jl(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def table(path, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def epoch(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp() if s else None


def union(a, b):
    return True if a is True or b is True else False if a is False and b is False else None


def tax_label(values, strict=False):
    buy, sell, *extra = values
    if strict:
        return max(buy, sell) >= 10 if buy is not None and sell is not None else None
    if any(v is not None and math.isfinite(v) and v >= 10 for v in values):
        return True
    return False if buy is not None and sell is not None and all(math.isfinite(v) for v in (buy, sell)) else None


def sim_view(r, legacy=False):
    if not r:
        return {'honeypot': None, 'high_tax': None, 'harm': None, 'why': 'not_simulated'}
    lab = r['primary_label']
    known = lab in ('honeypot', 'high_tax', 'normal', 'owner_controlled', 'rugged')
    hp = (lab == 'honeypot') if known and lab != 'rugged' else None
    if 'exit_unresolved' in (r.get('flags') or []):
        hp = None
    tx = r.get('taxes') or {}
    buy = r.get('buy') or {}
    values = [None if x is None else float(x)*100 for x in (tx.get('buy', buy.get('tax')), tx.get('sell'), tx.get('transfer'))]
    high = tax_label(values, strict=legacy) if known and buy.get('ok') is not False else None
    if not legacy and lab == 'high_tax':
        high = True
    return {'honeypot': hp, 'high_tax': high, 'harm': union(hp, high),
            'why': (r.get('unprobeable') or r.get('label_reason') or lab) if not known else lab}


def provider_view(path, provider, token, legacy=False):
    if not path or not (ROOT/path).exists():
        return {'honeypot': None, 'high_tax': None, 'harm': None, 'why': 'not_available'}
    p = ROOT/path
    env = json.loads(p.read_text())
    try:
        norm = N.normalize_honeypot(env, p) if provider == 'honeypot' else N.normalize_goplus(env, token, p)
    except (TypeError, ValueError, AttributeError, KeyError):
        return {'honeypot': None, 'high_tax': None, 'harm': None, 'why': 'provider_schema_error', 'raw': path}
    covered = norm['coverage'] in (('ok', 'sim_failed') if provider == 'honeypot' else ('ok',))
    body = N._body(env) or {}
    if not isinstance(body, dict):
        return {'honeypot': None, 'high_tax': None, 'harm': None, 'why': 'provider_schema_error', 'raw': path}
    if provider == 'goplus':
        result = body.get('result') or {}
        if not isinstance(result, dict):
            return {'honeypot': None, 'high_tax': None, 'harm': None, 'why': 'provider_schema_error', 'raw': path}
        matches = {str(k).lower(): v for k, v in result.items()}
        record = matches.get(token.lower())
        if covered and record is None:
            return {'honeypot': None, 'high_tax': None, 'harm': None, 'why': 'response_token_mismatch', 'raw': path}
        value = (record or {}).get('is_honeypot')
    else:
        actual_token = (body.get('token') or {}).get('address')
        if actual_token and actual_token.lower() != token.lower():
            return {'honeypot': None, 'high_tax': None, 'harm': None, 'why': 'response_token_mismatch', 'raw': path}
        value = (body.get('honeypotResult') or {}).get('isHoneypot')
    hp = (True if value in (True, 1, '1') else False if value in (False, 0, '0') else None) if covered else None
    tx = tax_label([norm.get(x) for x in ('buy_tax', 'sell_tax', 'transfer_tax')], strict=legacy) if covered else None
    return {'honeypot': hp, 'high_tax': tx, 'harm': union(hp, tx), 'why': norm['coverage'],
            'raw': path, 'sha256': norm['sha256'], 'retrieved_at': norm['retrieved_at']}


def reclassify(records, rule, round_name):
    if rule == 'v2':
        measured = {x['key']: x for x in jl(OUT/'liquidity_measurements.jsonl')}
        updated = []
        for original in records:
            r = dict(original)
            key = f"{round_name}:{r['token'].lower()}:{r['pool'].lower()}:{r['block']}"
            enrichment = measured.get(key)
            if enrichment:
                r['pool_info'] = {**(r.get('pool_info') or {}), **enrichment['measurement']}
                r['liquidity_evidence'] = enrichment['evidence']
            updated.append(r)
        records = updated
    dest = OUT/'reclassified'/rule/f'{round_name}.jsonl'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(''.join(json.dumps(x)+'\n' for x in records))
    command = [sys.executable, '-B', str(OUT/'rules'/rule/'reclassify.py'), str(dest)]
    p = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
    if p.returncode:
        raise RuntimeError(p.stdout+p.stderr)
    print(p.stdout.strip(), flush=True)
    return {(1, x['token'].lower()): x for x in jl(dest)}


def load_rows(name, sims, legacy=False):
    subset = jl(OLD/'subset.jsonl')
    timings = {}
    if name == 'round2':
        with (OLD/'timing.csv').open() as f:
            for r in csv.DictReader(f):
                timings[int(r['chain']), r['token'].lower()] = r
    else:
        paths = sorted((OUT/'local/v4').glob('*/timing.json')) if name == 'round3_v4' else sorted((OUT/'batches').glob('*/timing.json'))
        for p in paths:
            for r in json.loads(p.read_text()):
                timings[r['chain'], r['token']] = r
    rows = []
    for meta in subset:
        chain, token = int(meta['chain']), meta['token'].lower()
        tm = timings.get((chain, token))
        if tm is None:
            continue
        s = sims.get((chain, token))
        out = {**meta, 'token': token, 'chain': chain, 'sim': sim_view(s, legacy),
               'venue': (s.get('pool_info') or {}).get('kind') if s else meta.get('pool_kind'),
               'sim_block': s.get('block') if s else None, 'sim_time': s.get('retrieved_at') if s else None,
               'sim_evidence': s.get('evidence') if s else None}
        for p in ('honeypot', 'goplus'):
            path = tm.get(p+'_raw') if name == 'round2' else tm[p]['raw']
            out[p] = provider_view(path, p, token, legacy)
            out[p+'_time'] = tm.get(p+'_retrieved_at') if name == 'round2' else tm[p]['retrieved_at']
        ts = [epoch(out[p+'_time']) for p in ('sim', 'honeypot', 'goplus')]
        out['threeway_lag_seconds'] = max(ts)-min(ts) if all(x is not None for x in ts) else None
        if name.startswith('round3'):
            out['batch'] = tm['batch']
            out['observed_head_before'] = tm['observed_head_before']
            out['provider_blocks'] = {p: tm[p]['provider_block'] for p in ('honeypot', 'goplus')}
        rows.append(out)
    return rows


def wilson(p, n):
    if not n:
        return None, None
    z = 1.959963984540054
    d = 1+z*z/n
    m = (p+z*z/(2*n))/d
    h = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (0. if p == 0 else max(0, m-h)), (1. if p == 1 else min(1, m+h))


def from_cells(c):
    d, c01, c10, a = c
    n = sum(c)
    if not n:
        return dict(agreement=None, kappa=None, positive_specific_agreement=None, negative_specific_agreement=None, pabak=None)
    po = (a+d)/n
    pe = ((a+c10)*(a+c01)+(d+c01)*(d+c10))/(n*n)
    return dict(agreement=po, kappa=(po-pe)/(1-pe) if pe < 1 else None,
                positive_specific_agreement=2*a/(2*a+c01+c10) if 2*a+c01+c10 else None,
                negative_specific_agreement=2*d/(2*d+c01+c10) if 2*d+c01+c10 else None, pabak=2*po-1)


def metrics(rows, a, b, label, weights, boot, seed):
    cells = [0, 0, 0, 0]
    weighted = [0., 0., 0., 0.]
    groups = defaultdict(lambda: [0, 0, 0, 0])
    w_by_group = {}
    wsum2 = 0.
    for r in rows:
        x, y = r[a][label], r[b][label]
        assert isinstance(x, bool) and isinstance(y, bool)
        i = 2*int(x)+int(y)
        w = weights.get((r['chain'], r['token']), 1.)
        g = (r.get('part'), r.get('stratum'))
        cells[i] += 1; weighted[i] += w; wsum2 += w*w
        groups[g][i] += 1
        assert g not in w_by_group or w_by_group[g] == w
        w_by_group[g] = w
    sw = sum(weighted)
    neff = sw*sw/wsum2 if wsum2 else 0
    m = from_cells(weighted)
    low, high = wilson(m['agreement'], neff) if sw else (None, None)
    rng = np.random.default_rng(seed)
    draws = np.zeros((boot, 4))
    for g, counts in sorted(groups.items()):
        n = sum(counts)
        draws += rng.multinomial(n, np.asarray(counts)/n, size=boot)*w_by_group[g]
    if sw:
        dn = draws.sum(axis=1)
        po = (draws[:, 0]+draws[:, 3])/dn
        pe = ((draws[:, 3]+draws[:, 2])*(draws[:, 3]+draws[:, 1])+
              (draws[:, 0]+draws[:, 1])*(draws[:, 0]+draws[:, 2]))/(dn*dn)
        good = pe < 1-1e-14
        kappas = (po[good]-pe[good])/(1-pe[good])
        kci = np.quantile(kappas, [.025, .975]).tolist() if len(kappas) else [None, None]
        aci = np.quantile(po, [.025, .975]).tolist()
        valid = int(good.sum())
    else:
        kci, aci, valid = [None, None], [None, None], 0
    return {'n': sum(cells), **{f'n{i//2}{i%2}': x for i, x in enumerate(cells)},
            **{f'w{i//2}{i%2}': x for i, x in enumerate(weighted)}, 'weight_sum': sw, 'kish_n_eff': neff,
            **m, 'wilson_ci_low': low, 'wilson_ci_high': high,
            'agreement_bootstrap_ci_low': aci[0], 'agreement_bootstrap_ci_high': aci[1],
            'kappa_ci_low': kci[0], 'kappa_ci_high': kci[1], 'kappa_bootstrap_valid': valid,
            'kappa_bootstrap_undefined': boot-valid if sw else boot,
            'bootstrap_replicates': boot, 'bootstrap_seed': seed}


def stratum(r, s):
    if s == 'all': return True
    if s in ('liquid', 'thin', 'dust'): return r.get('stratum') == s
    if s == 'top': return r.get('part') == 'w6_top'
    if s == 'newer': return r.get('part') == 'newer14'
    if s in ('w6_new', 'w6_top'): return r.get('part') == s
    if s.startswith('newer_'): return r.get('part') == 'newer14' and r.get('stratum') == s[6:]
    return r.get('venue') == s


def lagged(r, a, b, group, old_stale):
    if group == 'all': return True
    if group == 'old_stale_gt2h_matched': return (r['chain'], r['token']) in old_stale
    if group.startswith('pair_'):
        ta, tb = epoch(r.get(a+'_time')), epoch(r.get(b+'_time'))
        lag = abs(ta-tb) if ta is not None and tb is not None else None
    else:
        lag = r['threeway_lag_seconds']
    if lag is None: return False
    if 'aligned' in group: return lag <= 1800
    if 'mid' in group: return 1800 < lag <= 7200
    return lag > 7200


def summarize(datasets, boot):
    population = Counter(x['stratum'] for x in jl(ROOT/'data/validate/new_eth_candidates.jsonl'))
    sample = Counter(x['stratum'] for x in jl(OLD/'subset.jsonl') if x['part'] == 'w6_new')
    weights = {(int(x['chain']), x['token'].lower()): population[x['stratum']]/sample[x['stratum']]
               for x in jl(OLD/'subset.jsonl') if x['part'] == 'w6_new'}
    table(OUT/'weights.csv', [{'stratum': s, 'candidate_N': population[s], 'sample_n': sample[s],
                              'calibration_weight': population[s]/sample[s]} for s in sorted(sample)])
    oldrows = datasets['round2_original']
    old_stale = {(r['chain'], r['token']) for r in oldrows if (r['threeway_lag_seconds'] or 0) > 7200}
    output = []
    exclusions = []
    def status(view, label):
        if view[label] is not None:
            return 'known'
        why = view['why']
        if why in ('ok', 'sim_failed', 'normal', 'owner_controlled', 'honeypot', 'high_tax', 'rugged'):
            return f'{why}:missing_{label}_evidence'
        return why
    for name, allrows in datasets.items():
        for weighting in ('unweighted', 'new_eth_candidates_calibrated'):
            if '_v4_' in name and weighting != 'unweighted':
                continue
            base = [r for r in allrows if r['chain'] == 1 and (weighting == 'unweighted' or r['part'] == 'w6_new')]
            wm = weights if weighting != 'unweighted' else {}
            for a, b in PAIRS:
                for label in LABELS:
                    for s in STRATA:
                        slice_rows = [r for r in base if stratum(r, s)]
                        for lag in LAGS:
                            selected = [r for r in slice_rows if lagged(r, a, b, lag, old_stale)]
                            paired = [r for r in selected if r[a][label] is not None and r[b][label] is not None]
                            ident = {'dataset': name, 'scope': 'current_state_ethereum_v4_diagnostic' if '_v4_' in name else 'current_state_ethereum', 'comparison': f'{a}_vs_{b}',
                                     'label': label, 'stratum': s, 'lag_group': lag, 'weighting': weighting}
                            # Equivalent slices get identical Monte Carlo draws.
                            signature = sorted((r['chain'], r['token'], r[a][label], r[b][label],
                                                wm.get((r['chain'], r['token']), 1.)) for r in paired)
                            seed = int(hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:8], 16)
                            m = metrics(paired, a, b, label, wm, boot, seed)
                            output.append({**ident, 'n_eligible': len(selected), 'n_excluded': len(selected)-len(paired),
                                           **m, 'ci_method': 'wilson' if weighting == 'unweighted' else 'wilson_kish_approx',
                                           'target_weight_coverage': m['weight_sum']/sum(population.values()) if wm else None})
                        if s == 'all':
                            counts = Counter((status(r[a], label), status(r[b], label)) for r in slice_rows)
                            for (why_a, why_b), n in sorted(counts.items()):
                                exclusions.append({'dataset': name, 'comparison': f'{a}_vs_{b}', 'label': label,
                                                   'weighting': weighting, 'party_a_status': why_a, 'party_b_status': why_b, 'n': n})
    # Legacy scope solely to reconcile the six previously published aggregate counts.
    for a, b in PAIRS:
        for label in LABELS:
            pp = [r for r in oldrows if r[a][label] is not None and r[b][label] is not None]
            m = metrics(pp, a, b, label, {}, boot, 20261022)
            output.append({'dataset': 'round2_original', 'scope': 'legacy_full_cohort', 'comparison': f'{a}_vs_{b}',
                           'label': label, 'stratum': 'all', 'lag_group': 'all', 'weighting': 'unweighted',
                           'n_eligible': len(oldrows), 'n_excluded': len(oldrows)-len(pp), **m,
                           'ci_method': 'wilson', 'target_weight_coverage': None})
    table(OUT/'summary.csv', output)
    count_fields = ['dataset', 'scope', 'comparison', 'label', 'stratum', 'lag_group', 'weighting',
                    'n_eligible', 'n_excluded', 'n', 'n00', 'n01', 'n10', 'n11', 'w00', 'w01', 'w10', 'w11', 'weight_sum']
    table(OUT/'counts.csv', [{k: r[k] for k in count_fields} for r in output])
    table(OUT/'exclusions.csv', exclusions)
    headline = [r for r in output if r['scope'] == 'current_state_ethereum' and r['stratum'] == 'all'
                and r['lag_group'] == 'all' and r['weighting'] == 'unweighted']
    table(OUT/'headline.csv', headline)
    return output


def matched_comparisons(datasets, boot):
    """Compare old/fresh rounds on identical paired-complete token identities."""
    result = []
    for rule in ('current', 'v2'):
        if f'round3_{rule}' not in datasets:
            continue
        old = {(r['chain'], r['token']): r for r in datasets[f'round2_{rule}'] if r['chain'] == 1}
        fresh = {(r['chain'], r['token']): r for r in datasets[f'round3_{rule}'] if r['chain'] == 1}
        for a, b in PAIRS:
            for label in LABELS:
                for source in ('all', 'old_threeway_stale_gt2h', 'old_threeway_mid_30m_2h', 'old_threeway_aligned_le30m'):
                    pp = []
                    for k in sorted(old.keys() & fresh.keys()):
                        o, f = old[k], fresh[k]
                        if any(r[p][label] is None for r in (o, f) for p in (a, b)):
                            continue
                        lag = o['threeway_lag_seconds']
                        if source.endswith('stale_gt2h') and (lag is None or lag <= 7200): continue
                        if source.endswith('mid_30m_2h') and (lag is None or not 1800 < lag <= 7200): continue
                        if source.endswith('aligned_le30m') and (lag is None or lag > 1800): continue
                        pp.append((o, f))
                    oc = [sum(int(o[a][label])*2+int(o[b][label]) == i for o, f in pp) for i in range(4)]
                    fc = [sum(int(f[a][label])*2+int(f[b][label]) == i for o, f in pp) for i in range(4)]
                    transitions = defaultdict(lambda: np.zeros(4, dtype=int))
                    for o, f in pp:
                        oa, fa = o[a][label] == o[b][label], f[a][label] == f[b][label]
                        transitions[o['part'], o['stratum']][int(oa)*2+int(fa)] += 1
                    n = len(pp)
                    rng = np.random.default_rng(20261022)
                    draws = np.zeros((boot, 4))
                    for counts in transitions.values():
                        draws += rng.multinomial(int(sum(counts)), counts/sum(counts), size=boot)
                    ci = np.quantile((draws[:, 1]-draws[:, 2])/n, [.025, .975]).tolist() if n else [None, None]
                    om, fm = from_cells(oc), from_cells(fc)
                    result.append({'rules': rule, 'comparison': f'{a}_vs_{b}', 'label': label, 'old_lag_group': source,
                                   'n_matched': n, **{f'old_n{i//2}{i%2}': v for i, v in enumerate(oc)},
                                   **{f'new_n{i//2}{i%2}': v for i, v in enumerate(fc)},
                                   'old_agreement': om['agreement'], 'new_agreement': fm['agreement'],
                                   'agreement_change': fm['agreement']-om['agreement'] if n else None,
                                   'change_ci_low': ci[0], 'change_ci_high': ci[1],
                                   'old_kappa': om['kappa'], 'new_kappa': fm['kappa']})
    table(OUT/'matched_comparisons.csv', result)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bootstrap', type=int, default=4000)
    ap.add_argument('--partial', action='store_true')
    args = ap.parse_args()
    if not args.partial and not (OUT/'collection_complete.json').exists():
        raise SystemExit('Collection incomplete; use --partial only for a labelled progress report')
    if not args.partial and (OUT/'rules/v2').exists() and not (OUT/'liquidity_complete.json').exists():
        raise SystemExit('v2 liquidity enrichment is still incomplete')
    old_records = jl(OLD/'sim-latest/results.jsonl')
    fresh_records = [r for p in sorted((OUT/'batches').glob('*/sim/results.jsonl')) for r in jl(p)]
    datasets = {'round2_original': load_rows('round2', {(1, x['token'].lower()): x for x in old_records}, legacy=True)}
    rule_names = ['current'] + (['v2'] if (OUT/'rules/v2/reclassify.py').exists() else [])
    for rule in rule_names:
        for name, records in [('round2', old_records), ('round3', fresh_records)]:
            sim = reclassify(records, rule, name)
            datasets[f'{name}_{rule}'] = load_rows(name, sim)
        v4_records = [r for p in sorted((OUT/'local/v4').glob('*/results.jsonl')) for r in jl(p)]
        if v4_records:
            sim = reclassify(v4_records, rule, 'round3_v4')
            datasets[f'round3_v4_{rule}'] = load_rows('round3_v4', sim)
    for name, rows in datasets.items():
        p = OUT/'local'/f'analysis_rows_{name}.jsonl'
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    result = summarize(datasets, args.bootstrap)
    matched_comparisons(datasets, args.bootstrap)
    dump(OUT/'statistics_meta.json', {'generated_at': datetime.now(timezone.utc).isoformat(),
         'partial_collection': not (OUT/'collection_complete.json').exists(), 'rules': rule_names,
         'datasets': {k: len(v) for k, v in datasets.items()}, 'summary_rows': len(result),
         'bootstrap': args.bootstrap, 'bootstrap_unit': 'token, stratified within source part x liquidity',
         'python_version': sys.version.split()[0], 'numpy_version': np.__version__,
         'classification_error_included': False, 'adjudication_used': False})
    print(json.dumps({k: len(v) for k, v in datasets.items()}), flush=True)


if __name__ == '__main__':
    main()
