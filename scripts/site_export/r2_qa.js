async (page) => {
  const errors = [], failed = [], bad = [], checked = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {if (m.type() === 'error') errors.push(m.text());});
  page.on('requestfailed', (r) => failed.push(r.url()));
  page.on('response', (r) => {if (r.status() >= 400) bad.push(`${r.status()} ${r.url()}`);});
  const check = (ok, message) => {if (!ok) throw new Error(message);};
  const pct = (n) => n === null || n === undefined ? 'NA' : `${(n*100).toFixed(1)}%`;
  const capture = async (selector, path) => {
    for (let attempt=0; attempt<3; attempt++) {
      try { await page.locator(selector).scrollIntoViewIfNeeded(); await page.waitForTimeout(350); await page.locator(selector).screenshot({path,animations:'disabled'}); return; }
      catch (error) {if (attempt===2) throw error; await page.waitForTimeout(350);}
    }
  };
  await page.goto('http://127.0.0.1:4173/');
  await page.locator('.virtual-rows .matrix-cell').first().waitFor();
  const index = await page.request.get('http://127.0.0.1:4173/data/index.json').then((r) => r.json());
  for (const theme of ['light','dark']) for (const width of [1440,390]) {
    await page.setViewportSize({width,height:900});
    await page.emulateMedia({colorScheme:theme});
    await page.evaluate((theme) => {document.documentElement.dataset.theme=theme;document.documentElement.style.scrollBehavior='auto';},theme);
    await page.waitForTimeout(200);
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Overflow ${width}/${theme}`);
    const strictV2 = index.revision.v2_only.find((r) => r.year === '2026');
    check((await page.locator('#composition-series tbody tr').first().locator('td').nth(1).innerText()).includes(strictV2.effective_n < 30 ? 'Not reported' : pct(strictV2.share)), 'Strict V2 point was replaced by the broader family estimate');
    for (const floor of ['primary','floor_1e9_wei','floor_0.1_eth']) {
      await page.selectOption('#sensitivity-floor', floor);
      for (const denominator of ['headline_excl_lp','measurable_pooled']) {
        await page.selectOption('#sensitivity-den', denominator);
        const expected = index.revision.sensitivity.find((r) => r.frequency === 'annual' && r.period === '2026' && r.scenario === floor && r.denominator === denominator);
        check((await page.locator('#sensitivity-table tbody tr').first().innerText()).includes(expected.effective_n < 30 ? 'Not reported' : pct(expected.share)), `Sensitivity value ${floor}/${denominator}`);
        check((await page.locator('#sensitivity-table tbody tr').first().innerText()).includes(expected.effective_n < 30 ? 'Not reported' : pct(expected.no_meaningful_liquidity_share_pooled)), 'Descriptive below-floor share lost');
      }
    }
    check((await page.locator('#tier2-sensitivity [data-ledger-key="v2_overall_below_floor"]').innerText()) === index.numbers.v2_overall_below_floor.value, 'Overall descriptive share differs from ledger');
    await page.selectOption('#sensitivity-offset','7200');
    check(await page.locator('#sensitivity-floor').isDisabled(), 'Unmeasured legacy floor combinations enabled');
    check((await page.locator('#sensitivity-scope').innerText()).includes('not a pure offset experiment'), 'Legacy selection caveat lost');
    const controlBeforeHeightChange = await page.locator('#sensitivity-offset').elementHandle();
    await page.setViewportSize({width,height:920}); await page.waitForTimeout(250);
    check(await controlBeforeHeightChange.evaluate((e) => e.isConnected), 'Height-only resize replaced the active controls');
    await page.setViewportSize({width,height:900}); await page.waitForTimeout(250);
    await page.setViewportSize({width:width-1,height:900}); await page.waitForTimeout(250);
    check(await page.locator('#sensitivity-offset').inputValue() === '7200', 'Resize reset the offset');
    await page.setViewportSize({width,height:900}); await page.waitForTimeout(250);
    await capture('#tier2-sensitivity',`screenshots/r3/sensitivity-7200-${width}-${theme}.png`);
    await page.selectOption('#sensitivity-offset','300');
    check(await page.locator('#sensitivity-floor').isEnabled(), 'Floor remains disabled after primary offset');
    await page.selectOption('#sensitivity-floor','primary');
    for (const analysis of ['primary','ex']) {
      await page.selectOption('#rate-scope',analysis);
      for (const feature of ['is_proxy','ret_missing_transfer','fee_on_transfer_observed','share_accounting_capability','upgrade_single_eoa']) {
        await page.selectOption('#rate-feature',feature);
        const source = analysis === 'primary' ? index.revision.rank_rates : index.revision.ex_securities;
        const expected = source.find((r) => r.chain === 'all' && r.rank_band === 'all_market_cap' && r.feature === feature);
        check((await page.locator('#overall-rate').innerText()).includes(pct(expected.rate)), `Ranked rate ${feature}/${analysis}`);
        check(await page.locator('#rank-rate-table tbody tr').count() === 6, 'Chain-by-rank table lost a chain');
      }
    }
    await capture('#matrix-rates',`screenshots/r3/matrix-ex-securities-${width}-${theme}.png`);
    await page.selectOption('#rate-scope','primary');await page.selectOption('#rate-feature','ret_missing_transfer');
    check((await page.locator('#holm-results').innerText()).includes('0.00864'), 'Holm-adjusted missing-return result is stale');
    for (const lag of ['all','pair_aligned_le30m']) {
      await page.selectOption('#validation-lag',lag);
      const source = index.revision.validation.filter((r) => r.lag_group === lag);
      check(await page.locator('#validation3-table tbody tr').count() === source.length, 'Validation row count differs');
      const row = source.find((r) => r.comparison === 'sim_vs_goplus' && r.label === 'honeypot');
      const text = await page.locator('#validation3-table tbody tr').nth(source.indexOf(row)).innerText();
      check(text.includes(`[[${row.n00}, ${row.n01}], [${row.n10}, ${row.n11}]]`), '2x2 counts changed');
      check(text.includes(Number(row.kappa).toFixed(3).replace('-', '−')), 'Negative kappa was clipped');
    }
    await capture('#validation-state',`screenshots/r3/validation-aligned-${width}-${theme}.png`);
    await page.selectOption('#validation-lag','all');
    check((await page.locator('#story-tornado').innerText()).includes('2025-03-21'), 'Delisting date absent');
    const text = await page.locator('body').innerText();
    check(!/shared figure unavailable|\b(?:C\d+[ab]?|W\d+)\b|pending|placeholder|TODO|\{\{/i.test(text), 'Internal or stale text visible');
    for (const [key,value] of await page.locator('[data-ledger-key]').evaluateAll((es) => es.map((e) => [e.dataset.ledgerKey,e.textContent]))) check(String(index.numbers[key].value).replaceAll(',', '') === value.replaceAll(',', ''), `Finding ${key} differs from ledger`);
    checked.push({width,theme});
  }
  // Updated forward-upgrade cells must cite the new raw responses, not old IDs.
  await page.fill('#token-search','0xdac17f958d2ee523a2206206994597c13d831ec7');
  const eth = await page.request.get('http://127.0.0.1:4173/data/matrix-ethereum.json').then((r) => r.json());
  const usdt = eth.rows.find((r) => r.symbol === 'USDT');
  const col = eth.columns.findIndex((c) => c.id === 'upgradeable');
  await page.locator(`.matrix-cell[data-col="${col}"]`).click();
  const evidence = await page.locator('#evidence-dialog').innerText();
  check(evidence.includes('legacy deprecate-forward') && evidence.includes(usdt.evidence.forward.call_hashes.e11), 'Updated forward evidence is missing');
  await page.locator('#evidence-dialog').screenshot({path:'screenshots/r3/usdt-forward-evidence.png'});
  await page.keyboard.press('Escape');
  check(!errors.length && !failed.length && !bad.length, JSON.stringify({errors,failed,bad}));
  return {checks:'passed',viewports:checked,consoleErrors:errors,failedRequests:failed,httpErrors:bad,verified:['floor sensitivities','legacy offset scope','descriptive below-floor share','ranked and ex-securities rates','Holm correction','aligned validation counts and kappa','Tornado delisting','ledger-only findings','revised forward-upgrade evidence']};
}
