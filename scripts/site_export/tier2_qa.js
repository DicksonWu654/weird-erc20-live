async (page) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  await page.unroute('**/data/index.json');
  await page.goto('http://127.0.0.1:4173/');
  await page.locator('#tier2-chart svg').waitFor();
  const originalIndex = await page.request.get('http://127.0.0.1:4173/data/index.json').then((r) => r.json());
  const synthetic = [];
  const labels = {honeypot:.1, high_tax:.1, owner_controlled:.1, normal:.3, no_pool:.2, dust_pool:.1, unprobeable:.1, headline_trade_harm:.2, owner_control_capability:.3};
  for (const month of ['2025-01','2025-02','2025-04']) for (const denominator of ['headline_excl_lp','measurable_pooled']) for (const [label, share] of Object.entries(labels)) {
    const unknown = month === '2025-02' && denominator === 'headline_excl_lp';
    synthetic.push({month, denominator, label, share:unknown ? null : share, ci_low:unknown ? null : Math.max(0,share-.05), ci_high:unknown ? null : share+.05, effective_n:month === '2025-01' ? 4 : 40, low_n:month === '2025-01', identified:!unknown, period_coverage:unknown ? 'undefined_missing_2025-02_prefix' : 'full_frame_scope'});
  }
  const index = structuredClone({...originalIndex, scam_shares:synthetic});
  synthetic.forEach((r,i) => { index.numbers.site_counts.value[`/scam_shares/${i}/effective_n`] = r.effective_n; });
  await page.route('**/data/index.json', (route) => route.fulfill({json:index}));
  await page.reload(); await page.locator('#tier2-chart svg').waitFor();
  check(!JSON.stringify(index.scam_shares).includes('DO_NOT_PUBLISH'), 'Token/raw columns escaped the aggregate allowlist');
  await page.selectOption('#tier2-denominator', 'headline_excl_lp');
  check(await page.locator('#tier2-chart [data-period="2025-01"][data-low-n="true"]').count() === 0, 'Low-n observation escaped suppression');
  check(await page.locator('#tier2-chart [data-period="2025-02"]').count() === 0, 'NA point is plotted as zero');
  check(await page.locator('#tier2-chart [data-period="2025-03"]').count() === 0, 'Absent month was filled');
  check(await page.locator('#tier2-chart [data-period="2025-04"]').count() === 1, 'Isolated month vanished');
  await page.selectOption('#tier2-month', '2025-02');
  check((await page.locator('#monthly-details tr[data-label="headline_trade_harm"]').textContent()).includes('NA'), 'NA table point became zero');
  await page.selectOption('#tier2-denominator', 'measurable_pooled');
  check(await page.locator('#tier2-chart [data-period="2025-02"]').count() === 1, 'Pooled February wrongly inherits all-new gap');
  await page.selectOption('#tier2-month', '2025-02');
  check(await page.locator('#monthly-details tr[data-label="pending_v4"]').count() === 0, 'Inactive unresolved-outcome row was not hidden');
  check((await page.locator('#monthly-details tr[data-label="no_pool"]').textContent()).includes('Structurally excluded'), 'Structural exclusion became a measured zero');
  await page.selectOption('#tier2-label', 'owner_control_capability');
  check((await page.locator('#tier2-label').inputValue()) === 'owner_control_capability', 'Capability has no separate view');
  await page.selectOption('#tier2-label', 'headline_trade_harm');
  const captures = [];
  for (const [device, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ['dark', 'light']) {
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
        document.documentElement.style.scrollBehavior = 'auto';
        document.activeElement?.blur();
        if (!document.querySelector('#synthetic-note')) {
          const note = document.createElement('p'); note.id = 'synthetic-note';
          note.textContent = 'SYNTHETIC SCHEMA CHECK · NOT PUBLICATION DATA'; note.style.color = 'var(--coral)';
          document.querySelector('#tier2-monthly').prepend(note);
        }
      }, theme);
      await page.waitForTimeout(350);
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Synthetic view overflows ${device}/${theme}`);
      const path = `screenshots/r3/${device}-${theme}/tier2-synthetic.png`;
      await page.locator('#tier2-monthly').screenshot({ path, animations: 'disabled' }); captures.push(path);
    }
  }
  // Missing scalar ledger entries cannot crash the application or create zero headlines.
  await page.route('**/data/index.json', async (route) => {
    await route.fulfill({ json: { ...index, numbers: {site_counts: {...index.numbers.site_counts, value: {...index.numbers.site_counts.value, '/headline/tokens':null,'/headline/chains':null,'/headline/months':null,'/headline/ranked':null}}}, headline: { tokens: null, chains: null, months: null, ranked: null } } });
  });
  await page.reload();
  await page.locator('.virtual-rows [role=row]').first().waitFor();
  check(!/NaN|undefined/.test(await page.locator('#trade-headline-cards').textContent()), 'Missing scalar facts broke aggregate headlines');
  check(await page.locator('#trade-period-chart circle').count() > 0, 'Verified aggregate points disappeared with unrelated scalar keys');
  check(await page.locator('#stat-tokens').textContent() === '—', 'Missing hero number became zero');
  await page.unroute('**/data/index.json');
  await page.route('**/data/index.json', async (route) => {
    await route.fulfill({ json: { ...index, scam_shares: [], tier2: { year: [], quarter: [] } } });
  });
  await page.reload(); await page.locator('#scam-layer .pending-card').waitFor();
  check(await page.locator('#tier2-chart').count() === 0, 'Absent aggregates did not show the unavailable state');
  await page.unroute('**/data/index.json');
  check(errors.length === 0, errors.join('; '));
  return { checks: 'passed', errors, captures, tested: ['uniform low-n suppression', 'NA vs measured zero', 'absent-month gap', 'isolated observation', 'separate capability', 'all-new vs pooled frame gap', 'missing ledger keys', 'missing aggregates'] };
}
