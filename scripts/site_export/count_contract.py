"""Every exported research count must be a literal in the article ledger.

JSON pointers identify the displayed domain, so equal numbers in different
populations cannot accidentally validate each other. Calculations here only
verify the ledger; the browser reads counts from the ledger itself.
"""
from __future__ import annotations
import math
import re

LEDGER_KEY = 'site_counts'
COUNT_FIELDS = {'n', 'tokens', 'ranked', 'months', 'deployments', 'yes', 'unknown', 'measurable',
                'yes_a', 'yes_b', 'route_agreement', 'tp', 'fp', 'fn', 'tn', 'harm_weight',
                'denominator_weight', 'could_not_buy_weight', 'kappa_bootstrap_valid',
                'kappa_bootstrap_undefined', 'holm_family_n'}
COUNT_BRANCHES = {'counts', 'chain_counts', 'baseline_counts', 'b20_counts', 'feature_counts'}


def count_paths(index):
    result = {}
    def walk(value, path=(), count_branch=False):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'numbers', 'ledger', 'manifest', 'aggregate_sources', 'count_ledger_key'}:
                    continue
                parts = (*path, str(key))
                is_count = (count_branch or key in COUNT_FIELDS or key.startswith('n_') or
                            key.endswith('_n') or re.fullmatch(r'n[0-9]+', key) or key.startswith('phase2_') or
                            key in {'est_count', 'ci_low', 'ci_high'} and path[:1] == ('census',) or
                            key == 'chains' and path == ('headline',))
                if isinstance(item, (int, float)) and not isinstance(item, bool) and is_count:
                    if not math.isfinite(item):
                        raise ValueError(f'Non-finite count at {parts}')
                    pointer = '/' + '/'.join(p.replace('~', '~0').replace('/', '~1') for p in parts)
                    result[pointer] = item
                elif isinstance(item, (dict, list)):
                    walk(item, parts, count_branch or key in COUNT_BRANCHES)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, (*path, str(i)), count_branch)
    walk(index)
    return result


def bind_counts(index, facts):
    expected = count_paths(index)
    values = facts.get(LEDGER_KEY, {}).get('value')
    if not isinstance(values, dict):
        raise ValueError('Missing article/numbers.json site_counts fact; rebuild the publication ledger first')
    missing = expected.keys() - values.keys()
    if missing:
        raise ValueError(f'Counts missing from article ledger: {sorted(missing)[:12]} ({len(missing)} total)')
    for pointer, computed in expected.items():
        saved = values[pointer]
        if not isinstance(saved, (int, float)) or isinstance(saved, bool) or not math.isclose(saved, computed, rel_tol=1e-12, abs_tol=1e-8):
            raise ValueError(f'Count differs from article ledger at {pointer}: {computed!r} != {saved!r}')
        parts = [p.replace('~1', '/').replace('~0', '~') for p in pointer[1:].split('/')]
        target = index
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        if isinstance(target, list):
            target[int(parts[-1])] = saved
        else:
            target[parts[-1]] = saved
    index['count_ledger_key'] = LEDGER_KEY
    return index
