// Real Core/browser onboarding. No request interception, injected provider or model call.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { createWorkFromSidebar, changeLanguageViaSettings } = require('./work_settings_browser.cjs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const directory = path.resolve(process.argv[3]); fs.mkdirSync(directory, { recursive: true });
const report = { result: 'RUNNING', checks: {}, mode: 'real Core / explicit UI onboarding / no model calls' };
const destination = path.join(directory, 'onboarding-browser.json');
const origin = new URL(fixture.url); assert.equal(origin.hostname, '127.0.0.1');
(async () => {
  let browser, page; const errors = [], writes = [];
  try {
    browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'en-US', reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.method() === 'POST') writes.push(new URL(request.url()).pathname); });
    const read = suffix => page.evaluate(async suffix => {
      const session = await fetch('/api/session').then(response => response.json());
      const response = await fetch('/api/v1' + suffix, { headers: { 'X-Session-Token': session.token } });
      if (!response.ok) throw new Error('READ_FAILED_' + response.status);
      return response.json();
    }, suffix);
    const postAction = async (suffix, action) => {
      const [response] = await Promise.all([page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(suffix)), action()]);
      const result = await response.json();
      if (!response.ok()) throw new Error('POST_FAILED_' + response.status() + '_' + (result.reason_code ?? suffix));
      return result;
    };
    if (fixture.phase === 'reopen') {
      await page.goto(origin.href + '?workspace=1&work=' + encodeURIComponent(fixture.work_id));
      await page.getByRole('button', { name: 'More actions', exact: true }).click();
      await page.getByRole('button', { name: 'File recovery', exact: true }).click();
      const recovery = page.getByRole('region', { name: 'File recovery', exact: true });
      await recovery.getByRole('combobox', { name: 'Protected directory', exact: true }).selectOption(fixture.scope_id);
      await recovery.getByText('Manage protected directories', { exact: true }).click();
      await recovery.getByLabel('Retention days', { exact: true }).waitFor();
      report.checks.policy_persisted_after_restart = await recovery.getByLabel('Retention days', { exact: true }).inputValue() === '45' &&
        await recovery.getByLabel('Storage quota (MiB)', { exact: true }).inputValue() === '512' &&
        !(await recovery.getByLabel('Protection enabled', { exact: true }).isChecked());
      report.checks.no_mutation_on_reopen = writes.length === 0;
      assert(Object.values(report.checks).every(value => value === true)); assert.deepEqual(errors, []);
      report.result = 'PASS'; report.page_errors = errors;
      await page.screenshot({ path: path.join(directory, 'onboarding-policy-reopened.png') });
      return;
    }
    await page.goto(origin.href + '?workspace=1'); await page.waitForLoadState('networkidle');
    await createWorkFromSidebar(page);
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Work title', { exact: true }).fill('Project onboarding acceptance');
    await dialog.getByLabel('Owner', { exact: true }).fill('Acceptance owner');
    await dialog.getByLabel('Requirement', { exact: true }).fill('Connect an existing project with explicit directory protection and session consent.');
    const work = await postAction('/api/v1/works', () => dialog.getByRole('button', { name: 'Save', exact: true }).click());
    report.work_id = work.work_id;
    await page.getByRole('button', { name: 'More actions', exact: true }).click();
    fs.writeFileSync(path.join(directory, 'menu.txt'), await page.locator('body').innerText());
    await page.getByRole('button', { name: /^(Session management|Sessions)$/ }).click();
    const panel = page.getByRole('region', { name: 'Session management', exact: true });
    await panel.getByRole('button', { name: 'Create session', exact: true }).click();
    await panel.getByText('Connect programs and session stores', { exact: true }).click();
    await panel.getByRole('button', { name: 'Scan programs and environments', exact: true }).click();
    await panel.getByRole('option', { name: /Local.*local.*(OBSERVED|PARTIAL|EMPTY)/ }).waitFor({ state: 'attached' });
    report.checks.explicit_environment_scan = true;
    await panel.getByText('Programs and session stores', { exact: true }).click();
    await panel.locator('summary').filter({ hasText: /^Register program$/ }).click();
    await panel.getByLabel('Program path', { exact: true }).fill(fixture.program);
    const registeredApp = await postAction('/discovery/applications', () => panel.getByRole('button', { name: 'Register program', exact: true }).click());
    await panel.locator('summary').filter({ hasText: /^Register session store$/ }).click();
    await panel.getByLabel('Session store path', { exact: true }).fill(fixture.store);
    const registeredStore = await postAction('/discovery/session-stores', () => panel.getByRole('button', { name: 'Register session store', exact: true }).click());
    report.checks.program_and_store_registered = Boolean(registeredApp.application_id && registeredStore.store_id);
    await panel.getByRole('combobox', { name: 'Application', exact: true }).selectOption(registeredApp.application_id);
    await panel.getByRole('combobox', { name: 'Session store', exact: true }).selectOption(registeredStore.store_id);
    await panel.getByText('Manage protected directories', { exact: true }).click();
    await panel.getByLabel('Project directory path', { exact: true }).fill(fixture.project);
    report.checks.opening_manager_does_not_register = !writes.includes('/api/v1/protection/scopes');
    const registered = await postAction('/protection/scopes', () => panel.getByRole('button', { name: 'Register and protect directory', exact: true }).click());
    await panel.getByRole('status').filter({ hasText: 'Directory registered.' }).waitFor();
    assert.equal(await panel.getByRole('combobox', { name: 'Protected directory', exact: true }).inputValue(), registered.scope_id);
    report.scope_id = registered.scope_id;
    report.checks.registration_refresh_selects_scope = true;
    const create = panel.getByRole('button', { name: 'Create', exact: true });
    report.checks.registration_does_not_grant_session_consent = await create.isDisabled();
    await panel.getByLabel('Protection enabled', { exact: true }).check();
    await panel.getByLabel('Retention days', { exact: true }).fill('45');
    await panel.getByLabel('Storage quota (MiB)', { exact: true }).fill('512');
    await postAction('/configure', () => panel.getByRole('button', { name: 'Save protection settings', exact: true }).click());
    await panel.getByText('Protection settings saved.', { exact: true }).waitFor();
    const scope = (await read('/protection/scopes')).items.find(item => item.id === registered.scope_id);
    report.checks.policy_authoritatively_saved = scope.retention_days === 45 && scope.quota_bytes === 512 * 1048576 && Boolean(scope.enabled) === true;
    await page.screenshot({ path: path.join(directory, 'onboarding-session-desktop.png') });
    await panel.getByLabel('Allow ASG to manage this session', { exact: true }).check();
    report.checks.consent_unlocks_create = await create.isEnabled();
    // Only create/connect the real session. Never send an execution or consume model tokens.
    const [createdResponse] = await Promise.all([page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith('/sessions')), create.click()]);
    const created = await createdResponse.json();
    report.session_creation = { status: createdResponse.status(), reason_code: created.reason_code ?? null, session_id: created.session_id ?? null };
    report.checks.real_session_creation_request = true;
    if (createdResponse.ok()) {
      report.checks.session_created = typeof created.session_id === 'string';
      let session;
      for (let attempt = 0; attempt < 100; attempt++) {
        session = await read('/works/' + report.work_id + '/sessions/' + created.session_id);
        if (!['CONNECTING', 'CREATING'].includes(session.state ?? session.status)) break;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      report.session_state = { state: session.state ?? session.status, connection_state: session.connection_state ?? null, reason_code: session.reason_code };
      if (report.session_state.state !== 'READY') report.provider_limit = session.reason_code ?? 'SESSION_CONNECTION_NOT_READY';
    }
    else {
      // A refused native sandbox is an explicit limit, not a successful provider execution.
      assert(['EXECUTION_PROVIDER_SANDBOX_UNVERIFIED', 'EXECUTION_PROVIDER_UNAVAILABLE'].includes(created.reason_code), JSON.stringify(report.session_creation));
      report.provider_limit = created.reason_code;
      report.checks.provider_refusal_visible = (await panel.getByRole('alert').allTextContents()).some(text => text.includes(created.reason_code));
    }
    await panel.getByRole('button', { name: 'Close session management', exact: true }).click();
    await page.getByRole('button', { name: 'More actions', exact: true }).click();
    await page.getByRole('button', { name: 'File recovery', exact: true }).click();
    const recovery = page.getByRole('region', { name: 'File recovery', exact: true });
    await recovery.getByRole('combobox', { name: 'Protected directory', exact: true }).selectOption(registered.scope_id);
    await recovery.getByText('Manage protected directories', { exact: true }).click();
    await recovery.getByLabel('Retention days', { exact: true }).waitFor();
    report.checks.recovery_reuses_registered_policy = await recovery.getByLabel('Retention days', { exact: true }).inputValue() === '45';
    await recovery.getByLabel('Protection enabled', { exact: true }).uncheck();
    await postAction('/configure', () => recovery.getByRole('button', { name: 'Save protection settings', exact: true }).click());
    await recovery.getByText('Protection settings saved.', { exact: true }).waitFor();
    report.checks.pause_protection_saved = (await read('/protection/scopes')).items.find(item => item.id === registered.scope_id).enabled == false;
    await recovery.getByRole('button', { name: 'Close file recovery', exact: true }).click();
    await changeLanguageViaSettings(page, 'zh-CN');
    await page.getByRole('button', { name: '更多操作', exact: true }).click();
    await page.getByRole('button', { name: '文件恢复', exact: true }).click();
    const localized = page.getByRole('region', { name: '文件恢复', exact: true });
    await localized.getByRole('combobox', { name: '保护目录', exact: true }).selectOption(registered.scope_id);
    await localized.getByText('管理保护目录', { exact: true }).click();
    assert.equal(await localized.getByLabel('保留天数', { exact: true }).inputValue(), '45');
    assert.equal(await localized.getByLabel('存储配额（MiB）', { exact: true }).inputValue(), '512');
    assert.equal(await localized.getByLabel('启用保护', { exact: true }).isChecked(), false);
    await localized.getByRole('button', { name: '登记并保护目录', exact: true }).waitFor();
    report.checks.chinese_controls = true;
    await page.setViewportSize({ width: 390, height: 844 });
    await localized.getByRole('button', { name: '保存保护设置', exact: true }).scrollIntoViewIfNeeded();
    report.checks.mobile_no_horizontal_overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
    await page.screenshot({ path: path.join(directory, 'onboarding-recovery-mobile-zh.png') });
    fs.writeFileSync(path.join(directory, 'reopen-fixture.json'), JSON.stringify({ ...fixture, work_id: report.work_id, scope_id: report.scope_id }));
    report.checks.no_execution_sent = !writes.some(route => route.endsWith('/runs'));
    assert.deepEqual(errors, []); assert(Object.values(report.checks).every(value => value === true));
    report.page_errors = errors; report.result = 'PASS';
  } catch (error) {
    report.result = 'FAIL'; report.reason = error.message;
    if (page) { await page.screenshot({ path: path.join(directory, 'onboarding-failure.png'), fullPage: true }).catch(() => {}); fs.writeFileSync(path.join(directory, 'onboarding-failure.txt'), await page.locator('body').innerText().catch(() => '')); }
    process.exitCode = 1;
  } finally {
    fs.writeFileSync(destination, JSON.stringify(report, null, 2));
    if (browser) await browser.close();
  }
})();
