// Spatial geometry against explicit demo fixtures; no Core or model execution.
const assert = require('node:assert/strict');
const fs = require('node:fs'); const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = path.resolve('artifacts/spatial-bubbles'), dist = path.resolve('artifacts/demo-dist');
fs.mkdirSync(directory, { recursive: true });
const report = { result: 'RUNNING', mode: 'demo fixtures / actual browser geometry', checks: {} };
(async () => {
 const server = require('node:http').createServer((request, response) => {
   const pathname = new URL(request.url, 'http://local').pathname;
   const file = path.resolve(dist, pathname === '/' ? 'index.html' : '.' + pathname);
   if (!file.startsWith(dist + path.sep)) return response.writeHead(403).end();
   try { response.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2' })[path.extname(file)] || 'application/octet-stream'); response.end(fs.readFileSync(file)); }
   catch { response.writeHead(404).end(); }
 });
 await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
 let browser, page; const errors = [];
 try {
  browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'en-US', reducedMotion: 'no-preference' });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/?workspace=1&work=7d1c9b52-4a3e-4f0b-9c61-2f8e5a1d3b70`);
  await page.getByRole('button', { name: 'Show relationships', exact: true }).click();
  const map = page.locator('.work-space');
  const group = map.locator('.work-bubble-group').filter({ has: page.locator('.work-agent-bubble') }).first();
  await group.waitFor();
  report.checks.children_initially_absent = await group.locator('[data-bubble-child]').count() === 0;
  const parent = group.locator('.work-bubble-trigger');
  await parent.hover();
  const child = group.locator('[data-bubble-child]').first();
  await child.waitFor(); await page.waitForTimeout(280);
  const childDistance = () => group.evaluate(node => {
    const parentBox = node.querySelector('.work-bubble-trigger').getBoundingClientRect();
    const childBox = node.querySelector('[data-bubble-child]')?.getBoundingClientRect();
    return childBox ? Math.hypot(
      childBox.x + childBox.width / 2 - parentBox.x - parentBox.width / 2,
      childBox.y + childBox.height / 2 - parentBox.y - parentBox.height / 2,
    ) : null;
  });
  await child.hover();
  report.checks.parent_to_child_hover_continuity = await group.evaluate(node => node.classList.contains('is-expanded'));
  await page.screenshot({ path: path.join(directory, 'expanded-desktop.png') });
  const originDistance = await childDistance();
  assert(originDistance > 0, 'Expanded child must be visible at a distance from its parent');
  await page.mouse.move(8, 110);
  await group.locator('.work-bubble-children[aria-hidden="true"]').waitFor({ state: 'attached' });
  await page.waitForTimeout(75);
  const retractDistance = await childDistance();
  report.geometry = { expandedDistance: originDistance, retractDistance };
  report.checks.visible_parentward_retraction = retractDistance !== null && retractDistance < originDistance - 8 && retractDistance > 0;
  await child.waitFor({ state: 'detached' });
  await parent.hover(); await child.waitFor(); await parent.click(); await page.mouse.move(8, 110); await page.waitForTimeout(140);
  report.checks.click_pin_survives_leave = await parent.getAttribute('aria-pressed') === 'true' && await child.isVisible();
  await child.focus(); await page.keyboard.press('Escape');
  report.checks.escape_returns_parent_without_navigation = await parent.evaluate(node => document.activeElement === node) && new URL(page.url()).searchParams.get('work') !== null;
  await child.waitFor({ state: 'detached' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Fit canvas', exact: true }).click();
  await parent.hover(); await child.waitFor(); await page.waitForTimeout(260);
  report.checks.narrow_no_page_overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  const smallParent = await parent.boundingBox(), core = await map.locator('.work-space-node.requirement:not([data-historical])').boundingBox();
  report.checks.core_larger_than_agent = core.width > smallParent.width * 1.5;
  const smallChildren = await group.locator('[data-bubble-child]').evaluateAll(nodes => nodes.map(node => { const r = node.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; }));
  report.checks.mobile_children_bounded_and_clear_of_core = smallChildren.every(box => box.left >= 0 && box.right <= 390 &&
    (box.right <= core.x || box.left >= core.x + core.width || box.bottom <= core.y || box.top >= core.y + core.height));
  await page.screenshot({ path: path.join(directory, 'expanded-mobile.png') });
  await page.emulateMedia({ reducedMotion: 'reduce' }); await page.keyboard.press('Escape');
  await child.waitFor({ state: 'detached' });
  report.checks.reduced_motion_closes = await child.count() === 0;
  assert.deepEqual(errors, []); assert(Object.values(report.checks).every(value => value === true));
  report.result = 'PASS'; report.page_errors = errors;
 } catch (error) {
  report.result = 'FAIL'; report.reason = error.message; process.exitCode = 1;
  if (page) await page.screenshot({ path: path.join(directory, 'failure.png'), fullPage: true }).catch(() => {});
 } finally {
  fs.writeFileSync(path.join(directory, 'report.json'), JSON.stringify(report, null, 2));
  if (browser) await browser.close(); await new Promise(resolve => server.close(resolve));
 }
})();
