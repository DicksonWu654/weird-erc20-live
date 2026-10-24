async (page) => {
  const errors=[], captures=[], scrolls=[];
  page.on('pageerror',(e)=>errors.push(e.message));
  page.on('console',(e)=>{if(e.type()==='error') errors.push(e.text());});
  await page.goto('http://127.0.0.1:4173/');
  await page.locator('.virtual-rows .matrix-cell').first().waitFor();
  for(const width of [1440,390]) for(const theme of ['light','dark']) {
    await page.setViewportSize({width,height:900});
    await page.emulateMedia({colorScheme:theme});
    await page.evaluate((theme)=>{document.documentElement.dataset.theme=theme;document.documentElement.style.scrollBehavior='auto';},theme);
    await page.waitForTimeout(300);
    for(const id of ['composition-series','sensitivity-table','could-not-buy-bounds','validation3-table','rank-rate-table']) {
      const region=page.locator(`#${id} .tier2-table-scroll`);
      const state=await region.evaluate((e)=>{e.scrollLeft=e.scrollWidth;return {maximum:e.scrollWidth-e.clientWidth,actual:e.scrollLeft};});
      if(state.maximum>0&&state.actual<=0) throw new Error(`Cannot scroll ${id}`);
      const path=`screenshots/r3/table-right-${id}-${width}-${theme}.png`;
      await region.screenshot({path,animations:'disabled'});captures.push(path);scrolls.push({id,width,theme,...state});
      await region.evaluate((e)=>e.scrollLeft=0);
    }
  }
  if(errors.length) throw new Error(errors.join('; '));
  return {checks:'passed',consoleErrors:errors,captures,scrolls};
}
