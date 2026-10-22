// UI verification against the explicit demo build. Never starts Core or a model.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = path.resolve('artifacts/bilingual');
const dist = path.resolve(process.env.ASG_BILINGUAL_DIST || 'artifacts/demo-dist');
fs.mkdirSync(directory, { recursive: true });

(async () => {
  const server = require('node:http').createServer((request, response) => {
    const pathname = new URL(request.url, 'http://local').pathname;
    const file = path.resolve(dist, pathname === '/' ? 'index.html' : '.' + pathname);
    if (!file.startsWith(dist + path.sep)) { response.writeHead(403).end(); return; }
    try {
      response.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2' })[path.extname(file)] || 'application/octet-stream');
      response.end(fs.readFileSync(file));
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  let browser;
  const errors = [], apiRequests = [];
  try {
    browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'en-US', reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(request.url()); });
    const select = () => page.getByRole('combobox', { name: /^(Language|语言)$/ });
    await page.goto(`http://127.0.0.1:${server.address().port}/?workspace=1`);
    await page.getByText('4 work items, most recently updated first.').waitFor();
    await select().selectOption('zh-CN');
    await page.getByRole('button', { name: '我的工作', exact: true }).waitFor();
    await page.screenshot({ path: path.join(directory, 'overview-zh-desktop.png') });
    await page.getByRole('button', { name: /Windows 受管会话断线可靠性/ }).click();
    await page.getByText('6 项任务', { exact: true }).waitFor();
    await page.getByText('5 项成果', { exact: true }).waitFor();
    const viewport = await page.locator('.work-space-world').getAttribute('style');
    await page.evaluate(() => {
      window.__bilingualPosts = 0;
      const fetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        if ((init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase() === 'POST') window.__bilingualPosts++;
        return fetch(input, init);
      };
    });
    await select().selectOption('en-US');
    await page.getByText('6 tasks', { exact: true }).waitFor();
    await page.getByText('5 artifacts', { exact: true }).waitFor();
    assert.equal(await page.locator('.work-space-world').getAttribute('style'), viewport);
    assert.equal(await page.evaluate(() => window.__bilingualPosts), 0);
    await page.getByRole('button', { name: 'Continue workbench', exact: true }).click();
    await page.locator('.work-prose').filter({ hasText: 'Windows' }).waitFor();
    await page.screenshot({ path: path.join(directory, 'workbench-en-desktop.png') });
    const content = await page.locator('.work-prose').allTextContents();
    await select().selectOption('zh-CN');
    await page.getByRole('button', { name: '放大', exact: true }).waitFor();
    await page.getByRole('button', { name: '调整要求卡片大小', exact: true }).waitFor();
    assert.deepEqual(await page.locator('.work-prose').allTextContents(), content);
    for (const locale of ['zh-CN', 'en-US']) {
      await select().selectOption(locale);
      await page.setViewportSize({ width: 390, height: 844 });
      const box = await select().boundingBox();
      assert(box && box.x >= 0 && box.x + box.width <= 391);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(directory, `workbench-${locale}-mobile.png`) });
    }
    await page.getByRole('button', { name: 'All work', exact: true }).click();
    await page.getByRole('button', { name: 'Create work', exact: true }).waitFor();
    await page.screenshot({ path: path.join(directory, 'overview-en-mobile.png') });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.evaluate(() => {
      const header = document.querySelector('.app-topbar').getBoundingClientRect();
      const navigation = document.querySelector('.app-nav').getBoundingClientRect();
      return navigation.top >= header.top && navigation.bottom <= header.bottom;
    }), true);
    await page.evaluate(async () => {
      await fetch('/api/v1/works', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        request_id: crypto.randomUUID(), title: 'Separator regression', owner: 'Fixture', requirement: '| --- | --- |',
      }) });
    });
    // Reload the overview through its real navigation to include the new fixture.
    await page.getByRole('button', { name: /Windows 受管会话断线可靠性/ }).click();
    await page.getByRole('button', { name: 'All work', exact: true }).click();
    await page.getByRole('button', { name: /Separator regression/ }).click();
    await page.getByRole('button', { name: 'Continue workbench', exact: true }).click();
    await page.getByText('| --- | --- |', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    assert.deepEqual(apiRequests, []);
    fs.writeFileSync(path.join(directory, 'browser.json'), JSON.stringify({ result: 'PASS', mode: 'demo fixtures; no model calls',
      checks: ['overview-switch', 'work-switch', 'plural-counts', 'no-language-business-writes', 'viewport-preserved', 'original-content-preserved',
        'localized-accessible-controls', '390px-both-locales', '390px-overview', 'separator-content-no-crash'], page_errors: errors }, null, 2));
    console.log('Bilingual browser checks PASS (demo fixtures; no model calls)');
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => {
  fs.writeFileSync(path.join(directory, 'browser.json'), JSON.stringify({ result: 'FAIL', reason: error.message }, null, 2));
  console.error(error); process.exitCode = 1;
});
