// UI verification against the explicit demo build. Never starts Core or a model.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createWorkFromSidebar, changeLanguageViaSettings } = require('./work_settings_browser.cjs');
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
    const snapshot = () => page.evaluate(async () => {
      const id = new URL(location.href).searchParams.get('work');
      if (!id) throw new Error('Work must remain selected');
      const auth = await fetch('/api/session').then(response => response.json());
      const read = suffix => fetch('/api/v1/works/' + encodeURIComponent(id) + suffix,
        { headers: { 'X-Session-Token': auth.token } }).then(response => {
          if (!response.ok) throw new Error('Work snapshot unavailable');
          return response.json();
        });
      return { work: await read(''), view: await read('/view') };
    });
    const workButton = title => page.locator('.work-layout:visible .work-sidebar-list').getByRole('button', { name: title });
    await page.goto(`http://127.0.0.1:${server.address().port}/?workspace=1`);
    await page.getByText('4 work items, most recently updated first.').waitFor();
    await changeLanguageViaSettings(page, 'zh-CN');
    await page.getByRole('button', { name: '我的工作', exact: true }).waitFor();
    await page.screenshot({ path: path.join(directory, 'overview-zh-desktop.png') });
    await workButton(/Windows 受管会话断线可靠性/).click();
    await page.getByText('6 项任务', { exact: true }).waitFor();
    await page.getByText('5 项成果', { exact: true }).waitFor();
    const viewport = await page.locator('.work-space-world').getAttribute('style');
    const beforeLanguage = await snapshot();
    await changeLanguageViaSettings(page, 'en-US');
    await page.getByText('6 tasks', { exact: true }).waitFor();
    await page.getByText('5 artifacts', { exact: true }).waitFor();
    assert.equal(await page.locator('.work-space-world').getAttribute('style'), viewport);
    assert.deepEqual(await snapshot(), beforeLanguage, 'Language navigation must not change Work data or workspace revision');
    await page.getByRole('button', { name: 'Continue workbench', exact: true }).click();
    await page.locator('.work-prose').filter({ hasText: 'Windows' }).waitFor();
    await page.screenshot({ path: path.join(directory, 'workbench-en-desktop.png') });
    const content = await page.locator('.work-prose').allTextContents();
    await changeLanguageViaSettings(page, 'zh-CN');
    await page.getByRole('button', { name: '放大', exact: true }).waitFor();
    await page.getByRole('button', { name: '调整要求卡片大小', exact: true }).waitFor();
    assert.deepEqual(await page.locator('.work-prose').allTextContents(), content);
    for (const locale of ['zh-CN', 'en-US']) {
      await page.setViewportSize({ width: 390, height: 844 });
      await changeLanguageViaSettings(page, locale, { inspectPreference: async radio => {
        const box = await radio.boundingBox();
        assert(box && box.x >= 0 && box.x + box.width <= 391);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      } });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(directory, `workbench-${locale}-mobile.png`) });
    }
    await page.getByRole('button', { name: 'All work', exact: true }).click();
    await page.locator('.work-layout:visible .work-sidebar').getByRole('button', { name: 'New work', exact: true }).waitFor();
    await page.screenshot({ path: path.join(directory, 'overview-en-mobile.png') });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.evaluate(() => {
      const sidebar = document.querySelector('.work-layout:not([aria-hidden="true"]) .work-sidebar').getBoundingClientRect();
      const navigation = document.querySelector('.work-layout:not([aria-hidden="true"]) .work-sidebar-nav').getBoundingClientRect();
      return navigation.left >= sidebar.left && navigation.right <= sidebar.right && navigation.top >= sidebar.top && navigation.bottom <= sidebar.bottom;
    }), true);
    await createWorkFromSidebar(page);
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Work title', { exact: true }).fill('Separator regression');
    await dialog.getByLabel('Owner', { exact: true }).fill('Fixture');
    await dialog.getByLabel('Requirement', { exact: true }).fill('| --- | --- |');
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await page.getByRole('button', { name: 'More actions', exact: true }).waitFor();

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
