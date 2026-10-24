/* Build-time browser gate. Uses an installed Playwright or the existing CLI cache;
 * it never downloads packages or browser binaries. */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const os = require('node:os');

function playwright() {
  const roots = [process.env.PLAYWRIGHT_NODE_PATH, process.cwd()].filter(Boolean);
  const cache = path.join(os.homedir(), '.npm/_npx');
  if (fs.existsSync(cache)) for (const entry of fs.readdirSync(cache)) roots.push(path.join(cache, entry, 'node_modules'));
  for (const root of roots) {
    for (const name of ['playwright', 'playwright-core']) {
      try { return require(require.resolve(name, { paths: [root] })); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
    }
  }
  throw new Error('Rendered-text check requires an installed Playwright/Playwright CLI. Set PLAYWRIGHT_NODE_PATH to its node_modules directory. No packages were downloaded.');
}

(async () => {
  const dist = path.resolve(__dirname, '../../site/dist');
  const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png' };
  const server = http.createServer((req, res) => {
    const file = path.resolve(dist, '.' + decodeURIComponent(req.url.split('?')[0] === '/' ? '/index.html' : req.url.split('?')[0]));
    if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'text/plain' }); fs.createReadStream(file).pipe(res);
  });
  let browser;
  try {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const { chromium } = playwright();
    let executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
    if (!executablePath && !fs.existsSync(chromium.executablePath())) {
      const cache = process.env.PLAYWRIGHT_BROWSERS_PATH || path.join(os.homedir(), '.cache/ms-playwright');
      if (fs.existsSync(cache)) for (const directory of fs.readdirSync(cache).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse()) {
        for (const file of ['chrome-linux64/chrome', 'chrome-linux/chrome']) {
          const candidate = path.join(cache, directory, file);
          if (fs.existsSync(candidate)) { executablePath = candidate; break; }
        }
        if (executablePath) break;
      }
    }
    browser = await chromium.launch({ headless: true, executablePath });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    const script = process.argv[2] || 'rendered_text_qa.js';
    if (!['rendered_text_qa.js', 'visual_qa.js', 'tier2_qa.js', 'r1_qa.js', 'r2_qa.js', 'manual_qa.js', 'r3_qa.js'].includes(script)) throw new Error('Unknown site QA script');
    process.chdir(path.resolve(__dirname, '../../site'));
    const source = fs.readFileSync(path.join(__dirname, script), 'utf8').replaceAll('http://127.0.0.1:4173', `http://127.0.0.1:${server.address().port}`);
    const result = await eval('(' + source + ')')(page);
    if (errors.length) throw new Error(errors.join('; '));
    console.log(JSON.stringify({ ...result, consoleErrors: errors }));
    if (process.argv[2]) {
      const target = path.join(__dirname, '../../site/screenshots/r3'); fs.mkdirSync(target, {recursive:true});
      fs.writeFileSync(path.join(target, script.replace('.js', '-results.json')), JSON.stringify(result, null, 2));
    }
  } finally { if (browser) await browser.close(); await new Promise((resolve) => server.close(resolve)); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
