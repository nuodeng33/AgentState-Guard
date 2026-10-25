// Demo fixture verification with a real WebGL renderer. No Core/provider calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { changeLanguageViaSettings } = require('./work_settings_browser.cjs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = path.resolve('artifacts/spatial-viewer');
const dist = path.resolve(process.env.ASG_SPATIAL_DIST || 'artifacts/demo-dist');
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
  let browser; const errors = [], checks = [];
  const check = (name, passed) => { assert.equal(passed, true, name); checks.push(name); };
  try {
    browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
      headless: true, args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'en-US', reducedMotion: 'no-preference' });
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.goto(`http://127.0.0.1:${server.address().port}/?work=7d1c9b52-4a3e-4f0b-9c61-2f8e5a1d3b70`);
    await changeLanguageViaSettings(page, 'en-US');
    const viewport = await page.locator('.work-space-world').getAttribute('style');
    await page.getByRole('button', { name: '3D view', exact: true }).click();
    await page.locator('.work-rest-stage canvas').waitFor();
    check('real WebGL canvas present', await page.locator('.work-rest-stage canvas').count() === 1);
    check('WebGL fallback absent', await page.locator('.work-rest.is-unavailable').count() === 0);
    await page.getByRole('button', { name: 'Task links', exact: true }).click();
    check('core identity stays visible in task mode', await page.locator('.work-rest-core-label').isVisible());
    check('readable Agent labels use stacked layout', await page.locator('.work-rest-agent-label').first().evaluate(label => getComputedStyle(label).display === 'grid'));
    check('task list uses stacked layout', await page.locator('.work-rest-task-row').first().evaluate(row => getComputedStyle(row).display === 'grid'));
    check('all six actual task records reachable', await page.locator('.work-rest-task-row').count() === 6);
    await page.screenshot({ path: path.join(directory, 'desktop-task-links.png') });
    await page.getByRole('button', { name: 'Task links', exact: true }).click();
    const stage = page.getByRole('group', { name: /3D scene:/ });
    await stage.focus();
    const before = await page.locator('.work-rest-core-label').getAttribute('style');
    await stage.press('-');
    check('keyboard zoom changes projection', (await page.locator('.work-rest-core-label').getAttribute('style')) !== before);
    await stage.press('ArrowRight'); await stage.press('Home');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.getByRole('button', { name: 'Resume motion' }).waitFor();
    check('live reduced motion pauses scene', await page.getByRole('button', { name: 'Resume motion' }).getAttribute('aria-pressed') === 'true');
    const renderInfo = await page.locator('canvas').evaluate(canvas => {
      const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    });
    for (const size of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
      await page.setViewportSize(size);
      await page.waitForTimeout(120);
      check(`visible labels bounded ${size.width}x${size.height}`, await page.locator('.work-rest-agent-label').evaluateAll(labels => labels.every(label => {
        const style = getComputedStyle(label); if (style.visibility === 'hidden' || !label.getClientRects().length || getComputedStyle(label.parentElement).display === 'none') return true;
        const box = label.getBoundingClientRect(), host = label.closest('.work-rest-stage').getBoundingClientRect();
        return box.left >= host.left - 1 && box.right <= host.right + 1 && box.top >= host.top - 1 && box.bottom <= host.bottom + 1;
      })));
      check(`header scene facts do not overlap ${size.width}x${size.height}`, await page.evaluate(() => {
        const box = selector => document.querySelector(selector).getBoundingClientRect();
        const head = box('.work-rest-header'), stage = box('.work-rest-stage'), facts = box('.work-rest-facts');
        return head.bottom <= stage.top + 1 && stage.bottom <= facts.top + 1 && facts.bottom <= innerHeight + 1;
      }));
      check(`readable list does not obscure core ${size.width}x${size.height}`, await page.evaluate(() => {
        const list = document.querySelector('.work-rest-mobile-agents');
        if (getComputedStyle(list).display === 'none') return true;
        const a = list.getBoundingClientRect(), b = document.querySelector('.work-rest-core-label').getBoundingClientRect();
        return a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom;
      }));
      check(`no horizontal page overflow ${size.width}x${size.height}`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({ path: path.join(directory, `scene-${size.width}x${size.height}.png`) });
    }
    await page.getByRole('button', { name: 'Return to workspace', exact: true }).click();
    check('2D viewport unchanged after return', await page.locator('.work-space-world').getAttribute('style') === viewport);
    check('no browser or shader errors', errors.length === 0);
    fs.writeFileSync(path.join(directory, 'report.json'), JSON.stringify({ status: 'PASS', renderer: renderInfo,
      environment: 'Demo fixtures, headless Chromium with SwiftShader software WebGL; not native GPU/device acceptance', checks, errors }, null, 2));
    console.log(JSON.stringify({ status: 'PASS', checks: checks.length, renderer: renderInfo, report: path.join(directory, 'report.json') }));
  } catch (error) {
    fs.writeFileSync(path.join(directory, 'report.json'), JSON.stringify({ status: 'FAIL', checks, errors, failure: String(error) }, null, 2));
    throw error;
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
