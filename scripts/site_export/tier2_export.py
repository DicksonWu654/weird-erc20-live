"""Publication schema for revised trade, capability and LP-withdrawal aggregates."""
from __future__ import annotations
import hashlib
import re
from pathlib import Path

TRADE = {'honeypot', 'high_tax', 'owner_controlled', 'normal', 'no_pool', 'dust_pool', 'unprobeable', 'pending_v4',
         'could_not_buy', 'v3_fot_incompatible', 'no_meaningful_liquidity', 'late_pool'}
DERIVED = {'headline_trade_harm', 'owner_control_capability', 'liquidity_pulled', 'insider_rug'}
CORE_DENOMINATORS = ('headline_excl_lp', 'measurable_pooled')


def compact_shares(rows, number, period='month'):
    text_fields = {period, 'denominator', 'label', 'creation_method', 'dex', 'stratum', 'period_coverage', 'measurement_scope',
                   'venue', 'creation_method_group'}
    numeric_fields = {'n_sampled', 'share', 'ci_low', 'ci_high', 'est_count', 'denominator_effective_n',
                      'effective_n', 'positive_effective_n', 'phase2_effective_n', 'identification_low',
                      'identification_high', 'phase2_checked', 'phase2_positive', 'phase2_unknown',
                      'capability_unassessed', 'v4_unassessed_weight_share', 'dust_floor_wei', 'dust_floor_eth'}
    flags = {'low_n', 'quarterly_publishable', 'identified'}
    result = []
    seen = set()
    for row in rows:
        if row.get('label') not in TRADE | DERIVED or not row.get(period) or not row.get('denominator'):
            continue
        item = {k: v for k, v in row.items() if k in text_fields and v != ''}
        for key in numeric_fields:
            if row.get(key) not in (None, '') or key in {'share', 'ci_low', 'ci_high'}:
                item[key] = number(row.get(key))
        for key in flags:
            value = row.get(key)
            if value not in (None, ''):
                if str(value).lower() not in ('true', 'false', '1', '0'):
                    raise ValueError(f'Invalid Tier-2 boolean {key}: {value}')
                item[key] = str(value).lower() in ('true', '1')
        for key in ('share', 'ci_low', 'ci_high'):
            value = item.get(key)
            if value is not None and (not isinstance(value, (int, float)) or not 0 <= value <= 1):
                raise ValueError(f'Invalid Tier-2 {key}: {item}')
        if item.get('ci_low') is not None and item.get('ci_high') is not None and item['ci_low'] > item['ci_high']:
            raise ValueError(f'Reversed Tier-2 interval: {item}')
        # A finite placeholder cannot turn an explicitly unidentified point into zero.
        if item.get('identified') is False:
            item.update(share=None, ci_low=None, ci_high=None, est_count=None)
        key = tuple(item.get(k) for k in (period, 'denominator', 'label', 'creation_method', 'dex', 'stratum', 'dust_floor_wei', 'dust_floor_eth', 'venue', 'creation_method_group'))
        if key in seen:
            raise ValueError(f'Duplicate Tier-2 estimate: {key}')
        seen.add(key)
        # Trade rows have no second-stage design. Avoid shipping repetitive zero fields.
        if item['label'] not in {'liquidity_pulled', 'insider_rug'}:
            for key in ('phase2_checked', 'phase2_positive', 'phase2_unknown', 'phase2_effective_n', 'v4_unassessed_weight_share'):
                item.pop(key, None)
        if item['label'] != 'owner_control_capability':
            item.pop('capability_unassessed', None)
        result.append(item)
    return result


def attach_ledger(rows, period, source: Path, facts):
    """Resolve values by the paper's source/query selectors, independent of key names.

    A stale ledger value cannot become a revised headline merely because its key
    resembles a current one. Only a matching CSV hash and exact selector count.
    Missing keys are a pending state, never a build failure or CSV fallback.
    """
    if not source.exists():
        return rows
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source_name = ('data/tier2/v2/' if source.parent.name == 'v2' else 'data/tier2/') + source.name
    lookup = {}
    table_lookup = {}
    for key, entry in facts.items():
        if entry.get('source_sha256', {}).get(source_name) != digest:
            continue
        query = entry.get('query', '')
        selectors = dict(re.findall(r'\b(month|quarter|year|denominator|label|column)\s*=\s*([^,;\s]+)', query))
        if all(k in selectors for k in (period, 'denominator', 'label', 'column')):
            lookup[(selectors[period], selectors['denominator'], selectors['label'], selectors['column'])] = key
        # The paper publishes every supported quarter in an evidence-backed
        # table, while only selected quarters have individual scalar keys.
        # Read those cells from the ledger itself, never synthesize new facts.
        if key not in {'tier2_annual_table', 'tier2_quarterly_table'} or selectors.get('label') != 'headline_trade_harm':
            continue
        lines = str(entry.get('value', '')).splitlines()
        if len(lines) < 3:
            continue
        header = [cell.strip().lower() for cell in lines[0].strip('|').split('|')]
        denominator_columns = {den: next((i for i, title in enumerate(header)
                                          if title.startswith('measurable pooled' if den == 'measurable_pooled' else 'all new non-lp')), None)
                               for den in CORE_DENOMINATORS}
        for line in lines[2:]:
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            if not cells:
                continue
            match = re.match(r'^(\d{4}Q[1-4]|\d{4})(?:\s|$)', cells[0])
            if not match:
                continue
            for den, column in denominator_columns.items():
                if column is None or column >= len(cells):
                    continue
                interval = re.fullmatch(r'(\d+(?:\.\d+)?%)\s*\[(\d+(?:\.\d+)?%),\s*(\d+(?:\.\d+)?%)\]', cells[column])
                if not interval:
                    continue
                for field, value in zip(('share', 'ci_low', 'ci_high'), interval.groups()):
                    table_lookup[(match[1], den, 'headline_trade_harm', field)] = {
                        'key': key, 'value': value, 'row': cells[0], 'column': header[column]}
    for row in rows:
        row['ledger'] = {field: lookup[(row[period], row['denominator'], row['label'], field)]
                         for field in ('share', 'ci_low', 'ci_high', 'effective_n', 'n_sampled')
                         if (row[period], row['denominator'], row['label'], field) in lookup}
        if not row['ledger']:
            row.pop('ledger')
        for field in ('share', 'ci_low', 'ci_high'):
            ref = table_lookup.get((row[period], row['denominator'], row['label'], field))
            if ref and field not in row.get('ledger', {}):
                row.setdefault('ledger', {})[field] = ref
    return rows
