async (page) => {
  const errors = [], failed = [], httpErrors = [], captures = [], checked = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('requestfailed', (r) => failed.push(r.url()));
  page.on('response', (r) => { if (r.status() >= 400) httpErrors.push(`${r.status()} ${r.url()}`); });
  const check = (ok, why) => { if (!ok) throw new Error(why); };
  await page.goto('http://127.0.0.1:4173/');
  await page.locator('.virtual-rows .matrix-cell').first().waitFor();
  const index = await page.request.get('http://127.0.0.1:4173/data/index.json').then((r) => r.json());
  const textCount = (ref) => Number(index.numbers.site_counts.value[ref]).toLocaleString('en-US', { maximumFractionDigits: 1 });
  for (const [ref, text] of await page.locator('[data-count-ref]').evaluateAll((nodes) => nodes.map((e) => [e.dataset.countRef, e.textContent]))) check(text === textCount(ref), `Unbound rendered count ${ref}`);
  check(await page.locator('#tier2-rug select, #tier2-rug table, #rug-chart').count() === 0, 'LP dead widget remains');
  for (const key of ['lp_blind_n','lp_blind_rugged_insider','lp_blind_undetermined','lp_blind_stable_or_growing','lp_blind_died_by_selling']) check(await page.locator(`#tier2-rug [data-ledger-key="${key}"]`).innerText() === index.numbers[key].value, `LP fact ${key} differs`);
  for (const key of ['v21_first_liquidity_table','v21_combined_table','v21_coverage_table','blind300_table','blind300_weighted_table']) {
    const lines = index.numbers[key].value.trim().split('\n');
    const cells = lines.filter((_,i) => i !== 1).map((line) => line.trim().replace(/^\||\|$/g, '').split('|').map((v) => v.trim().replaceAll('&#124;', '|')));
    if (key === 'v21_coverage_table') for (let i=1;i<cells.length;i++) {
      const domain=index.tier2.year.find((r) => r.year===cells[i][0] && r.denominator==='measurable_pooled' && r.label==='headline_trade_harm');
      if (domain.effective_n<30) cells[i]=[cells[i][0],...cells[i].slice(1).map(() => `Not reported (effective n ${Number(domain.effective_n).toLocaleString('en-US',{maximumFractionDigits:2})} < 30)`)];
    }
    const actual = await page.locator(`[data-ledger-table="${key}"] tr`).evaluateAll((rows) => rows.map((r) => [...r.cells].map((c) => c.textContent)));
    check(JSON.stringify(actual) === JSON.stringify(cells), `Ledger table changed: ${key}`);
  }
  const unboundFails = await page.evaluate(() => { try { window.SiteLedger.format({ n: 314 }, 'n'); return false; } catch { return true; } });
  check(unboundFails, 'Derived count objects bypass the runtime ledger gate');
  await page.locator('#findings-list a[href="#matrix-rates"]').click();
  check(await page.locator('#rate-feature').inputValue() === 'ret_missing_transfer', 'Missing-return link does not preselect the feature');
  check((await page.locator('#findings').innerText()).includes('7/498') && (await page.locator('#findings').innerText()).includes('0/2,181'), 'Rank-matched missing-return finding absent');
  check((await page.locator('#story-validation').innerText()).includes('GoPlus flagged 1 of 124'), 'Provider marginal-positive definition absent');
  check((await page.locator('#story-validation').innerText()).includes('[−0.020, 0.000]'), 'Signed CI is ambiguous');
  check((await page.locator('#tier2-composition').innerText()).includes('V4 had zero 2024 weight'), 'V4 fixed-mix caveat absent');
  check((await page.locator('#composition-series').innerText()).includes('Smallest venue support'), 'Standardized support is unexplained');
  await page.selectOption('#tier2-denominator', 'measurable_pooled');
  await page.selectOption('#tier2-label', 'owner_control_capability');
  await page.selectOption('#tier2-month', '2026-09');
  check((await page.locator('#monthly-details tr[data-label="owner_control_capability"]').innerText()).includes('capability flags are unassessed'), 'Capability NA has no reason');
  const points = await page.locator('#tier2-chart [data-period]').evaluateAll((nodes) => nodes.map((n) => n.dataset.period));
  for (const period of points) {
    const row = index.scam_shares.find((r) => r.month === period && r.denominator === 'measurable_pooled' && r.label === 'owner_control_capability');
    check(row.effective_n >= 30 && row.identified !== false && row.share !== null, `Unsupported capability point ${period}`);
  }
  await page.selectOption('#tier2-label', 'headline_trade_harm');
  await page.selectOption('#chain-filter', 'base');
  await page.waitForFunction(() => document.querySelector('#matrix-count').textContent.endsWith('base inventory'));
  await page.fill('#token-search', '0xb200');
  await page.waitForFunction(() => { const nodes=[...document.querySelectorAll('.virtual-rows .token-cell small')]; return nodes.length>0 && nodes.every((e) => e.textContent.includes('0xb200…')); });
  const addresses = await page.locator('.virtual-rows .token-cell small').allTextContents();
  await page.locator('#matrix').screenshot({path:'screenshots/r3/matrix-b20.png',animations:'disabled'});
  check(addresses.every((s) => /0xb200…[a-f0-9]{5}/i.test(s)), 'B20 addresses lack distinct tails');
  check(new Set(addresses).size === addresses.length, 'Visible B20 addresses are indistinguishable');
  await page.fill('#token-search', ''); await page.selectOption('#chain-filter', 'ethereum');
  await page.waitForFunction(() => document.querySelector('#matrix-count').textContent.endsWith('ethereum inventory'));
  check(index.crosschain.every((f) => f.deployments >= 2 && f.display_name), 'Singleton or unnamed cross-chain family');
  const sections = ['hero','findings','matrix','matrix-rates','compare','tier2-headline','tier2-composition','tier2-liquidity','tier2-sensitivity','tier2-bounds','tier2-monthly','tier2-rug','story-b20','story-validation','story-control','story-tornado','launch-blind-checks','postfix-results','methods'];
  for (const [device,width,height] of [['desktop',1440,900],['phone',390,844]]) for (const theme of ['light','dark']) {
    await page.setViewportSize({width,height}); await page.emulateMedia({colorScheme:theme});
    await page.evaluate((theme) => { document.documentElement.dataset.theme=theme; document.documentElement.style.scrollBehavior='auto'; document.activeElement?.blur(); },theme);
    await page.waitForTimeout(300);
    check(await page.evaluate(() => document.documentElement.scrollWidth === innerWidth), `Overflow ${device}/${theme}`);
    // The former focused skip-link failure is checked against actual computed colors.
    const contrast = await page.locator('.skip-link').evaluate((el) => {
      el.focus(); const s=getComputedStyle(el);
      const luminance = (color) => { const rgb=color.match(/[\d.]+/g).slice(0,3).map(Number).map((v) => {v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;});return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722; };
      const a=luminance(s.color), b=luminance(s.backgroundColor);el.blur();return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    });
    check(contrast >= 4.5, `Focused skip-link contrast ${contrast}`);
    for (const id of sections) {
      const file=`screenshots/r3/${device}-${theme}/${id}.png`;
      await page.locator(`#${id}`).scrollIntoViewIfNeeded(); await page.waitForTimeout(100);
      await page.locator(`#${id}`).screenshot({path:file,animations:'disabled'}); captures.push(file);
    }
    checked.push({width,height,theme,skipLinkContrast:contrast});
  }
  check(!errors.length && !failed.length && !httpErrors.length, JSON.stringify({errors,failed,httpErrors}));
  return {checks:'passed',viewports:checked,consoleErrors:errors,failedRequests:failed,httpErrors,captures};
}
