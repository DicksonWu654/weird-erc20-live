async (page) => {
  // Run with playwright-cli run-code --filename ../scripts/site_export/visual_qa.js
  // from site/. These captures are review artifacts, outside the deploy bundle.
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  await page.unroute('**/data/index.json');
  await page.goto('http://127.0.0.1:4173/');
  await page.evaluate(() => { document.documentElement.style.scrollBehavior = 'auto'; });
  await page.locator('.virtual-rows [role=row]').first().waitFor();
  const featureCount = await page.request.get('http://127.0.0.1:4173/data/index.json').then((r) => r.json()).then((data) => data.columns.length);
  check(await page.locator('.grid-head [role=columnheader]').count() === featureCount + 1, 'Missing real column headers');
  check(!await page.locator('#matrix-table').evaluate((el) => el.textContent.includes('<div class=')), 'Header is still escaped HTML');
  check(await page.locator('#feature-filter option').count() === featureCount, 'Some features are inaccessible');
  check(await page.locator('.feature-head').evaluateAll((heads) => heads.every((el) => el.scrollHeight <= el.clientHeight + 1)), 'A feature header is clipped');
  check(await page.locator('.virtual-rows .matrix-cell').evaluateAll((cells) => cells.every((el) => {
    const labels = { pass: 'No', quirk: 'Yes', fail: 'Error', unprobeable: 'Unknown' };
    return Object.entries(labels).every(([status, label]) => !el.classList.contains(status) || el.textContent === label);
  })), 'A decoded value is being mistaken for a predicate answer');

  await page.keyboard.press('Control+Home');
  await page.keyboard.press('Tab');
  check(await page.locator('.skip-link').evaluate((el) => el.matches(':focus-visible')), 'Skip link must appear on keyboard focus');
  await page.keyboard.press('Enter');
  check(await page.locator('.skip-link').evaluate((el) => getComputedStyle(el).clipPath === 'inset(50%)'), 'Skip link remains visible after activation');
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.locator('#matrix').scrollIntoViewIfNeeded();
    await page.evaluate(() => document.querySelector('#matrix').scrollIntoView({ block: 'start', behavior: 'instant' }));
    await page.waitForTimeout(100);
    for (const id of ['chain-filter', 'rank-filter', 'category-filter', 'behaviour-filter', 'token-search']) {
      const usable = await page.locator(`#${id}`).evaluate((el) => {
        const r = el.getBoundingClientRect();
        return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === el;
      });
      check(usable, `${id} is covered at ${width}px`);
    }
    await page.locator('#matrix-frame').evaluate((el) => { el.scrollLeft = el.scrollWidth; });
    await page.waitForTimeout(100);
    const pinned = await page.evaluate(() => {
      const frame = document.querySelector('#matrix-frame').getBoundingClientRect();
      const token = document.querySelector('.virtual-rows .token-cell').getBoundingClientRect();
      const last = document.querySelector('.grid-head').lastElementChild.getBoundingClientRect();
      return Math.abs(token.left - frame.left - 1) < 2 && last.right <= frame.right + 1;
    });
    check(pinned, `Token column or final feature is not reachable at ${width}px`);
    await page.locator('#matrix-frame').evaluate((el) => { el.scrollLeft = 0; });
  }
  await page.selectOption('#family-filter', 'hooks');
  check((await page.locator('.grid-head [role=columnheader]').count()) < 10, 'Family selector does not narrow columns');
  await page.selectOption('#family-filter', 'all');
  check(await page.locator('.grid-head [role=columnheader]').count() === featureCount + 1, 'Family selector cannot restore all features');
  for (const chain of ['base', 'arbitrum', 'bsc', 'polygon', 'optimism', 'ethereum']) {
    await page.selectOption('#chain-filter', chain);
    await page.waitForFunction((chain) => document.querySelector('#matrix-count').textContent.endsWith(`${chain} inventory`), chain);
  }
  await page.selectOption('#chain-filter', 'base');
  await page.waitForFunction(() => document.querySelector('#matrix-count').textContent.endsWith('base inventory'));
  await page.fill('#token-search', '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913');
  await page.waitForFunction(() => document.querySelector('#matrix-count').textContent.startsWith('Matching tokens'));
  check(await page.locator('.virtual-rows [role=rowheader]').first().textContent().then((s) => s.includes('USDC')), 'Search used the previous chain');
  await page.fill('#token-search', '');
  await page.selectOption('#chain-filter', 'ethereum');
  await page.waitForFunction(() => document.querySelector('#matrix-count').textContent.endsWith('ethereum inventory'));
  await page.getByRole('gridcell', { name: 'USDT Probe funding: access list', exact: true }).click();
  for (const label of ['Token', 'Decoded value', 'Block', 'Call id', 'sha256', 'Raw pointer']) {
    check((await page.locator('#evidence-dialog').textContent()).includes(label), `Missing evidence field ${label}`);
  }
  await page.keyboard.press('Escape');
  await page.fill('#token-search', '0xb8c77482e45f1f44de1745f52c74426c631bdd52');
  await page.getByRole('gridcell', { name: 'BNB Fresh contract transfer reverts: No', exact: true }).click();
  check((await page.locator('#evidence-dialog').textContent()).includes('no return value'), 'BNB decoded evidence was lost');
  await page.keyboard.press('Escape');
  await page.fill('#token-search', '');
  const index = await page.request.get('http://127.0.0.1:4173/data/index.json').then((r) => r.json());
  check(!index.validation_round2 && !index.validation, 'Obsolete provider export leaked into download');
  const validationHeading = 'Agreement measures consistency, not accuracy.';
  check((await page.locator('#validation-state').textContent()).includes(validationHeading), 'Validation phase is mislabeled');

  check((await page.locator('#self-checks').textContent()).includes('0/35'), 'Missing pre-fix blind finding');
  check((await page.locator('#self-checks').textContent()).includes('16 remaining cases'), 'Missing neutral blind-case counts');
  check((await page.locator('#b20-validation-copy').textContent()).includes('refute 2 (NVDAc, OUSD)'), 'B20 overclaims sample');
  check((await page.locator('#validation-state').textContent()).includes('κ intervals'), 'Missing kappa interval scope');
  if (index.scam_shares.length) {
    check(await page.locator('#tier2-chart [data-period]').count() > 0, 'Monthly trade series is empty');
    check(await page.locator('#tier2-chart [data-low-n="true"]').count() === 0, 'Low-support monthly estimate is plotted');
    const strata = await page.locator('#tier2-denominator option').evaluateAll((opts) => opts.map((o) => o.value));
    for (const stratum of strata) {
      await page.selectOption('#tier2-denominator', stratum);
      for (const label of ['No meaningful liquidity', 'No pool', 'Unprobeable']) {
        check((await page.locator('#monthly-details').textContent()).includes(label), `Excluded outcome missing: ${label}`);
      }
    }
    await page.selectOption('#tier2-denominator', 'headline_excl_lp');
    await page.selectOption('#tier2-month', '2025-02');
    check((await page.locator('#monthly-details tr[data-label="headline_trade_harm"]').textContent()).includes('NA') || (await page.locator('#monthly-details tr[data-label="headline_trade_harm"]').textContent()).includes('Not reported'), 'Unidentified February point became zero');
    check(await page.locator('#tier2-chart [data-period="2025-02"]').count() === 0, 'Unidentified point is plotted');
    await page.selectOption('#tier2-denominator', 'measurable_pooled');
    await page.selectOption('#tier2-month', '2026-09');
    check(await page.locator('#tier2-rug select, #tier2-rug table, #rug-chart').count() === 0, 'LP reconstruction is still an empty widget');
    await page.locator('#quarterly-view').click();
    check((await page.locator('#trade-period-table').textContent()).includes('Not reported'), 'Sparse/incomplete quarters were published');
    const quarterPoints = index.tier2.quarter.filter((r) => ['headline_excl_lp', 'measurable_pooled'].includes(r.denominator) && r.label === 'headline_trade_harm' && r.quarterly_publishable && r.effective_n >= 30 && r.share !== null);
    check(await page.locator('#trade-period-chart circle').count() === quarterPoints.length, 'Quarterly ledger values or publication gate were lost');
    await page.locator('#annual-view').click();
    const annualPoints = index.tier2.year.filter((r) => ['headline_excl_lp', 'measurable_pooled'].includes(r.denominator) && r.label === 'headline_trade_harm' && r.effective_n >= 30 && r.share !== null);
    check(await page.locator('#trade-period-chart circle').count() === annualPoints.length, 'Annual ledger values were lost');
    for (const [cadence, period] of [['year', '2026'], ['quarter', '2026Q3']]) {
      await page.locator(cadence === 'year' ? '#annual-view' : '#quarterly-view').click();
      const expected = index.tier2[cadence].filter((r) => r[cadence] === period && ['headline_excl_lp', 'measurable_pooled'].includes(r.denominator) && r.label === 'headline_trade_harm');
      for (const row of expected) {
        const ref = row.ledger?.share;
        const value = typeof ref === 'object' ? ref.value : index.numbers[ref]?.value;
        if (value) check((await page.locator('#trade-headline-cards').textContent()).includes(value), 'Headline differs from the paper ledger');
      }
    }
    await page.locator('#annual-view').click();
    for (const id of ['check-rug', 'check-stats']) check(await page.locator(`#${id}`).count() === 1, `Missing ${id}`);
  }
  if (index.verification.postfix.length) {
    const table = await page.locator('.postfix-results').textContent();
    for (const label of ['Protocol', 'Metric', 'Estimate', 'precision', 'recall']) check(table.includes(label), `Precision table lost ${label}`);
  }
  const sections = ['hero', 'findings', 'headlines', 'matrix-intro', 'matrix', 'compare', 'timeline', 'stories', 'story-b20', 'story-validation', 'story-control', 'self-checks', 'check-rug', 'check-stats', 'tier2-headline', 'tier2-monthly', 'tier2-rug', 'methods'];
  const captures = [];
  for (const [device, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height });
    await page.waitForTimeout(350);
    for (const theme of ['dark', 'light']) {
      await page.evaluate((theme) => { document.documentElement.dataset.theme = theme; document.activeElement?.blur(); }, theme);
      // Let the theme styles and glyph paints settle before taking a clipped
      // section screenshot. Chromium can otherwise capture an accent-filled
      // button/card before its inherited text color is repainted.
      await page.evaluate(() => document.fonts?.ready);
      await page.waitForTimeout(500);
      // The site uses smooth anchor scrolling. Disable it for deterministic
      // clipped captures so a section is fully painted before the screenshot.
      await page.evaluate(() => { document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, 0); });
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Page overflows horizontally: ${device}/${theme}`);
      await page.locator('#matrix-frame').evaluate((el) => { el.scrollTop = 0; el.scrollLeft = 0; });
      for (const section of sections) {
        const path = `screenshots/r3/visual-${device}-${theme}/${section}.png`;
        const locator = page.locator(`#${section}`);
        // Tall element captures can resize Chromium; wait for responsive charts.
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            await page.waitForTimeout(250);
            await locator.scrollIntoViewIfNeeded();
            await page.waitForTimeout(150);
            await locator.screenshot({ path, animations: 'disabled' });
            break;
          } catch (error) { if (attempt === 2) throw new Error(`${section}: ${error.message}`); await page.waitForTimeout(500); }
        }
        captures.push(path);
      }
      await page.waitForTimeout(350);
      await page.locator('#quarterly-view').click();
      await page.waitForTimeout(350);
      await page.locator('#tier2-headline').scrollIntoViewIfNeeded();
      await page.waitForTimeout(250);
      await page.locator('#tier2-headline').screenshot({ path: `screenshots/r3/visual-${device}-${theme}/tier2-quarterly.png`, animations: 'disabled' });
      await page.locator('#annual-view').click();
      if (theme === 'dark') {
        await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
        await page.screenshot({ path: `screenshots/r3/visual-${device}.png`, fullPage: true, animations: 'disabled' });
      }
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  await page.selectOption('#chain-filter', 'base');
  await page.waitForFunction(() => document.querySelector('#matrix-count').textContent.endsWith('base inventory'));
  await page.locator('#matrix').screenshot({ path: 'screenshots/r3/visual-base.png', animations: 'disabled' });
  check(errors.length === 0, `Browser errors: ${errors.join('; ')}`);
  return { checks: 'passed', browserErrors: errors, sectionCaptures: captures.length, captures };
}
