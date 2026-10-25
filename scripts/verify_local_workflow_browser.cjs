// Real loopback Core only: no API interception, demo mode or provider execution.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { parseArgs } = require('node:util');
const { createWorkFromSidebar, changeLanguageViaSettings } = require('./work_settings_browser.cjs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { values } = parseArgs({ options: { url: { type: 'string' }, 'report-dir': { type: 'string' },
  phase: { type: 'string' }, 'work-id': { type: 'string' } } });
const origin = new URL(values.url);
assert.equal(origin.protocol, 'http:');
assert.equal(origin.hostname, '127.0.0.1');
assert(['create', 'reopen'].includes(values.phase));
const directory = path.resolve(values['report-dir']);
fs.mkdirSync(directory, { recursive: true });
const report = { result: 'RUNNING', mode: 'real Core / manual Work / no model calls', checks: {} };
const destination = path.join(directory, `browser-${values.phase}.json`);
const title = 'Local acceptance 工作';
const requirement = '# Original requirement\nKeep this original content.\n| --- | --- |';
const artifactBody = '## Result\nVerified local original body.\n| --- | --- |';

(async () => {
  let browser, page;
  const errors = [], apiRequests = [];
  try {
    browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'en-US', reducedMotion: 'reduce' });
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(request.url()); });
    const read = suffix => page.evaluate(async suffix => {
      const session = await fetch('/api/session').then(r => r.json());
      const response = await fetch('/api/v1' + suffix, { headers: { 'X-Session-Token': session.token } });
      if (!response.ok) throw new Error('REAL_CORE_READ_FAILED: ' + response.status);
      return response.json();
    }, suffix);
    const postAction = async (suffix, action) => {
      const [response] = await Promise.all([page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(suffix)), action()]);
      assert(response.ok(), `POST ${suffix}: ${response.status()}`);
      return response.json();
    };
    if (values.phase === 'create') {
      await page.goto(origin.href + '?workspace=1');
      await createWorkFromSidebar(page);
      let dialog = page.getByRole('dialog');
      await dialog.getByLabel('Work title', { exact: true }).fill(title);
      await dialog.getByLabel('Owner', { exact: true }).fill('Local owner');
      await dialog.getByLabel('Requirement', { exact: true }).fill(requirement);
      const created = await postAction('/api/v1/works', () => dialog.getByRole('button', { name: 'Save', exact: true }).click());
      report.work_id = created.work_id;
      const root = '/works/' + report.work_id;
      await page.getByRole('button', { name: 'More actions', exact: true }).waitFor();
      report.checks.created_work = (await read(root)).requirement === requirement;
      await page.getByRole('button', { name: 'More actions', exact: true }).click();
      await page.getByRole('button', { name: 'Add task', exact: true }).click();
      dialog = page.getByRole('dialog');
      await dialog.getByLabel('Title', { exact: true }).fill('Local task');
      await dialog.getByLabel('Content', { exact: true }).fill('A task recorded through the real UI.');
      await postAction('/tasks', () => dialog.getByRole('button', { name: 'Save', exact: true }).click());
      report.checks.created_task = (await read(root)).tasks.some(task => task.title === 'Local task');
      await page.getByRole('button', { name: 'More actions', exact: true }).click();
      await page.getByRole('button', { name: 'Save artifact', exact: true }).click();
      dialog = page.getByRole('dialog');
      await dialog.getByLabel('Title', { exact: true }).fill('Local artifact');
      await dialog.getByLabel('Content', { exact: true }).fill(artifactBody);
      const artifact = await postAction('/artifacts', () => dialog.getByRole('button', { name: 'Save', exact: true }).click());
      const saved = await read(`${root}/artifacts/${artifact.artifact_id}/${artifact.version}`);
      report.checks.manual_artifact = saved.body === artifactBody && saved.source === 'MANUAL' && !saved.source_run_id;
      await page.getByRole('button', { name: 'Continue workbench', exact: true }).click();
      await page.getByRole('button', { name: 'Add related content', exact: true }).click();
      await page.getByRole('button', { name: 'Local artifact', exact: false }).click();
      await page.getByText('Verified local original body.', { exact: true }).waitFor();
      const original = await page.locator('.work-prose').allTextContents();
      await changeLanguageViaSettings(page, 'zh-CN');
      await page.getByRole('button', { name: '放大', exact: true }).waitFor();
      report.checks.language_preserved_content = JSON.stringify(await page.locator('.work-prose').allTextContents()) === JSON.stringify(original);
      await changeLanguageViaSettings(page, 'en-US');
      await postAction('/view', () => page.getByRole('button', { name: 'Save workspace', exact: true }).click());
      const view = await read(root + '/view');
      report.checks.layout_saved = view.revision > 0 && view.state.cards.some(card => card.ref_id === artifact.artifact_id);
      fs.writeFileSync(path.join(directory, 'expected-view.json'), JSON.stringify(view));
      await page.screenshot({ path: path.join(directory, 'real-workbench.png') });
      await page.getByRole('button', { name: 'More actions', exact: true }).click();
      await page.getByRole('button', { name: 'Delivery', exact: true }).click();
      const delivery = page.getByRole('region', { name: 'Delivery', exact: true });
      await delivery.getByRole('checkbox', { name: /Local artifact/ }).check();
      await delivery.getByLabel('Responsible person', { exact: true }).fill('Local implementer');
      const submitted = await postAction('/submissions', () => delivery.getByRole('button', { name: 'Submit delivery', exact: true }).click());
      report.submission_id = submitted.submission_id;
      await delivery.getByLabel('Reviewer', { exact: true }).fill('Local reviewer');
      await delivery.getByLabel('Review decision').selectOption('APPROVED');
      await delivery.getByLabel('Feedback', { exact: true }).fill('Read the selected artifact version.');
      await postAction('/reviews', () => delivery.getByRole('button', { name: 'Record review', exact: true }).click());
      const reviewed = (await read(root + '/submissions')).items.find(item => item.id === report.submission_id);
      report.checks.review_separate_from_acceptance = reviewed.reviews.length === 1 && reviewed.reviews[0].decision === 'APPROVED' && reviewed.acceptances.length === 0;
      await delivery.getByLabel('Acceptance owner', { exact: true }).fill('Local owner');
      await delivery.getByLabel('Acceptance decision').selectOption('ACCEPTED');
      await delivery.getByLabel('Acceptance basis', { exact: true }).fill('Verified original manual content and version.');
      await postAction('/acceptances', () => delivery.getByRole('button', { name: 'Record acceptance', exact: true }).click());
      const accepted = (await read(root + '/submissions')).items.find(item => item.id === report.submission_id);
      report.checks.owner_accepted = accepted.acceptances.length === 1 && accepted.acceptances[0].decision === 'ACCEPTED';
      await page.screenshot({ path: path.join(directory, 'real-delivery.png') });
    } else {
      report.work_id = values['work-id'];
      await page.goto(origin.href + '?workspace=1&work=' + encodeURIComponent(report.work_id));
      await page.getByText('Verified local original body.', { exact: true }).waitFor();
      const root = '/works/' + report.work_id;
      const work = await read(root);
      report.checks.work_persisted = work.title === title && work.requirement === requirement && work.tasks.length === 1;
      const artifact = work.artifacts[0];
      const saved = await read(`${root}/artifacts/${artifact.id}/${artifact.version}`);
      report.checks.artifact_persisted = saved.body === artifactBody && saved.source === 'MANUAL';
      report.checks.layout_persisted = JSON.stringify(await read(root + '/view')) === fs.readFileSync(path.join(directory, 'expected-view.json'), 'utf8');
      const submissions = await read(root + '/submissions');
      report.checks.acceptance_persisted = submissions.items.length === 1 && submissions.items[0].reviews.length === 1 && submissions.items[0].acceptances[0].decision === 'ACCEPTED';
      await changeLanguageViaSettings(page, 'zh-CN');
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByRole('button', { name: '放大', exact: true }).waitFor();
      report.checks.mobile_readable = !(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth));
      await page.screenshot({ path: path.join(directory, 'real-reopened-mobile.png') });
    }
    assert(apiRequests.length > 0, 'The browser must contact real Core routes');
    assert.deepEqual(errors, []);
    assert(Object.values(report.checks).every(value => value === true));
    report.result = 'PASS';
    report.page_errors = errors;
    report.actual_api_requests = apiRequests.length;
  } catch (error) {
    report.result = 'FAIL'; report.reason = error.message;
    if (page) await page.screenshot({ path: path.join(directory, `failure-${values.phase}.png`) }).catch(() => {});
    process.exitCode = 1;
  } finally {
    fs.writeFileSync(destination, JSON.stringify(report, null, 2));
    if (browser) await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
