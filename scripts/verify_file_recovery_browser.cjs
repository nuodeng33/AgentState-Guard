// Actual file-recovery UI against the disposable Core. No API interception or model calls.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { parseArgs } = require('node:util');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { values } = parseArgs({ options: { url: { type: 'string' }, 'report-dir': { type: 'string' },
  phase: { type: 'string' }, fixture: { type: 'string' } } });
const origin = new URL(values.url);
assert.equal(origin.protocol, 'http:'); assert.equal(origin.hostname, '127.0.0.1');
assert(['exercise', 'reopen'].includes(values.phase));
const directory = path.resolve(values['report-dir']);
const fixture = JSON.parse(fs.readFileSync(values.fixture, 'utf8'));
const project = fs.realpathSync(fixture.project), database = fs.realpathSync(fixture.database);
const temporaryRoot = fs.realpathSync(path.dirname(values.fixture));
assert(project.startsWith(temporaryRoot + path.sep));
assert(database.startsWith(temporaryRoot + path.sep));
const destination = path.join(directory, `browser-recovery-${values.phase}.json`);
const report = { result: 'RUNNING', mode: 'actual recovery UI / disposable native files / no models', checks: {} };
fs.writeFileSync(destination, JSON.stringify(report, null, 2));
const expected = path.join(directory, 'expected-recovery-operations.json');
const bytes = name => fs.readFileSync(path.join(project, name), 'utf8');
const missingB = () => !fs.existsSync(path.join(project, 'B.txt'));
const changed = () => bytes('A.txt') === 'changed-A\n' && missingB() && bytes('C.txt') === 'new-C\n';

(async () => {
  let browser, page;
  const errors = [], apiRequests = [];
  try {
    browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'en-US', reducedMotion: 'reduce' });
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(request); });
    await page.goto(origin.href + '?workspace=1&work=' + encodeURIComponent(fixture.work_id));
    const open = async () => {
      await page.getByRole('button', { name: 'More actions', exact: true }).click();
      await page.getByRole('button', { name: 'File recovery', exact: true }).click();
      const region = page.getByRole('region', { name: 'File recovery', exact: true });
      await region.getByRole('button', { name: 'Close file recovery', exact: true }).waitFor();
      return region;
    };
    const panel = await open();
    const confirmAndWait = async name => {
      const [response] = await Promise.all([
        page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/v1/recovery/operations'),
        panel.getByRole('button', { name, exact: true }).click(),
      ]);
      assert.equal(response.status(), 202);
      const operation = await response.json();
      await panel.getByTestId('recovery-result').filter({ hasText: operation.id }).getByText('Restore succeeded', { exact: true }).waitFor();
      return operation.id;
    };
    const selectBaseline = async () => {
      await panel.getByLabel('Protected directory', { exact: true }).selectOption(fixture.scope_id);
      await panel.getByRole('option', { name: new RegExp(fixture.version_id) }).waitFor({ state: 'attached' });
      await panel.getByLabel('Directory version', { exact: true }).selectOption(fixture.version_id);
      await panel.getByRole('checkbox', { name: 'A.txt', exact: true }).check();
      await panel.getByRole('checkbox', { name: 'B.txt', exact: true }).check();
    };
    if (values.phase === 'exercise') {
      assert.equal(await panel.getByRole('button', { name: 'Close file recovery', exact: true }).evaluate(node => document.activeElement === node), true);
      await page.keyboard.press('Escape');
      await panel.waitFor({ state: 'hidden' });
      await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === 'More actions');
      report.checks.ui_keyboard_focus = true;
      await open();
      await selectBaseline();
      report.checks.ui_global_directory_and_version = true;
      const before = apiRequests.filter(r => r.method() === 'POST' && new URL(r.url()).pathname === '/api/v1/recovery/operations').length;
      await panel.getByRole('button', { name: 'Preview restore', exact: true }).click();
      await panel.getByRole('button', { name: 'Confirm restore', exact: true }).waitFor();
      report.checks.ui_preview_read_only = changed() && before === apiRequests.filter(r => r.method() === 'POST' && new URL(r.url()).pathname === '/api/v1/recovery/operations').length;
      await panel.getByRole('button', { name: 'View diff A.txt', exact: true }).click();
      const diff = panel.locator('pre');
      await diff.filter({ hasText: '-changed-A' }).waitFor();
      report.checks.ui_actual_diff = (await diff.textContent()).includes('+original-A');
      await page.setViewportSize({ width: 390, height: 844 });
      const confirm = panel.getByRole('button', { name: 'Confirm restore', exact: true });
      await confirm.scrollIntoViewIfNeeded();
      const box = await confirm.boundingBox();
      report.checks.ui_mobile_confirm_accessible = box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844;
      await page.screenshot({ path: path.join(directory, 'real-ui-recovery-mobile-confirm.png') });
      const restoreId = await confirmAndWait('Confirm restore');
      report.checks.ui_selective_restore = bytes('A.txt') === 'original-A\n' && bytes('B.txt') === 'original-B\n' && bytes('C.txt') === 'new-C\n';
      await page.setViewportSize({ width: 1440, height: 1000 });
      await panel.getByRole('button', { name: 'Undo ' + restoreId, exact: true }).click();
      await panel.getByRole('checkbox', { name: 'A.txt', exact: true }).check();
      await panel.getByRole('checkbox', { name: 'B.txt', exact: true }).check();
      await panel.getByRole('button', { name: 'Preview undo', exact: true }).click();
      await panel.getByRole('button', { name: 'Confirm undo', exact: true }).waitFor();
      const undoId = await confirmAndWait('Confirm undo');
      report.checks.ui_undo_restore = changed();
      fs.writeFileSync(expected, JSON.stringify({ restoreId, undoId }));
      await panel.getByRole('button', { name: 'Return to versions', exact: true }).click();
      await panel.getByLabel('Directory version', { exact: true }).selectOption(fixture.version_id);
      await panel.getByRole('checkbox', { name: 'A.txt', exact: true }).check();
      const [prepared] = await Promise.all([
        page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/v1/recovery/previews'),
        panel.getByRole('button', { name: 'Preview restore', exact: true }).click(),
      ]);
      const preview = await prepared.json();
      await panel.getByRole('button', { name: 'Confirm restore', exact: true }).waitFor();
      // Isolated evidence only: expire this fixture's preview; never add a production hook or wait five minutes.
      const expire = spawnSync(fixture.python, ['-c',
        'import sqlite3,sys\nwith sqlite3.connect(sys.argv[1], timeout=15) as db:\n c=db.execute("UPDATE protection_previews SET expires_at=? WHERE id=?",("2000-01-01T00:00:00+00:00",sys.argv[2]))\n assert c.rowcount==1', database, preview.preview_id], { encoding: 'utf8' });
      assert.equal(expire.status, 0, expire.stderr);
      const [rejected] = await Promise.all([
        page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/v1/recovery/operations'),
        panel.getByRole('button', { name: 'Confirm restore', exact: true }).click(),
      ]);
      report.expired_rejection = { status: rejected.status(), body: await rejected.json() };
      assert.equal(report.expired_rejection.status, 409);
      assert.equal(report.expired_rejection.body.reason_code, 'PROTECTION_PREVIEW_EXPIRED');
      await panel.getByRole('alert').filter({ hasText: /Preview expired/ }).waitFor();
      report.checks.ui_expired_preview_rejected = changed() && await panel.getByRole('button', { name: 'Confirm restore', exact: true }).count() === 0;
      await page.screenshot({ path: path.join(directory, 'real-ui-recovery-expired.png') });
    } else {
      const saved = JSON.parse(fs.readFileSync(expected, 'utf8'));
      await panel.getByRole('button', { name: 'Undo ' + saved.restoreId, exact: true }).waitFor();
      const history = (await panel.locator('article').allTextContents()).join('\n');
      report.checks.ui_recovery_history_after_restart = history.includes(saved.restoreId) && history.includes(saved.undoId) && changed();
      await page.screenshot({ path: path.join(directory, 'real-ui-recovery-reopened.png') });
    }
    assert(apiRequests.length > 0, 'No actual Core requests observed');
    assert.deepEqual(errors, []);
    assert(Object.values(report.checks).every(value => value === true));
    report.result = 'PASS'; report.page_errors = errors; report.actual_api_requests = apiRequests.length;
  } catch (error) {
    report.result = 'FAIL'; report.reason = error.message;
    if (page) await page.screenshot({ path: path.join(directory, `failure-recovery-${values.phase}.png`) }).catch(() => {});
    process.exitCode = 1;
  } finally {
    fs.writeFileSync(destination, JSON.stringify(report, null, 2));
    if (browser) await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
