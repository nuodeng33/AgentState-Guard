// Actual production bundle + Core HTTP; only provider events come from the fixture driver.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const directory = path.resolve(process.argv[3]);
const report = { result: 'RUNNING', phase: fixture.phase, checks: {}, scope: 'production browser/Core/SQLite; protocol fixture, no models' };
assert.equal(new URL(fixture.url).hostname, '127.0.0.1');
assert.equal(new URL(fixture.driver_url).hostname, '127.0.0.1');
(async () => {
 let browser, page; const errors = [], writes = [];
 const drive = async action => {
  const response = await fetch(fixture.driver_url, { method: 'POST', headers: {
   'Content-Type': 'application/json', 'X-Verify-Token': fixture.driver_token }, body: JSON.stringify({ action }) });
  assert(response.ok, 'CONTROL_FAILED'); return response.json();
 };
 const wait = async (read, predicate) => {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) { const value = await read(); if (predicate(value)) return value; await new Promise(resolve => setTimeout(resolve, 50)); }
  throw new Error('QUEUE_STATE_TIMEOUT');
 };
 const api = suffix => page.evaluate(async route => {
  const auth = await fetch('/api/session').then(response => response.json());
  const response = await fetch(route, { headers: { 'X-Session-Token': auth.token } });
  if (!response.ok) throw new Error('CORE_READ_FAILED');
  return response.json();
 }, '/api/v1/works/' + fixture.work_id + '/sessions/' + fixture.session_id + suffix);
 const post = async (suffix, action) => {
  const [response] = await Promise.all([page.waitForResponse(response => response.request().method() === 'POST' &&
    new URL(response.url()).pathname.endsWith(suffix)), action()]);
  assert(response.ok(), 'CORE_POST_FAILED'); return response.json();
 };
 try {
  browser = await chromium.launch({ headless: true, chromiumSandbox: true,
   ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  page = await browser.newPage({ locale: 'en-US', viewport: { width: 1440, height: 1100 }, reducedMotion: 'reduce' });
  page.on('pageerror', () => errors.push('PAGE_ERROR'));
  page.on('request', request => { if (request.method() === 'POST') writes.push(new URL(request.url()).pathname); });
  await page.goto(fixture.url + '/?workspace=1&work=' + encodeURIComponent(fixture.work_id));
  await page.getByRole('button', { name: 'More actions', exact: true }).click();
  await page.getByRole('button', { name: /^(Sessions|Session management)$/ }).click();
  const panel = page.getByRole('region', { name: 'Session management', exact: true });
  await panel.getByRole('button', { name: /CODEX.*queue-native-/ }).click();
  const input = panel.getByLabel('Execution input', { exact: true });
  await input.waitFor();
  if (fixture.phase === 'reopen') {
   await panel.getByText('Held for manual review', { exact: true }).waitFor();
   assert.equal((await api('/pending-instruction')).item.state, 'HELD');
   assert.equal((await drive('inspect')).sends, fixture.initial_sends);
   assert.equal(writes.length, 0);
   report.checks.reopened_held_exact_text = (await panel.locator('.work-pending-instruction').innerText()).includes('Keep until manual review');
   report.checks.no_automatic_resubmit_or_replay = true;
   await page.screenshot({ path: path.join(directory, 'queue-held-after-restart.png') });
  } else {
   const execute = panel.getByRole('button', { name: 'Start execution', exact: true });
   await input.fill('Predecessor from actual UI');
   const predecessor = await post('/runs', () => execute.click());
   await wait(() => api('/runs/' + predecessor.run_id), row => row.state === 'RUNNING');
   const text = 'Exact queued UI instruction\nKeep this second line';
   await input.fill(text);
   const enqueue = panel.getByRole('button', { name: 'Queue next instruction', exact: true });
   await wait(() => enqueue.isEnabled(), Boolean);
   const queued = await post('/pending-instruction', () => enqueue.click());
   await panel.getByText('Queued for next execution', { exact: true }).waitFor();
   await wait(() => input.inputValue(), value => value === '');
   const item = (await api('/pending-instruction')).item;
   assert.equal(item.input, text); assert.equal(item.predecessor_run_id, predecessor.run_id);
   assert.equal((await drive('inspect')).sends, 1);
   report.checks.explicit_queue_immutable_exact_text = true;
   report.checks.one_slot_no_early_dispatch_and_draft_clear = await enqueue.isDisabled();
   await page.screenshot({ path: path.join(directory, 'queue-pending.png') });
   await drive('complete');
   const promoted = await wait(async () => (await api('/pending-instruction')).item, row => row.state === 'PROMOTED');
   const child = await wait(() => api('/runs/' + promoted.promoted_run_id), row => row.state === 'COMPLETED');
   assert.equal(child.file_result.status, 'AVAILABLE');
   await panel.getByText('Started as an execution', { exact: true }).waitFor();
   assert.equal((await drive('inspect')).sends, 2);
   report.checks.real_completed_file_result_precedes_child_once = true;
   report.checks.promoted_execution_visible = true;
   await page.screenshot({ path: path.join(directory, 'queue-promoted.png') });
   // Verify Stop through one existing UI run/cancel call, including its queued next instruction.
   await drive('wait');
   await input.fill('Predecessor to stop');
   const stopRun = await post('/runs', () => execute.click());
   await wait(() => api('/runs/' + stopRun.run_id), row => row.state === 'RUNNING');
   await input.fill('Never dispatch this queued instruction');
   await wait(() => enqueue.isEnabled(), Boolean);
   await post('/pending-instruction', () => enqueue.click());
   await panel.getByText('Queued for next execution', { exact: true }).waitFor();
   await post('/cancel', () => panel.getByRole('button', { name: 'Stop execution', exact: true }).click());
   await wait(async () => (await api('/pending-instruction')).item, row => row.state === 'CANCELLED');
   await wait(() => api('/runs/' + stopRun.run_id), row => row.state === 'CANCELLED');
   await panel.locator('.work-pending-instruction').getByText('Cancelled', { exact: true }).waitFor();
   assert.equal((await drive('inspect')).sends, 3);
   report.checks.stop_cancels_current_and_pending_no_next_send = true;
   // Leave one durable instruction pending; normal Core close/reopen must hold it.
   await input.fill('Predecessor before Core restart');
   const restartRun = await post('/runs', () => execute.click());
   await wait(() => api('/runs/' + restartRun.run_id), row => row.state === 'RUNNING');
   await input.fill('Keep until manual review');
   await wait(() => enqueue.isEnabled(), Boolean);
   await post('/pending-instruction', () => enqueue.click());
   await panel.getByText('Queued for next execution', { exact: true }).waitFor();
   report.checks.durable_pending_prepared_for_restart = true;
  }
  report.checks.no_model_calls = !writes.some(route => route.startsWith('/api/ai/'));
  report.checks.no_page_errors = errors.length === 0;
  assert(Object.values(report.checks).every(value => value === true), 'QUEUE_CHECK_FAILED');
  report.result = 'PASS';
 } catch (error) {
  report.result = 'FAIL';
  report.reason = /No usable sandbox|Chromium sandboxing failed/.test(error.message ?? '') ? 'BROWSER_SANDBOX_UNAVAILABLE'
    : /^[A-Z0-9_]+$/.test(error.message ?? '') ? error.message : 'QUEUE_BROWSER_FAILED';
  if (page) await page.screenshot({ path: path.join(directory, 'failure.png') }).catch(() => {});
 } finally {
  if (browser) await browser.close();
  fs.writeFileSync(path.join(directory, 'report.json'), JSON.stringify(report, null, 2));
 }
 process.exitCode = report.result === 'PASS' ? 0 : 1;
})();
