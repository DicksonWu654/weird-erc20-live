"""Cross-source publication checks, including sparse validation and v2 joins."""
import csv
import hashlib
import json
import re
import unittest
from pathlib import Path
from export import ROOT, read_csv, compact_number


class RevisionPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = json.loads((ROOT / 'site/dist/data/index.json').read_text())
        cls.ledger = json.loads((ROOT / 'article/numbers.json').read_text())

    def test_every_shared_site_fact_is_exactly_the_current_paper_fact(self):
        for key, entry in self.index['numbers'].items():
            self.assertEqual(entry, self.ledger[key], key)
        for ref in self.index['aggregate_sources']:
            self.assertEqual(ref['sha256'], hashlib.sha256((ROOT / ref['path']).read_bytes()).hexdigest(), ref['path'])
        for column in self.index['columns']:
            self.assertNotRegex(column['label'], r'\b(?:C\d+|W\d+)\b')

    def test_rendered_aggregate_inputs_match_paper_numeric_values(self):
        annual = self.index['tier2']['year']
        validation = self.index['revision']['validation']
        compared = 0
        for key, entry in self.ledger.items():
            match = re.fullmatch(r'v2_(\d{4})_(measurable|all_new)_(share|ci_low|ci_high|denominator_effective_n|positive_effective_n)', key)
            if match:
                year, domain, column = match.groups()
                denominator = 'measurable_pooled' if domain == 'measurable' else 'headline_excl_lp'
                row = next(r for r in annual if r['year'] == year and r['denominator'] == denominator and r['label'] == 'headline_trade_harm')
            elif key.startswith('validation_') and 'numeric_value' in entry and 'column=' in entry['query']:
                where = dict(re.findall(r'(comparison|label|column)=([^,\s]+)',entry['query']))
                column = where.pop('column')
                row = next(r for r in validation if r['lag_group'] == 'all' and all(r[k] == v for k,v in where.items()))
            else:
                continue
            expected = entry.get('numeric_value')
            if expected is None:
                self.assertIsNone(row.get(column), key)
            else:
                self.assertAlmostEqual(row[column], expected, delta=1e-7, msg=key)
            compared += 1
        self.assertGreaterEqual(compared, 100, 'Shared-number check did not recognize the final ledger schema')

    def test_all_supported_headline_points_have_matching_ledger_values(self):
        for cadence in ('year',):
            for row in self.index['tier2'][cadence]:
                if row['label'] != 'headline_trade_harm' or row['denominator'] not in ('measurable_pooled', 'headline_excl_lp'):
                    continue
                if row['share'] is None or row.get('effective_n', 0) < 30 or cadence == 'quarter' and not row.get('quarterly_publishable'):
                    continue
                for field in ('share', 'ci_low', 'ci_high'):
                    ref = row.get('ledger', {}).get(field)
                    self.assertIsNotNone(ref, (cadence, row[cadence], row['denominator'], field))
                    value = ref['value'] if isinstance(ref, dict) else self.ledger[ref]['value']
                    if isinstance(ref, dict):
                        self.assertIn(value, self.ledger[ref['key']]['value'])
                    actual = float(str(value).rstrip('%')) / (100 if str(value).endswith('%') else 1)
                    # Ledger percentages are deliberately formatted for readers.
                    self.assertAlmostEqual(actual, row[field], delta=.00051)

    def test_paper_quarter_table_matches_the_site_points(self):
        table = self.ledger['v2_quarter_table']['value']
        rows = [r for r in self.index['tier2']['quarter'] if r['denominator'] == 'measurable_pooled'
                and r['label'] == 'headline_trade_harm' and r.get('quarterly_publishable')]
        cells = {}
        for line in table.splitlines()[2:]:
            values = [v.strip() for v in line.strip('|').split('|')]
            cells[values[0]] = values[1]
        self.assertEqual(set(cells), {r['quarter'] for r in rows})
        for row in rows:
            value = f"{row['share']*100:.1f}% [{row['ci_low']*100:.1f}%, {row['ci_high']*100:.1f}%]"
            self.assertEqual(cells[row['quarter']], value, row['quarter'])

    def test_validation_keeps_observed_counts_and_does_not_promote_agreement_to_accuracy(self):
        source = read_csv(ROOT / 'data/validate/round3/summary.csv')
        source = [r for r in source if r['dataset'] == 'round3_v2' and r['weighting'] == 'unweighted' and r['stratum'] == 'all']
        exported = self.index['revision']['validation']
        self.assertEqual(len(source), len(exported))
        for original, row in zip(source, exported):
            self.assertEqual(row['n'], sum(row[k] for k in ('n00', 'n01', 'n10', 'n11')))
            if row['n']:
                self.assertAlmostEqual(row['agreement'], (row['n00'] + row['n11']) / row['n'], places=7)
            for key in row:
                expected = original[key] if isinstance(row[key], str) else compact_number(original[key])
                self.assertEqual(row[key], expected, key)
            self.assertFalse(any('accuracy' in key for key in row))
        go = next(r for r in exported if r['comparison'] == 'sim_vs_goplus' and r['label'] == 'honeypot' and r['lag_group'] == 'all')
        self.assertEqual(go['n11'], 0)
        self.assertLess(go['kappa'], 0)
        self.assertEqual(go['positive_specific_agreement'], 0)

    def test_revised_aggregate_values_reproduce_saved_tables(self):
        files = {'sensitivity':'data/tier2/v2/sensitivity.csv', 'standardized':'data/tier2/v2/standardized.csv',
                 'venue':'data/tier2/v2/venue_by_year.csv', 'method':'data/tier2/v2/method_by_year.csv',
                 'bounds':'data/tier2/v2/bounds.csv', 'misclassification':'data/tier2/v2/misclass_adjusted.csv',
                 'rank_rates':'data/matrix/prevalence_by_chain_rank.csv', 'ex_securities':'data/matrix/prevalence_ex_securities.csv',
                 'cross_chain_tests':'data/matrix/cross_chain_tests.csv'}
        for dataset, path in files.items():
            source = read_csv(ROOT / path)
            if dataset == 'cross_chain_tests':
                source = [r for r in source if r['chain_a'] == 'ethereum' and r['chain_b'] == 'rest']
            rows = self.index['revision'][dataset]
            self.assertEqual(len(rows), len(source), dataset)
            for row, original in zip(rows, source):
                for key, value in row.items():
                    expected = original[key] if isinstance(value, str) else float(original[key]) if key in ('p_raw', 'p_holm') and original[key] else compact_number(original[key])
                    self.assertEqual(value, expected, (dataset, key))
                self.assertFalse({'token', 'sample_id', 'body', 'response', 'raw_body'} & row.keys())

    def test_could_not_buy_bounds_use_the_same_denominator_and_are_not_intervals(self):
        annual = {(r['year'],r['denominator'],r['label']):r for r in self.index['tier2']['year']}
        for row in self.index['revision']['bounds']:
            if row['frequency'] != 'annual' or row['lower'] is None:
                continue
            key = (row['period'],row['denominator'])
            harm = annual[(*key,'headline_trade_harm')]['share']
            buy = annual[(*key,'could_not_buy')]['share']
            self.assertAlmostEqual(row['lower'], harm, places=7)
            self.assertAlmostEqual(row['upper'] - row['lower'], buy, places=7)
            self.assertLessEqual(row['lower'], row['upper'])
            self.assertLessEqual(row['upper'], row['all_unresolved_upper'])
            self.assertLessEqual(row['all_unresolved_upper'], 1)

    def test_below_floor_share_is_monotone_without_imposing_monotone_harm(self):
        rows = self.index['revision']['sensitivity']
        for year in {r['period'] for r in rows if r['frequency'] == 'annual'}:
            shares = [next(r for r in rows if r['period'] == year and r['frequency'] == 'annual'
                           and r['denominator'] == 'measurable_pooled' and r['scenario'] == scenario)
                      ['no_meaningful_liquidity_share_pooled']
                      for scenario in ['floor_1e9_wei','primary','floor_0.1_eth']]
            if all(v is not None for v in shares):
                self.assertEqual(shares, sorted(shares), year)

    def test_strict_v2_series_reproduces_an_independent_sql_domain_query(self):
        import duckdb
        connection = duckdb.connect()
        try:
            rows = connection.execute("""
                SELECT substr(analysis_month,1,4) AS year,
                    sum(CASE WHEN measurable AND selected_dex='uniswap_v2' THEN weight ELSE 0 END) AS denominator,
                    sum(CASE WHEN measurable AND selected_dex='uniswap_v2' AND headline_trade_harm THEN weight ELSE 0 END) AS harm,
                    count(*) FILTER (WHERE measurable AND selected_dex='uniswap_v2') AS sampled
                FROM read_parquet(?)
                WHERE NOT (frame_basis='first_pool_month' AND analysis_month<'2025-02')
                GROUP BY year ORDER BY year
            """, [str(ROOT / 'data/tier2/v2/token_results.parquet')]).fetchall()
        finally:
            connection.close()
        exported = self.index['revision']['v2_only']
        self.assertEqual(len(rows),len(exported))
        for (year,denominator,harm,sampled), row in zip(rows,exported):
            self.assertEqual(row['year'],year)
            self.assertEqual(row['n_sampled'],sampled)
            self.assertAlmostEqual(row['denominator_weight'],denominator,delta=max(1e-5,abs(denominator)*1e-12))
            if denominator:
                self.assertAlmostEqual(row['share'],harm/denominator,places=12)
                self.assertAlmostEqual(row['share'],self.ledger[f'v2_{year}_uniswap_v2_only']['numeric_value'],places=12)
            else:
                self.assertIsNone(row['share'])
                self.assertEqual(self.ledger[f'v2_{year}_uniswap_v2_only']['value'], 'Not reported (effective support <30)' if row['effective_n'] < 30 else 'NA')


if __name__ == '__main__':
    unittest.main()
