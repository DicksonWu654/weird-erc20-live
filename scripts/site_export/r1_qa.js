async (page) => {
  // Run from site/: playwright-cli run-code --filename ../scripts/site_export/r1_qa.js
  const errors = [], failed = [], bad = [], results = {};
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('requestfailed', (request) => failed.push(request.url()));
  page.on('response', (response) => { if (response.status() >= 400) bad.push(`${response.status()} ${response.url()}`); });
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  await page.goto('http://127.0.0.1:4173/');
  await page.locator('.virtual-rows .matrix-cell').first().waitFor();
  await page.evaluate(() => { document.documentElement.style.scrollBehavior = 'auto'; });
  const index = await page.request.get('http://127.0.0.1:4173/data/index.json').then((r) => r.json());
  const ethereum = await page.request.get('http://127.0.0.1:4173/data/matrix-ethereum.json').then((r) => r.json());

  // OS theme, persistent explicit override, and runtime preference changes.
  for (const theme of ['light', 'dark']) {
    await page.evaluate(() => localStorage.removeItem('weird-theme'));
    await page.emulateMedia({ colorScheme: theme }); await page.reload();
    await page.locator('.virtual-rows .matrix-cell').first().waitFor();
    check(await page.locator('html').getAttribute('data-theme') === theme, `OS ${theme} preference ignored`);
  }
  await page.locator('.theme-toggle').click(); await page.reload();
  check(await page.locator('html').getAttribute('data-theme') === 'light', 'Explicit theme was not saved');
  await page.evaluate(() => localStorage.removeItem('weird-theme')); await page.reload();
  await page.emulateMedia({ colorScheme: 'light' });
  check(await page.locator('html').getAttribute('data-theme') === 'light', 'Runtime OS preference change ignored');
  await page.locator('.virtual-rows .matrix-cell').first().waitFor();

  check(await page.locator('#findings-list li').count() === 6, 'Findings strip must contain six qualified links');
  for (const item of await page.locator('#findings-list [data-ledger-key]').evaluateAll((els) => els.map((e) => [e.dataset.ledgerKey, e.textContent]))) check(String(index.numbers[item[0]].value).replaceAll(',', '') === item[1].replaceAll(',', ''), `Finding ${item[0]} differs from ledger`);
  for (const row of index.tier2.year.filter((r) => r.label === 'headline_trade_harm' && r.effective_n < 30 && ['headline_excl_lp','measurable_pooled'].includes(r.denominator))) check((await page.locator('#trade-period-table').innerText()).includes('Not reported (effective n'), 'Sparse annual support not labeled');
  check((await page.locator('#self-checks').innerText()).includes('None of the 35 pre-fix V4 honeypot labels held up'), 'V4 headline reversed');
  check((await page.locator('#self-checks').innerText()).includes('verifier’s two independent routes agree on 50/50 verdicts'), 'Verifier routes mislabeled');
  check(await page.locator('#postfix-results a[href="#postfix-results"]').count() === 0, 'Unexpected self link');
  check(await page.locator('.check-card a[href="#postfix-results"]').count() === 1, 'Post-fix comparison pointer missing');
  check(await page.locator('#postfix-results tbody tr').count() === index.verification.postfix.filter((r) => Number.isFinite(r.estimate) && r.n > 0).length, 'Undefined precision rows remain');
  check(!/\b(?:C\d+|rug_C\d+|W\d+)\b/.test(await page.locator('body').innerText()), 'Internal workstream code is visible');
  check((await page.locator('#b20-validation-copy').innerText()) === 'honeypot.is flagged all 5 sampled B20 tokens; router buy+sell simulations refute 2 (NVDAc, OUSD) at the tested state; 3 unresolved.', 'B20 qualification differs');
  for (const key of ['eoa','eoa_7702','multisig_no_timelock','timelock','governor_dao','frozen']) {
    check((await page.locator('#control-paths').innerText()).includes(Number(index.upgrade_paths.counts[key]).toLocaleString('en-US')), `Control path ${key} differs from exported classification`);
  }

  // One grid tab stop; navigation crosses the virtual window and both axes.
  const tabbable = () => page.locator('#matrix-table .matrix-cell[tabindex="0"]');
  check(await tabbable().count() === 1, 'Grid has more than one tab stop');
  await tabbable().focus();
  const before = await page.evaluate(() => document.activeElement.getAttribute('aria-label'));
  await page.keyboard.press('ArrowRight');
  check(await page.evaluate(() => document.activeElement.getAttribute('aria-label')) !== before, 'ArrowRight did not move');
  await page.keyboard.press('ArrowDown');
  check(await page.evaluate(() => document.activeElement.dataset.row) === '1', 'ArrowDown did not move');
  await page.keyboard.press('PageDown');
  check(Number(await page.evaluate(() => document.activeElement.dataset.row)) > 1, 'PageDown did not move the virtual window');
  await page.keyboard.press('Control+End');
  check(await page.evaluate(() => document.activeElement.dataset.row) === String(ethereum.rows.length - 1), 'Ctrl+End missed the last virtual row');
  check(await page.locator('#matrix-frame').evaluate((e) => e.scrollTop > 48 * 2000), 'Keyboard failed to scroll deep');
  await page.keyboard.press('PageUp'); await page.keyboard.press('Home');
  check(await page.evaluate(() => document.activeElement.dataset.visibleCol) === '0', 'Home did not reach the first feature');
  await page.keyboard.press('End');
  check(await page.evaluate(() => document.activeElement.dataset.visibleCol) === String(ethereum.columns.length - 1), 'End did not reach the last feature');
  await page.keyboard.press('Control+Home');
  check(await page.evaluate(() => document.activeElement.dataset.row + '/' + document.activeElement.dataset.visibleCol) === '0/0', 'Ctrl+Home failed');
  await page.keyboard.press('Enter'); check(await page.locator('#evidence-dialog').evaluate((e) => e.open), 'Enter did not open evidence');
  await page.keyboard.press('Escape'); check(await tabbable().evaluate((e) => e === document.activeElement), 'Dialog focus was not restored');
  await page.keyboard.press('Tab'); check(!await page.evaluate(() => document.activeElement.matches('.matrix-cell')), 'Tab walks every matrix cell');
  results.grid = { tabStops: await tabbable().count(), virtualLastRow: ethereum.rows.length - 1, keys: 'Arrow, Home, End, Ctrl+Home/End, PageUp/PageDown, Enter, Escape, Tab' };

  // The reported USDT repeated-call case and a genuinely multi-id case.
  await page.fill('#token-search', '0xdac17f958d2ee523a2206206994597c13d831ec7');
  check((await page.locator('#matrix-count').innerText()).startsWith('Matching tokens'), 'Singular token grammar');
  const usdt = ethereum.rows.find((r) => r.symbol === 'USDT');
  const falseIndex = ethereum.columns.findIndex((c) => c.id === 'false_on_failure');
  await page.locator(`.matrix-cell[data-col="${falseIndex}"]`).click();
  check((await page.locator('#evidence-dialog').innerText()).includes(usdt.evidence.probe.call_hashes['26']), 'USDT call 26 digest incorrect');
  check(!await page.locator('#evidence-dialog .decoded-box').count(), 'Empty decoded context is shown');
  check(await page.locator('#evidence-dialog a[href^="https://etherscan.io/address/"]').count() === 1, 'Token explorer link missing');
  await page.locator('#evidence-dialog').screenshot({ path: 'screenshots/r3/evidence-usdt.png' });
  await page.keyboard.press('Escape');
  const multi = ethereum.rows.flatMap((row) => row.cells.map((cell, col) => ({ row, cell, col }))).find(({cell, col}) => ethereum.columns[col].src !== 'control' && new Set(String(cell[2] || '').split(',')).size > 1);
  check(!!multi, 'No multi-call fixture found');
  await page.fill('#token-search', multi.row.token);
  await page.locator(`.matrix-cell[data-col="${multi.col}"]`).click();
  const ids = [...new Set(String(multi.cell[2]).split(','))];
  for (const id of ids) check((await page.locator('#evidence-dialog').innerText()).includes(multi.row.evidence.probe.call_hashes[id]), `Missing hash for call ${id}`);
  await page.locator('#evidence-dialog').screenshot({ path: 'screenshots/r3/evidence-multicall.png' });
  await page.keyboard.press('Escape');
  const fallback = ethereum.rows.flatMap((row) => row.cells.map((cell, col) => ({ row, cell, col }))).find(({row, cell, col}) => ethereum.columns[col].src === 'probe' && cell[2] == null && row.evidence.probe.sha256);
  check(!!fallback, 'No row-level fallback fixture found');
  await page.fill('#token-search', fallback.row.token);
  await page.locator(`.matrix-cell[data-col="${fallback.col}"]`).click();
  check(/row-level envelope hash/i.test(await page.locator('#evidence-dialog').innerText()), 'Fallback digest is not labeled row-level');
  await page.locator('#evidence-dialog').screenshot({ path: 'screenshots/r3/evidence-row-level.png' });
  await page.keyboard.press('Escape');
  results.evidence = { repeated: 'USDT call 26', distinct: ids, token: multi.row.symbol, rowFallback: 'explicit' };
  await page.fill('#token-search', 'zzzz-no-token-matches');
  check((await page.locator('#matrix-frame').innerText()).includes('No tokens match'), 'No-results message missing');
  await page.locator('#matrix').screenshot({ path: 'screenshots/r3/matrix-empty.png' });
  await page.fill('#token-search', '');
  check(await page.locator('#family-picker button').count() === index.crosschain.length, 'Cross-chain families are truncated');

  await page.selectOption('#tier2-denominator', 'measurable_pooled');
  await page.selectOption('#tier2-month', '2026-09');
  for (const label of ['no_pool', 'no_meaningful_liquidity', 'late_pool']) {
    const row = page.locator(`#monthly-details tr[data-label="${label}"]`);
    check((await row.innerText()).includes('Structurally excluded'), `${label} is not structurally excluded`);
    check(!/0\.0%/.test(await row.innerText()), `${label} renders a false zero`);
  }
  await page.selectOption('#tier2-label', 'headline_trade_harm');

  const captures = [];
  const sections = ['hero','findings','headlines','matrix-intro','matrix','matrix-rates','compare','timeline','tier2-headline','tier2-composition','tier2-sensitivity','tier2-bounds','tier2-monthly','tier2-rug','stories','story-b20','story-validation','story-control','story-tornado','self-checks','check-rug','check-stats','postfix-results','methods'];
  for (const [device, width, height] of [['desktop',1440,900], ['phone',390,844]]) {
    await page.setViewportSize({width,height});
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      await page.evaluate((theme) => { document.documentElement.dataset.theme=theme; document.documentElement.style.scrollBehavior='auto'; document.activeElement?.blur(); }, theme);
      await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(800);
      check(await page.evaluate(() => document.documentElement.scrollWidth === innerWidth), `Horizontal overflow at ${device}/${theme}`);
      check(!/pending|placeholder|TODO|\{\{/i.test(await page.locator('body').innerText()), `Stale rendered copy at ${device}/${theme}`);
      const hero = await page.locator('#hero').evaluate((el) => ({ copy: el.querySelector('.hero-copy').getBoundingClientRect().top, figure: el.querySelector('.hero-figure').getBoundingClientRect().top }));
      if (device === 'phone') check(hero.copy < hero.figure, 'Mobile diagram precedes the claim');
      await page.locator('#matrix-frame').evaluate((e) => {e.scrollTop=0;e.scrollLeft=0;});
      for (const section of sections) {
        const file=`screenshots/r3/${device}-${theme}/${section}.png`;
        // Tall captures can trigger a viewport resize; the responsive chart
        // rebuild may detach a Tier-2 element while Chromium is painting it.
        for (let attempt=0; attempt<3; attempt++) {
          try {
            await page.locator(`#${section}`).scrollIntoViewIfNeeded(); await page.waitForTimeout(650);
            await page.locator(`#${section}`).screenshot({path:file,animations:'disabled'}); break;
          } catch (error) { if (attempt===2) throw new Error(`${section}: ${error.message}`); await page.waitForTimeout(650); }
        }
        captures.push(file);
      }
      await page.locator('#quarterly-view').click();
      for (let attempt=0; attempt<3; attempt++) {
        try { await page.waitForTimeout(650); await page.locator('#tier2-headline').screenshot({path:`screenshots/r3/${device}-${theme}/tier2-quarterly.png`,animations:'disabled'}); break; }
        catch (error) {if (attempt===2) throw error;}
      }
      await page.locator('#annual-view').click();
      await page.evaluate(() => window.scrollTo({top:0,behavior:'instant'}));
      await page.screenshot({path:`screenshots/r3/${device}-${theme}/full.png`,fullPage:true,animations:'disabled'});
    }
  }
  for (const width of [1440,390]) {
    await page.setViewportSize({width,height:900});
    for (const theme of ['light','dark']) {
      await page.goto('http://127.0.0.1:4173/paper.html');
      await page.evaluate((theme) => document.documentElement.dataset.theme=theme,theme);
      check(!/pending|placeholder|TODO|\{\{|\b(?:C\d+|W\d+)\b/i.test(await page.locator('body').innerText()), 'Paper has internal or stale copy');
      check(await page.locator('.paper-article img').evaluateAll((images) => images.length >= 3 && images.every((im) => im.naturalWidth > 0)), 'Paper figures broken');
      check(await page.evaluate(() => document.documentElement.scrollWidth === innerWidth), 'Paper overflows');
      await page.screenshot({path:`screenshots/r3/paper-${width}-${theme}.png`,animations:'disabled'});
      await page.locator('.paper-article img').first().screenshot({path:`screenshots/r3/paper-figure-${width}-${theme}.png`});
    }
  }
  const nojs = await page.context().browser().newContext({javaScriptEnabled:false,viewport:{width:390,height:844}});
  const fallbackPage = await nojs.newPage(); await fallbackPage.goto('http://127.0.0.1:4173/');
  check(await fallbackPage.locator('.noscript-note').isVisible(), 'No-JavaScript explanation missing');
  await fallbackPage.screenshot({path:'screenshots/r3/noscript-phone.png'});
  await fallbackPage.goto('http://127.0.0.1:4173/paper.html');
  check(await fallbackPage.locator('.paper-article h1').isVisible(), 'Paper needs JavaScript');
  await nojs.close();
  check(errors.length === 0 && failed.length === 0 && bad.length === 0, `Browser problems: ${JSON.stringify({errors,failed,bad})}`);
  return { checks:'passed', ...results, consoleErrors:errors, failedRequests:failed, httpErrors:bad, sectionScreenshots:captures.length, paperFigures:3, captures };
}
