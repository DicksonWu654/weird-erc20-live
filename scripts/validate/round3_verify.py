#!/usr/bin/env python3
"""Audit saved evidence, actual pacing, alignment, and aggregate arithmetic."""
from collections import Counter, defaultdict
import ast
import csv
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'data/validate/round3'


def epoch(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()


def main():
    report = {'checks': {}, 'problems': []}
    def check(name, passed, detail):
        report['checks'][name] = {'passed': bool(passed), 'detail': detail}
        if not passed:
            report['problems'].append(name)
    manifest = json.loads((OUT/'input_manifest.json').read_text())
    check('source_inputs_unchanged', all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest() == h for p, h in manifest.items()), len(manifest))
    old_ok, old_provider_n, old_sim_n = True, 0, 0
    for r in csv.DictReader((ROOT/'data/validate/round2/timing.csv').open()):
        for provider in ('honeypot', 'goplus'):
            rawpath = r[provider+'_raw']
            if not rawpath:
                continue
            p = ROOT/rawpath
            if not p.exists():
                old_ok = False
                continue
            e = json.loads(p.read_text())
            sha = hashlib.sha256(e['body_text'].encode()).hexdigest()
            old_ok &= sha == e['body_sha256'] == r[provider+'_sha256']
            old_ok &= e['retrieved_at'] == r[provider+'_retrieved_at']
            old_provider_n += 1
    for line in (ROOT/'data/validate/round2/sim-latest/results.jsonl').open():
        r = json.loads(line)
        e = r.get('evidence') or {}
        if not e:
            old_ok &= bool(r.get('unprobeable'))
            continue
        old_ok &= hashlib.sha256(gzip.decompress((ROOT/e['raw']).read_bytes())).hexdigest() == e['sha256']
        old_sim_n += 1
    check('round2_saved_evidence_hashes_and_provider_timestamps', old_ok, {'provider_envelopes': old_provider_n, 'sim_traces': old_sim_n})
    if (OUT/'rules/v2/RULES_V2_READY').exists():
        tree = ast.parse((OUT/'rules/v2/classify.py').read_text())
        constants = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == 'RULES' for t in node.targets))
        check('v2_marker_matches_frozen_rules', constants == json.loads((OUT/'rules/v2/RULES_V2_READY').read_text()), constants)
    attempts = defaultdict(list)
    statuses = Counter()
    hashes_ok = True
    raw_count = 0
    for p in sorted((OUT/'local/providers').glob('*/*/*/*.attempt*.json')):
        e = json.loads(p.read_text())
        hashes_ok &= hashlib.sha256(e['body_text'].encode()).hexdigest() == e['body_sha256']
        raw_count += 1
        provider = p.name.split('.')[0]
        attempts[provider].append(epoch(e['started_at']))
        try:
            code = json.loads(e['body_text']).get('code')
        except ValueError:
            code = None
        statuses[provider, str(e['status']), str(code)] += 1
    check('provider_body_hashes', hashes_ok, raw_count)
    for p, min_gap, per_minute in [('goplus', 6., 10), ('honeypot', .5, 120)]:
        stamps = sorted(attempts[p])
        gaps = [b-a for a, b in zip(stamps, stamps[1:])]
        maxwindow, left = 0, 0
        for right, ts in enumerate(stamps):
            while ts-stamps[left] >= 60:
                left += 1
            maxwindow = max(maxwindow, right-left+1)
        check(p+'_pacing', (not gaps or min(gaps) >= min_gap-.01) and maxwindow <= per_minute,
              {'attempts': len(stamps), 'min_gap_seconds': min(gaps) if gaps else None, 'max_requests_in_60s': maxwindow})
    report['provider_status_counts'] = [{'provider': p, 'http_status': s, 'body_code': c, 'n': n}
                                         for (p, s, c), n in sorted(statuses.items())]
    batches = [json.loads(p.read_text()) for p in sorted((OUT/'batches').glob('*/complete.json'))]
    check('completed_batches_aligned', all(b['batch_window_seconds'] <= 1800 for b in batches), batches)
    sim_records = [json.loads(l) for p in sorted((OUT/'batches').glob('*/sim/results.jsonl')) for l in p.open()]
    raw_ok = True
    block_ok = True
    read_only = True
    allowed = {'eth_call', 'eth_simulateV1', 'eth_getLogs', 'eth_getBlockByNumber', 'eth_blockNumber', 'eth_chainId',
               'eth_getCode', 'eth_getBalance', 'eth_getTransactionByHash', 'eth_getTransactionReceipt',
               'eth_getStorageAt', 'eth_getBlockReceipts', 'debug_traceCall'}
    refs = {}
    for s in sim_records:
        ev = s.get('evidence')
        if not ev:
            # Missing traces must be explicitly unprobeable.
            raw_ok &= bool(s.get('unprobeable'))
            continue
        p = ROOT/ev['raw']
        raw = gzip.decompress(p.read_bytes())
        raw_ok &= hashlib.sha256(raw).hexdigest() == ev['sha256']
        refs[ev['raw']] = ev['sha256']
        for e in json.loads(raw):
            req = e['request']
            read_only &= req.get('method') in allowed
            if req['method'] == 'eth_simulateV1':
                block_ok &= int(req['params'][-1], 16) == s['block']
    check('sim_trace_hashes', raw_ok, len(refs))
    check('sim_trace_pinned_blocks', block_ok, len(sim_records))
    check('sim_rpc_methods_read_only', read_only, len(sim_records))
    measurements = [json.loads(l) for l in (OUT/'liquidity_measurements.jsonl').open()] if (OUT/'liquidity_measurements.jsonl').exists() else []
    measure_ok, measure_blocks = True, True
    for r in measurements:
        p = ROOT/r['evidence']['raw']
        raw = p.read_bytes()
        measure_ok &= hashlib.sha256(raw).hexdigest() == r['evidence']['sha256']
        for e in json.loads(raw):
            req = e['request']
            measure_blocks &= int(req['params'][-1], 16) == r['block']
            env = json.loads((ROOT/e['http_envelope']).read_text())
            measure_ok &= hashlib.sha256(env['body_text'].encode()).hexdigest() == e['body_sha256']
        if r['measurement'].get('quote_liquidity_status') == 'ok':
            measure_blocks &= r['measurement']['quote_liquidity_block'] == r['block']
    check('liquidity_evidence_hashes', measure_ok, len(measurements))
    check('liquidity_measurements_pinned_to_sim_blocks', measure_blocks, len(measurements))
    if (OUT/'liquidity_complete.json').exists():
        unique = len({r['key'] for r in measurements})
        check('liquidity_complete', unique == 1258, {'unique_measurements': unique, 'attempt_records': len(measurements)})
    if (OUT/'collection_complete.json').exists():
        check('cohort_complete', sum(b['n_tokens'] for b in batches) == 770 and len(sim_records) == 628,
              {'tokens': sum(b['n_tokens'] for b in batches), 'sim_results': len(sim_records)})
        original = {(int(r['chain']), r['token'].lower()) for r in map(json.loads, (ROOT/'data/validate/round2/subset.jsonl').read_text().splitlines())}
        observed = [json.loads(p.read_text()) for p in (OUT/'local/providers').glob('*/*/*/record.json')]
        actual = {(r['chain'], r['token']) for r in observed}
        check('exact_original_token_identities', actual == original and len(observed) == len(original), len(actual))
        expected_pairs = {(r['token'].lower(), r['pool'].lower()) for r in csv.DictReader((ROOT/'data/validate/round2/sim_pairs.csv').open())}
        actual_pairs = {(r['token'].lower(), r['pool'].lower()) for r in sim_records}
        check('exact_original_simulator_pairs', actual_pairs == expected_pairs and len(sim_records) == len(expected_pairs), len(actual_pairs))
        alignment = [r for p in (OUT/'batches').glob('*/timing.json') for r in json.loads(p.read_text())]
        check('all_token_windows_aligned', all(r['threeway_window_seconds'] is None or r['threeway_window_seconds'] <= 1800 for r in alignment), len(alignment))
        if (OUT/'timing.csv').exists():
            timing = list(csv.DictReader((OUT/'timing.csv').open()))
            measured = [r for r in timing if r['sim_block']]
            check('pinned_state_timestamps_and_request_windows', len(measured) == 628 and all(
                  r['sim_block_timestamp'] and float(r['state_and_request_window_seconds']) <= 1800 for r in measured),
                  {'n_pinned_states': len(measured), 'max_span_seconds': max((float(r['state_and_request_window_seconds']) for r in measured), default=0)})
    summary = list(csv.DictReader((OUT/'summary.csv').open()))
    bad_arithmetic = []
    for i, r in enumerate(summary):
        n = int(r['n'])
        if sum(int(r[c]) for c in ('n00', 'n01', 'n10', 'n11')) != n or int(r['n_eligible'])-int(r['n_excluded']) != n:
            bad_arithmetic.append(i)
        if not n:
            continue
        w = [float(r[c]) for c in ('w00', 'w01', 'w10', 'w11')]
        agree = (w[0]+w[3])/sum(w)
        if abs(float(r['agreement'])-agree) > 1e-12 or abs(float(r['pabak'])-(2*agree-1)) > 1e-12:
            bad_arithmetic.append(i)
        if not (float(r['wilson_ci_low']) <= agree+1e-12 and agree <= float(r['wilson_ci_high'])+1e-12):
            bad_arithmetic.append(i)
    check('all_counts_and_agreement_arithmetic', not bad_arithmetic, {'summary_rows': len(summary), 'bad_rows': bad_arithmetic[:10]})
    old = [r for r in csv.DictReader((ROOT/'data/validate/round2/summary.csv').open()) if r['phase'] == 'raw']
    legacy = {(r['comparison'], r['label']): r for r in summary if r['scope'] == 'legacy_full_cohort'}
    match = []
    for r in old:
        ours = legacy[r['comparison'], r['label']]
        match.append(int(ours['n']) == int(r['n']) and abs(float(ours['agreement'])-float(r['agreement'])) < 1e-12
                     and abs(float(ours['kappa'])-float(r['kappa'])) < 1e-12)
    check('six_round2_legacy_aggregates_reproduced', len(match) == 6 and all(match), match)
    check('no_truth_accuracy_columns', not any('accuracy' in k or 'adjudicat' in k for k in summary[0]), list(summary[0]))
    (OUT/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'checks': len(report['checks']), 'problems': report['problems'], 'provider_attempts': raw_count,
                      'completed_batches': len(batches), 'sim_records': len(sim_records)}))
    if report['problems']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
