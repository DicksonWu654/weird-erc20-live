"""Publication-boundary checks: predicates, shared facts and long-form monthly data."""
import json
import csv
import unittest

from export import ROOT, CHAINS, compact_monthly_shares, compact_number, publish_numbers, public_class
from tier2_export import compact_shares, attach_ledger
from unittest.mock import patch
import tempfile
import hashlib
from pathlib import Path


def write_synthetic_monthly(path):
    """Revised fixture: low support, missing month, NA frame gap, overlapping flags."""
    fields = ['month', 'denominator', 'label', 'n_sampled', 'share', 'ci_low', 'ci_high',
              'est_count', 'effective_n', 'low_n', 'identified', 'period_coverage', 'token', 'raw_body']
    labels = dict(honeypot=.1, high_tax=.1, owner_controlled=.1, normal=.3,
                  no_pool=.2, dust_pool=.1, unprobeable=.1, pending_v4=0,
                  headline_trade_harm=.2, owner_control_capability=.3)
    with path.open('w') as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for month in ['2025-01', '2025-02', '2025-04']:
            for denominator in ['headline_excl_lp', 'measurable_pooled']:
                for label, share in labels.items():
                    unknown = month == '2025-02' and denominator == 'headline_excl_lp'
                    writer.writerow(dict(month=month, denominator=denominator, label=label, n_sampled=40,
                                         share='' if unknown else share, ci_low='' if unknown else max(0, share-.05),
                                         ci_high='' if unknown else min(1, share+.05), est_count=share*1000,
                                         effective_n=4 if month == '2025-01' else 40, low_n=month == '2025-01',
                                         identified=not unknown, period_coverage='undefined_missing_2025-02_prefix' if unknown else 'full_frame_scope',
                                         token='DO_NOT_PUBLISH_SYNTHETIC_TOKEN', raw_body='DO_NOT_PUBLISH_RAW'))


class MonthlySchemaTests(unittest.TestCase):
    def test_long_schema_keeps_denominator_outcome_intervals_and_exclusions(self):
        rows = []
        for denominator in ('headline_excl_lp', 'pooled_tokens', 'measurable_pooled_tokens'):
            for label in ('normal', 'dust_pool', 'no_pool', 'unprobeable', 'pending_v4'):
                rows.append(dict(month='2026-09', denominator=denominator, label=label,
                                 n_sampled='200', share='0.2', ci_low='0.1', ci_high='0.3',
                                 est_count='25', creation_method='all', token='must-not-ship', raw_body='private'))
        result = compact_monthly_shares(rows)
        self.assertEqual(len(result), 15)
        self.assertEqual(result[0]['ci_low'], .1)
        self.assertEqual(result[0]['n_sampled'], 200)
        self.assertEqual({r['denominator'] for r in result}, {r['denominator'] for r in rows})
        self.assertTrue(all('token' not in r and 'raw_body' not in r for r in result))
        self.assertIn('unprobeable', {r['label'] for r in result})

    def test_empty_share_is_unknown_and_zero_is_measured_zero(self):
        rows = [dict(month='2026-09', denominator='pooled_tokens', label='normal', share=''),
                dict(month='2026-09', denominator='pooled_tokens', label='honeypot', share='0')]
        self.assertIsNone(compact_monthly_shares(rows)[0]['share'])
        self.assertEqual(compact_monthly_shares(rows)[1]['share'], 0)

    def test_invalid_and_reversed_intervals_rejected(self):
        for values in ({'share': '1.2'}, {'share': '.5', 'ci_low': '.8', 'ci_high': '.2'}):
            with self.assertRaises(ValueError):
                compact_monthly_shares([dict(month='2026-09', denominator='pooled_tokens', label='normal', **values)])


class RevisedSchemaTests(unittest.TestCase):
    def test_flags_na_and_support_survive_all_cadences(self):
        for period, value in [('month', '2025-02'), ('quarter', '2025Q1'), ('year', '2025')]:
            row = {period: value, 'denominator': 'headline_excl_lp', 'label': 'headline_trade_harm',
                   'share': '0', 'ci_low': '0', 'ci_high': '.1', 'identified': 'False', 'low_n': 'True',
                   'quarterly_publishable': 'False', 'effective_n': '4', 'period_coverage': 'restricted_excludes_2025-02'}
            result = compact_shares([row], compact_number, period)[0]
            self.assertIsNone(result['share'])
            self.assertIsNone(result['ci_low'])
            self.assertTrue(result['low_n'])
            self.assertFalse(result['quarterly_publishable'])
            self.assertEqual(result['effective_n'], 4)
            self.assertEqual(result['period_coverage'], row['period_coverage'])

    def test_obsolete_scam_union_and_rugged_cannot_enter_publication(self):
        rows = [dict(month='2026-09', denominator='measurable_pooled', label=label, share='.2')
                for label in ['scam_any', 'rugged', 'headline_trade_harm', 'owner_control_capability', 'liquidity_pulled', 'insider_rug']]
        self.assertEqual({r['label'] for r in compact_monthly_shares(rows)},
                         {'headline_trade_harm', 'owner_control_capability', 'liquidity_pulled', 'insider_rug'})

    def test_missing_ledger_keys_do_not_break_export(self):
        with patch('export.read_json', return_value={}):
            self.assertEqual(publish_numbers(), ({}, None))

    def test_only_current_exact_query_can_supply_a_headline(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'annual_shares.csv'
            source.write_text('year,share\n2026,.12\n')
            row = dict(year='2026', denominator='measurable_pooled', label='headline_trade_harm', share=.12)
            entry = dict(value='12.0%', source_file='data/tier2/annual_shares.csv',
                         query='year=2026,denominator=measurable_pooled,label=headline_trade_harm,column=share',
                         source_sha256={'data/tier2/annual_shares.csv': 'stale'})
            self.assertNotIn('ledger', attach_ledger([dict(row)], 'year', source, {'arbitrary_key': entry})[0])
            entry['source_sha256']['data/tier2/annual_shares.csv'] = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(attach_ledger([dict(row)], 'year', source, {'arbitrary_key': entry})[0]['ledger'], {'share': 'arbitrary_key'})
            self.assertNotIn('ledger', attach_ledger([dict(row, label='insider_rug')], 'year', source, {'arbitrary_key': entry})[0])

    def test_quarterly_table_values_remain_ledger_references(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'quarterly_shares.csv'
            source.write_text('quarter,share\n2024Q2,.12\n')
            row = dict(quarter='2024Q2', denominator='headline_excl_lp', label='headline_trade_harm', share=.12)
            entry = dict(value='| Creation quarter | Measurable pooled (nominal 95% CI) | All new non-LP (nominal 95% CI) |\n'
                               '| --- | --- | --- |\n| 2024Q2 | 20.0% [18.0%, 22.0%] | 12.0% [10.0%, 14.0%] |',
                         query='label=headline_trade_harm; quarterly_publishable=true',
                         source_sha256={'data/tier2/quarterly_shares.csv': hashlib.sha256(source.read_bytes()).hexdigest()})
            result = attach_ledger([row], 'quarter', source, {'tier2_quarterly_table': entry})[0]
            self.assertEqual(result['ledger']['share']['value'], '12.0%')
            self.assertEqual(result['ledger']['ci_high']['value'], '14.0%')
            self.assertEqual(result['ledger']['share']['key'], 'tier2_quarterly_table')


class BuiltPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = json.loads((ROOT / 'site/dist/data/index.json').read_text())
        cls.matrix = json.loads((ROOT / 'data/matrix/matrix.json').read_text())

    def test_every_exported_cell_preserves_the_source_predicate_and_decoded_value(self):
        source = {(r['c'], r['t'].lower()): r for r in self.matrix['rows']}
        checked = 0
        for chain in CHAINS:
            payload = json.loads((ROOT / f'site/dist/data/matrix-{chain}.json').read_text())
            for row in payload['rows']:
                original = source[chain, row['token'].lower()]
                for i, (column, cell) in enumerate(zip(payload['columns'], row['cells'])):
                    raw = original['x'][i]
                    expected = {'y': 'quirk', 'n': 'pass', 'u': 'unprobeable', 'e': 'fail'}[raw[0]]
                    if column['kind'] == 'meta':
                        expected = 'meta'
                        self.assertEqual(cell[3], raw[0])
                    self.assertEqual(cell[0], expected, (chain, row['token'], column['id']))
                    self.assertEqual(cell[1], (public_class(raw[1]) if len(raw) > 1 else 'not_applicable' if raw[0] == 'n' else 'unresolved') if column['id'] in {'upgrade_authority_class', 'owner_path_class'} else raw[1] if len(raw) > 1 else '')
                    self.assertTrue(column['title'])
                    checked += 1
        self.assertEqual(checked, len(self.matrix['rows']) * len(self.index['columns']))

    def test_headlines_and_scalar_findings_are_the_paper_ledger(self):
        facts, digest = publish_numbers()
        self.assertEqual(self.index['numbers'], facts)
        self.assertEqual(self.index['numbers_source']['sha256'], digest)
        self.assertEqual(self.index['headline']['tokens'], int(facts['n_universe']['value']))
        self.assertEqual(self.index['headline']['ranked'], int(facts['n_main']['value']))
        self.assertEqual(self.index['headline']['months'], int(facts['census_months']['value']))

    def test_bnb_no_return_does_not_claim_a_transfer_revert(self):
        payload = json.loads((ROOT / 'site/dist/data/matrix-ethereum.json').read_text())
        bnb = next(r for r in payload['rows'] if r['symbol'] == 'BNB')
        i = next(i for i, c in enumerate(payload['columns']) if c['id'] == 'transfer_reverts_fresh_contract')
        self.assertEqual(bnb['cells'][i][:2], ['pass', 'no_return_value'])
        self.assertEqual(bnb['cells'][0][0], 'meta')

    def test_revised_period_tables_preserve_na_and_headline_support(self):
        from export import read_csv
        for period, filename in [('month', 'monthly_shares.csv'), ('quarter', 'quarterly_shares.csv'), ('year', 'annual_shares.csv')]:
            rows = self.index['scam_shares'] if period == 'month' else self.index['tier2'][period]
            source = {(r[period], r['denominator'], r['label']): r for r in read_csv(ROOT / 'data/tier2/v2' / filename)}
            for row in rows:
                original = source[(row[period], row['denominator'], row['label'])]
                if original['share'] == '' or original['identified'].lower() == 'false':
                    self.assertIsNone(row['share'])
                    self.assertIsNone(row['ci_low'])
                self.assertEqual(row['low_n'], original['low_n'].lower() == 'true')
                if original['quarterly_publishable']:
                    self.assertEqual(row['quarterly_publishable'], original['quarterly_publishable'].lower() == 'true')
                self.assertEqual(row['period_coverage'], original['period_coverage'])
                self.assertNotIn(row['label'], {'scam_any', 'rugged'})


if __name__ == '__main__':
    unittest.main()
