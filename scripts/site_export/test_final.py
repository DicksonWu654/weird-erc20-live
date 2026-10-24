"""Final release invariants and negative tests for the count publication gate."""
import copy
import csv
import json
import re
import unittest
from pathlib import Path
from count_contract import bind_counts, count_paths
from export import ROOT, public_class


def check_count_rendering(source):
    """Reject observed-count literals and arithmetic bypasses in reader copy.

    Dates, protocol identifiers, method constants, SVG geometry, grid positions
    and rank-band boundaries are not observations. Research counts must use
    count(object, field), a scalar fact, or the explicit SiteLedger helper.
    """
    source = source.replace('ERC<br><b>20</b>', '')
    if re.search(r'\$\{\s*[\d,.]+\s*(?:[+*/-]\s*[\d,.]+\s*)*\}', source):
        raise ValueError('Hard-coded or derived numeric interpolation')
    if re.search(r'>\s*\d[\d,]*\s*<', source):
        raise ValueError('Hard-coded numeric count in an HTML text node')
    if re.search(r'\b\d[\d,]*\s+(?:deployments|tokens|cases|joint positives|verdicts|sampled B20)\b', source):
        raise ValueError('Hard-coded observation count in site copy')
    if re.search(r'\bcount\(\s*(?:\d+|[^,()]+(?:[+*/-])[^,()]+)\s*\)', source):
        raise ValueError('Derived count bypasses ledger')
    fields = r'(?:n(?:[0-9]+|_[a-z_]+)?|effective_n|yes_a|yes_b|tp|fp|tn|fn|route_agreement)'
    for interpolation in re.findall(r'\$\{([^{}]*)\}', source):
        if re.search(r'\.' + fields + r'\b', interpolation) and not re.search(r'\b(?:count|supported|withheld)\(', interpolation):
            # Conditions may select labels without displaying the condition's number.
            if '?' not in interpolation:
                raise ValueError(f'Unbound count interpolation: {interpolation}')


class FinalCountGateTests(unittest.TestCase):
    def test_missing_mismatched_and_derived_counts_fail(self):
        fixture = {'upgrade_paths': {'counts': {'eoa': 314, 'eoa_7702': 12}},
                   'revision': {'validation': [{'n': 124, 'n00': 122, 'n11': 0, 'effective_n': 3.7}]}}
        expected = count_paths(fixture)
        facts = {'site_counts': {'value': expected}}
        self.assertEqual(bind_counts(copy.deepcopy(fixture), facts)['upgrade_paths']['counts']['eoa'], 314)
        missing = copy.deepcopy(facts)
        del missing['site_counts']['value']['/upgrade_paths/counts/eoa']
        with self.assertRaisesRegex(ValueError, 'missing'):
            bind_counts(copy.deepcopy(fixture), missing)
        mutated = copy.deepcopy(fixture)
        mutated['upgrade_paths']['counts']['eoa'] += 1
        with self.assertRaisesRegex(ValueError, 'differs'):
            bind_counts(mutated, facts)
        for source in ['<b>1054 deployments</b>', '<b>1,054</b>', '${314}', '${314 + 12}', '${count(314)}', '${count(a + b)}', '${row.n11}']:
            with self.assertRaises(ValueError, msg=source):
                check_count_rendering(source)
        self.assertIn('/revision/validation/0/n00', expected)
        self.assertIn('/revision/validation/0/effective_n', expected)

    def test_site_source_counts_use_ledger_helpers(self):
        for name in ('app.js', 'tier2.js', 'revision.js', 'index.html'):
            check_count_rendering((ROOT / 'site/src' / name).read_text())

    def test_all_exported_counts_have_literal_domain_matches(self):
        index = json.loads((ROOT / 'site/dist/data/index.json').read_text())
        ledger = json.loads((ROOT / 'article/numbers.json').read_text())
        self.assertEqual(index['numbers']['site_counts'], ledger['site_counts'])
        expected = count_paths(index)
        self.assertGreater(len(expected), 1000)
        for path, value in expected.items():
            self.assertIn(path, ledger['site_counts']['value'], path)
            self.assertEqual(value, ledger['site_counts']['value'][path], path)
        for label in ('eoa', 'eoa_7702', 'multisig_no_timelock', 'timelock', 'governor_dao', 'frozen'):
            self.assertIn(f'/upgrade_paths/counts/{label}', expected)

    def test_no_market_fields_precise_rank_ids_or_raw_error_classes(self):
        for path in (ROOT / 'site/dist/data').glob('*.json'):
            data = json.loads(path.read_text())
            def walk(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        self.assertNotRegex(key, r'(?i)(?:market_cap(?:_usd)?$|^mcap|^max_market_cap)')
                        walk(item)
                elif isinstance(value, list):
                    for item in value:
                        walk(item)
            walk(data)
            if path.name.startswith('matrix-'):
                for row in data['rows']:
                    self.assertNotIn('rank', row)
                    self.assertIn(row['rank_band'], {'extras', '1-100', '101-500', '501-2000'})
                    for col, cell in zip(data['columns'], row['cells']):
                        if col['id'] in {'upgrade_authority_class', 'owner_path_class'}:
                            self.assertNotIn('control_partial(', str(cell[1]))
        with (ROOT / 'data/matrix/tokens.csv').open() as source:
            bands = {(r['chain'], r['token'].lower()): r['rank_band'] for r in csv.DictReader(source)}
        for chain in ('ethereum','base','arbitrum','bsc','polygon','optimism'):
            payload = json.loads((ROOT / f'site/dist/data/matrix-{chain}.json').read_text())
            for row in payload['rows']:
                expected = bands[(chain, row['token'].lower())]
                self.assertEqual(row['rank_band'], 'extras' if expected.startswith('extras') else expected)
        self.assertEqual(public_class('control_partial(You reached Public endpoint rate limit, )'), 'unresolved')
        self.assertIn('Powered by CoinGecko', (ROOT / 'site/dist/index.html').read_text())
        self.assertNotIn('Max observed market cap', (ROOT / 'site/dist/app.js').read_text())
        for name in ('paper.md', 'paper-shell.html'):
            self.assertFalse((ROOT / 'site/dist' / name).exists())


if __name__ == '__main__':
    unittest.main()
