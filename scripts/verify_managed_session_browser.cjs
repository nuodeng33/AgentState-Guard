// Disposable browser acceptance against the separate validation server on 8788.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = path.resolve(__dirname, '../reports/managed-session-20260930');
const reportPath = path.join(directory, 'browser-live.json');
let report = fs.existsSync(reportPath) ? JSON.parse(fs.readFileSync(reportPath, 'utf8')) : { checks: {} };
report.result = 'RUNNING';
delete report.reason;
const checkpoint = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
const fixture = JSON.parse(fs.readFileSync(path.join(directory, 'browser-fixture.json'), 'utf8'));
const text = (en, cn) => new RegExp('^(?:' + en + '|' + cn + ')$');
const label = (en, cn) => new RegExp('^(?:' + en + '|' + cn + ')');
const wait = async (read, predicate, timeout = 180000) => {
  const deadline = Date.now() + timeout;
  let result;
  do {
    result = await read();
    if (predicate(result)) return result;
    await new Promise(resolve => setTimeout(resolve, 200));
  } while (Date.now() < deadline);
  throw new Error('BROWSER_ACCEPTANCE_TIMEOUT: ' + (result?.state || 'unknown'));
};

(async () => {
  const browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true });
  try {
    const context = await browser.newContext({ baseURL: 'http://127.0.0.1:8788', viewport: { width: 1440, height: 900 } });
    const sessionToken = (await (await context.request.get('/api/session')).json()).token;
    const api = async (method, route, data) => {
      const response = await context.request.fetch('/api/v1' + route, {
        method, data, headers: { 'X-Session-Token': sessionToken },
      });
      const result = await response.json();
      assert(response.ok(), route + ': ' + (result.reason_code || response.status()));
      return result;
    };
    if (!report.work_id) {
      const app = await api('POST', '/discovery/applications', { source_id: 'local', path: fixture.program });
      const store = await api('POST', '/discovery/session-stores', { source_id: 'local', provider: 'CODEX', path: fixture.store });
      const scope = await api('POST', '/protection/scopes', { request_id: randomUUID(), source_id: 'local', path: fixture.project });
      const work = await api('POST', '/works', { request_id: randomUUID(), title: 'Managed browser acceptance', owner: 'Browser Acceptance',
        requirement: 'Return the requested marker without changing any files.' });
      Object.assign(report, { work_id: work.work_id, application_id: app.application_id, store_id: store.store_id, scope_id: scope.scope_id });
      checkpoint();
    }
    const base = '/works/' + report.work_id;
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.name));
    page.setDefaultTimeout(30000);
    await page.goto('/?workspace=1&work=' + report.work_id);
    await page.locator('.work-heading').getByText('Managed browser acceptance', { exact: true }).waitFor();
    assert.equal(await page.locator('.work-sessions:visible, .work-delivery:visible').count(), 0);
    report.checks.initial_panels_collapsed = true;
    await page.screenshot({ path: path.join(directory, 'desktop-initial.png') });
    const openSessions = async () => {
      await page.getByRole('button', { name: text('More actions', '更多操作') }).click();
      await page.getByRole('button', { name: text('Session management', '会话管理') }).click();
    };
    await openSessions();
    let panel = page.locator('.work-sessions');
    if (!report.session_id) {
      await panel.getByRole('button', { name: text('Create session', '创建会话') }).click();
      await panel.getByLabel(label('Application', '应用')).selectOption(report.application_id);
      await panel.getByLabel(label('Session store', '会话库')).selectOption(report.store_id);
      await panel.getByLabel(label('Protected directory', '保护目录')).selectOption(report.scope_id);
      await panel.getByLabel(text('Read only', '只读')).check();
      await panel.getByLabel(text('Allow ASG to manage this session', '允许 ASG 管理此会话')).check();
      const createdResponse = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/sessions'));
      await panel.getByRole('button', { name: text('Create', '创建') }).click();
      const created = await (await createdResponse).json();
      assert(created.session_id, 'SESSION_CREATE_FAILED');
      report.session_id = created.session_id;
      checkpoint();
    } else {
      const saved = await api('GET', base + '/sessions/' + report.session_id);
      await panel.locator('.work-session-list button').filter({ hasText: saved.native_session_id || saved.id }).click();
      if (saved.state === 'DISCONNECTED') await panel.getByRole('button', { name: text('Connect session', '连接会话') }).click();
    }
    const session = await wait(() => api('GET', base + '/sessions/' + report.session_id), row => row.state !== 'CONNECTING');
    assert.equal(session.state, 'READY');
    report.checks.native_session_created_in_ui = { native_session_id: session.native_session_id, read_only: session.read_only };
    if (!report.run_id) {
      await panel.getByLabel(text('Execution input', '本次输入')).fill('Reply exactly asg-browser-ok. Do not call tools or change files.');
      const response = page.waitForResponse(row => row.request().method() === 'POST' && row.url().endsWith('/runs'));
      const started = Date.now();
      await panel.getByRole('button', { name: text('Start execution', '开始执行') }).click();
      const run = await (await response).json();
      assert(run.run_id, 'RUN_CREATE_FAILED');
      report.run_id = run.run_id;
      report.checks.submit_receipt_seconds = (Date.now() - started) / 1000;
      checkpoint();
      await panel.getByRole('button', { name: text('Close session management', '关闭会话管理') }).click();
      const afterClose = await api('GET', base + '/runs/' + report.run_id);
      assert(!['CANCELLED', 'FAILED', 'UNCERTAIN'].includes(afterClose.state));
      report.checks.close_does_not_cancel = afterClose.state;
      await openSessions();
    }
    const run = await wait(() => api('GET', base + '/runs/' + report.run_id), row => ['COMPLETED', 'FAILED', 'CANCELLED', 'UNCERTAIN', 'WAITING_APPROVAL', 'WAITING_INPUT'].includes(row.state));
    assert.equal(run.state, 'COMPLETED');
    await panel.getByRole('log').getByText(/asg-browser-ok/).waitFor();
    report.checks.real_output_visible = true;
    await page.screenshot({ path: path.join(directory, 'desktop-output.png') });
    await panel.getByRole('button', { name: text('Prepare delivery', '准备交付') }).click();
    const delivery = page.locator('.work-delivery');
    if (!report.artifact_id) {
      await delivery.getByLabel(text('Artifact title', '成果标题')).fill('asg-browser-artifact');
      await delivery.locator('.work-delivery-events input[type=checkbox]').first().waitFor();
      for (const checkbox of await delivery.locator('.work-delivery-events input[type=checkbox]').all()) await checkbox.check();
      const response = page.waitForResponse(row => row.request().method() === 'POST' && row.url().endsWith('/artifacts'));
      await delivery.getByRole('button', { name: text('Save artifact', '保存成果') }).click();
      const artifact = await (await response).json();
      assert(artifact.artifact_id, 'ARTIFACT_SAVE_FAILED');
      report.artifact_id = artifact.artifact_id;
      checkpoint();
    }
    if (!report.submission_id) {
      await delivery.getByRole('checkbox', { name: /asg-browser-artifact/ }).check();
      await delivery.getByLabel(/Reference execution|引用本次执行/).check();
      await delivery.getByLabel(text('Responsible person', '交付责任人')).fill('Browser Acceptance');
      const response = page.waitForResponse(row => row.request().method() === 'POST' && row.url().endsWith('/submissions'));
      await delivery.getByRole('button', { name: text('Submit delivery', '提交交付') }).click();
      const submission = await (await response).json();
      assert(submission.submission_id, 'SUBMISSION_FAILED');
      report.submission_id = submission.submission_id;
      checkpoint();
    }
    await delivery.getByRole('tab', { name: /Records|记录/ }).click();
    await delivery.locator('.work-delivery-list button').filter({ hasText: 'Browser Acceptance' }).click();
    if (!report.checks.review_saved) {
      await delivery.getByLabel(text('Reviewer', '复核人')).fill('Browser Reviewer');
      await delivery.getByLabel(label('Review decision', '复核决定')).selectOption('APPROVED');
      await delivery.getByLabel(text('Feedback', '反馈')).fill('Verified actual marker and its fixed artifact version.');
      await delivery.getByRole('button', { name: text('Record review', '记录复核') }).click();
      await delivery.getByText(/Review approved.*Browser Reviewer|复核通过.*Browser Reviewer/).waitFor();
      report.checks.review_saved = true;
      checkpoint();
    }
    if (!report.checks.acceptance_saved) {
      await delivery.getByLabel(text('Acceptance owner', '验收负责人')).fill('Browser Acceptance');
      await delivery.getByLabel(label('Acceptance decision', '验收决定')).selectOption('ACCEPTED');
      await delivery.getByLabel(text('Acceptance basis', '验收依据')).fill('Observed genuine native output and selected exact delivery versions.');
      await delivery.getByRole('button', { name: text('Record acceptance', '记录验收') }).click();
      await delivery.getByText(/Accepted.*Browser Acceptance|已验收.*Browser Acceptance/).waitFor();
      report.checks.acceptance_saved = true;
      checkpoint();
    }
    await delivery.locator('.work-delivery-content').evaluate(element => { element.scrollTop = element.scrollHeight; });
    await page.screenshot({ path: path.join(directory, 'desktop-delivery.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    const geometry = await delivery.evaluate(element => {
      const box = element.getBoundingClientRect();
      const body = element.querySelector('.work-delivery-content');
      return { left: box.left, right: box.right, top: box.top, bottom: box.bottom,
        scroll_width: body.scrollWidth, client_width: body.clientWidth };
    });
    assert(geometry.left >= 0 && geometry.right <= 390 && geometry.top >= 0 && geometry.bottom <= 844);
    assert(geometry.scroll_width <= geometry.client_width + 1, 'MOBILE_HORIZONTAL_OVERFLOW');
    report.checks.mobile_390 = geometry;
    await page.screenshot({ path: path.join(directory, 'mobile-delivery.png') });
    await delivery.getByRole('button', { name: text('Close delivery', '关闭交付') }).click();
    await panel.waitFor({ state: 'visible' });
    await panel.getByRole('button', { name: text('Close session management', '关闭会话管理') }).click();
    assert.equal(await page.locator('.work-sessions:visible, .work-delivery:visible').count(), 0);
    await page.screenshot({ path: path.join(directory, 'mobile-return.png') });
    report.checks.layered_return = true;
    report.checks.page_errors = errors;
    assert.equal(errors.length, 0);
    report.result = 'PASS';
    checkpoint();
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => {
  report.result = 'FAIL';
  report.reason = String(error.message).slice(0, 1000);
  checkpoint();
  console.log(JSON.stringify(report));
  process.exitCode = 1;
});
