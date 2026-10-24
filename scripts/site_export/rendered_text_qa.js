async (page) => {
  const forbidden = /pending|placeholder|TODO|\{\{|shared figure unavailable|\b(?:C\d+[ab]?|W\d+)\b/i;
  const scan = async (label) => {
    // Template delimiters, stale status copy and editorial markers must never
    // survive into text that a browser exposes to the reader.
    const text = await page.locator('body').innerText();
    const match = forbidden.exec(text);
    if (match) throw new Error(`${label}: forbidden rendered text near ${JSON.stringify(text.slice(Math.max(0, match.index - 60), match.index + 100))}`);
  };
  await page.goto('http://127.0.0.1:4173/');
  await page.locator('.virtual-rows .matrix-cell').first().waitFor();
  await scan('main/annual');
  await page.locator('#quarterly-view').click();
  await scan('main/quarterly');
  await page.locator('#annual-view').click();
  for (const floor of ['primary', 'floor_1e9_wei', 'floor_0.1_eth']) {
    await page.selectOption('#sensitivity-floor', floor);
    await scan(`sensitivity/${floor}`);
  }
  await page.selectOption('#sensitivity-offset', '7200');
  await scan('sensitivity/legacy');
  await page.selectOption('#sensitivity-offset', '300');
  await page.selectOption('#sensitivity-floor', 'primary');
  for (const scope of ['primary', 'ex']) {
    await page.selectOption('#rate-scope', scope);
    await scan(`matrix/${scope}`);
  }
  for (const lag of ['all', 'pair_aligned_le30m']) {
    await page.selectOption('#validation-lag', lag);
    await scan(`validation/${lag}`);
  }
  const denoms = await page.locator('#tier2-denominator option').evaluateAll((options) => options.map((o) => o.value));
  for (const denominator of denoms) {
    await page.selectOption('#tier2-denominator', denominator);
    await scan(`monthly/${denominator}`);
  }
  if (await page.locator('#tier2-rug select, #tier2-rug table, #rug-chart').count()) throw new Error('LP panel still contains empty controls or a chart');
  const index = await page.request.get('http://127.0.0.1:4173/data/index.json').then((r) => r.json());
  const assertSuppressed = async (selector, rowIndex, cells, context) => {
    const row = page.locator(`${selector} tbody tr`).nth(rowIndex);
    for (const cellIndex of cells) {
      const text = await row.locator('td').nth(cellIndex).innerText();
      if (!text.includes('Not reported (effective n') || /\d+(?:\.\d+)?%/.test(text)) throw new Error(`${context}: low-support estimate escaped suppression: ${text}`);
    }
  };
  const annual = index.tier2.year.filter((r) => r.denominator === 'measurable_pooled' && r.label === 'headline_trade_harm').reverse();
  for (let i = 0; i < annual.length; i++) {
    if (annual[i].effective_n < 30) await assertSuppressed('#composition-series', i, [0], `composition/${annual[i].year}`);
    const standardized = index.revision.standardized.find((r) => r.year === annual[i].year);
    if (standardized.effective_n < 30) await assertSuppressed('#composition-series', i, [3], `standardized/${annual[i].year}`);
    const v2 = index.revision.v2_only.find((r) => r.year === annual[i].year);
    if (v2.effective_n < 30) await assertSuppressed('#composition-series', i, [1], `V2/${annual[i].year}`);
  }
  for (const scenario of ['primary', 'floor_1e9_wei', 'floor_0.1_eth']) for (const denominator of ['measurable_pooled', 'headline_excl_lp']) {
    await page.selectOption('#sensitivity-floor', scenario); await page.selectOption('#sensitivity-den', denominator);
    const rows = index.revision.sensitivity.filter((r) => r.frequency === 'annual' && r.scenario === scenario && r.denominator === denominator).reverse();
    for (let i = 0; i < rows.length; i++) if (rows[i].effective_n < 30) await assertSuppressed('#sensitivity-table', i, [0,1,2], `sensitivity/${scenario}/${rows[i].period}`);
  }
  const bounds = index.revision.bounds.filter((r) => r.frequency === 'annual' && ['headline_excl_lp','measurable_pooled'].includes(r.denominator)).reverse();
  for (let i = 0; i < bounds.length; i++) {
    const support = index.tier2.year.find((r) => r.year === bounds[i].period && r.denominator === bounds[i].denominator && r.label === 'headline_trade_harm');
    if (support.effective_n < 30) await assertSuppressed('#could-not-buy-bounds', i, [1,2,3], `bounds/${bounds[i].period}/${bounds[i].denominator}`);
  }
  await scan('uniform small-sample rule');
  await page.goto('http://127.0.0.1:4173/paper.html');
  await scan('paper');
  if (/\[@[A-Za-z_]/.test(await page.locator('.paper-article').innerText())) throw new Error('Unrendered paper citations');
  if (!await page.locator('.paper-article #refs').count()) throw new Error('Paper bibliography is missing');
  for (const width of [1440,390]) {
    await page.setViewportSize({width,height:900});
    if (!await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)) throw new Error(`Paper overflows at ${width}`);
  }
  const broken = await page.locator('.paper-article img').evaluateAll((images) => images.filter((im) => !im.complete || im.naturalWidth === 0).map((im) => im.src));
  if (broken.length) throw new Error(`Broken paper figures: ${broken.join(', ')}`);
  return { renderedTextGate: 'passed', pages: ['index.html', 'paper.html'], forbiddenPattern: forbidden.source };
}
