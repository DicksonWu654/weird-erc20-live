"""Allowlisted aggregate inputs for the launch-state site refresh.

Only summary tables cross this boundary. Never load the v2 per-token frame or
the round-three local provider directory into the static site.
"""
import hashlib
import csv
import json


def uniswap_v2_points(root):
    """A strictly V2 domain; the producer's V1/V2/Sushi group is broader.

    Publish aggregate points only. Group confidence intervals cannot be reused
    for this domain; no per-token data crosses the publication boundary.
    """
    import pandas as pd
    path = root / 'data/tier2/v2/cohort_weights.csv'
    frame = pd.read_csv(path, keep_default_na=False)
    active = frame[~((frame.frame_basis == 'first_pool_month') & (frame.analysis_month < '2025-02'))]
    rows = []
    for year in sorted(active.analysis_month.str[:4].unique()):
        domain = active[(active.analysis_month.str.startswith(year)) & active.measurable
                        & active.selected_dex.eq('uniswap_v2')]
        denominator = float(domain.weight.sum())
        positives = domain[domain.headline_trade_harm]
        numerator = float(positives.weight.sum())
        def kish(group):
            return float(group.weight.sum() ** 2 / group.weight_sq.sum()) if len(group) else 0.
        rows.append({'year': year, 'share': numerator / denominator if denominator else None,
                     'denominator_weight': denominator, 'harm_weight': numerator,
                     'n_sampled': int(domain.n.sum()), 'effective_n': min(kish(domain), kish(positives))})
    return rows, {'path': 'data/tier2/v2/cohort_weights.csv',
                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def save_v2_points(root, rows, source):
    directory = root / 'scripts/site_export/derived'
    directory.mkdir(exist_ok=True)
    path = directory / 'uniswap_v2_only.csv'
    with path.open('w', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (directory / 'uniswap_v2_only.source.json').write_text(json.dumps({
        'source': source, 'domain': 'active frame; measurable; selected_dex=uniswap_v2; token creation year',
        'measure': 'sum(harm weight)/sum(domain weight); point estimates only, no supplied domain interval',
        'code': 'scripts/site_export/revision_export.py'}, indent=2) + '\n')
    return {'path': str(path.relative_to(root)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def table(root, relative, read_csv, number, text_fields, numeric_fields):
    path = root / relative
    if not path.is_file():
        raise ValueError(f'Required publication aggregate is unavailable: {relative}')
    records = []
    for row in read_csv(path):
        records.append({**{k: row[k] for k in text_fields if k in row},
                        **{k: (float(row[k]) if k in {'p_raw', 'p_holm'} and row[k] else number(row[k]))
                           for k in numeric_fields if k in row}})
    return records, {'path': relative, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def revised_aggregates(root, read_csv, number):
    result, sources = {}, []
    def load(key, relative, text, numeric):
        result[key], source = table(root, relative, read_csv, number, text.split(), numeric.split())
        sources.append(source)

    load('sensitivity', 'data/tier2/v2/sensitivity.csv',
         'period frequency denominator label scenario period_coverage identified low_n quarterly_publishable measurement_scope',
         'share ci_low ci_high effective_n n_sampled floor_wei offset_blocks no_meaningful_liquidity_share_pooled measurable_share_pooled liquidity_known_weight_share')
    load('standardized', 'data/tier2/v2/standardized.csv', 'year denominator reference_year reference_mix_json identified reason',
         'share ci_low ci_high effective_n')
    for key, group in [('venue', 'venue'), ('method', 'creation_method_group')]:
        load(key, f'data/tier2/v2/{"venue_by_year" if key == "venue" else "method_by_year"}.csv',
             f'year denominator label period_coverage identified {group}',
             'share ci_low ci_high effective_n n_sampled')
    load('bounds', 'data/tier2/v2/bounds.csv', 'period frequency denominator scope',
         'lower upper all_unresolved_upper could_not_buy_weight denominator_weight')
    load('misclassification', 'data/tier2/v2/misclass_adjusted.csv',
         'year denominator adjustment required_assumption reason',
         'observed_share adjusted_low adjusted_high strict_identified_low strict_identified_high unsupported_weight_share')
    load('validation', 'data/validate/round3/summary.csv',
         'dataset scope comparison label stratum lag_group weighting ci_method',
         'n_eligible n_excluded n n00 n01 n10 n11 agreement wilson_ci_low wilson_ci_high kappa kappa_ci_low kappa_ci_high positive_specific_agreement negative_specific_agreement pabak kappa_bootstrap_valid kappa_bootstrap_undefined target_weight_coverage')
    # Keep all main all-strata rows, and a separately selectable time-aligned slice.
    result['validation'] = [r for r in result['validation'] if r['dataset'] == 'round3_v2'
                            and r['weighting'] == 'unweighted' and r['stratum'] == 'all']
    if not result['validation']:
        raise ValueError('Round-three v2 validation rows are required')
    gp = next(r for r in result['validation'] if r['comparison'] == 'sim_vs_goplus' and r['label'] == 'honeypot' and r['lag_group'] == 'all')
    result['validation_counts'] = {'n_goplus_positive': gp['n01'] + gp['n11'],
                                   'n_simulator_positive': gp['n10'] + gp['n11']}
    for key, filename in [('rank_rates', 'prevalence_by_chain_rank'), ('ex_securities', 'prevalence_ex_securities')]:
        load(key, f'data/matrix/{filename}.csv', 'analysis_set scope chain rank_band feature measure_type definition',
             'n_tokens n_measurable n_yes n_no n_unmeasurable rate ci95_lo ci95_hi')
    load('cross_chain_tests', 'data/matrix/cross_chain_tests.csv',
         'analysis_set rank_band feature chain_a chain_b holm_family limitations',
         'yes_a n_measurable_a yes_b n_measurable_b p_raw p_holm holm_family_n')
    # The site presents Ethereum versus the rest. Holm values retain the full
    # producer family (all features and all comparisons), not a fresh correction.
    result['cross_chain_tests'] = [r for r in result['cross_chain_tests']
                                   if r['chain_a'] == 'ethereum' and r['chain_b'] == 'rest']
    result['v2_only'], source = uniswap_v2_points(root)
    sources.append(source)
    sources.append(save_v2_points(root, result['v2_only'], source))
    return result, sources
