// Browser contract/visual checks with explicit fixtures; never starts an Agent.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = path.resolve('artifacts/work-feedback');
fs.mkdirSync(directory, { recursive: true });
let base = process.env.ASG_FEEDBACK_URL;
const workId = '11111111-1111-4111-8111-111111111111';
const sessionId = '22222222-2222-4222-8222-222222222222';
const runId = '33333333-3333-4333-8333-333333333333';
const artifactId = '44444444-4444-4444-8444-444444444444';
let requirement = 1, waiting = true;
const commands = [], errors = [], unexpected = [];
const capabilities = { send: true, stream: true, cancel: true, responses: true, write: false };
const session = { id: sessionId, work_id: workId, provider: 'CODEX', state: 'READY', revision: 1,
  scope_id: 'fixture-scope', scope_root: 'C:/Disposable/project', source_id: 'local', task_id: null, read_only: true, capabilities };
const execution = () => ({ id: runId, session_id: sessionId, requirement_version: 1,
  state: waiting ? 'WAITING_INPUT' : 'COMPLETED', input_summary: 'Browser fixture only', last_seq: 0,
  pending_actions: waiting ? [{ event_seq: 1, type: 'QUESTION', action_ref: 'fixture-action',
    payload: { questions: [{ id: 'target', question: 'Which target?', header: 'Target', secret: false, options: [] }] } }] : [],
  created_at: '2026-10-15T01:00:00Z' });
(async () => {
  fs.writeFileSync(path.join(directory, 'browser.json'), JSON.stringify({ result: 'RUNNING', mode: 'browser-with-fixture-API' }));
  // Own the temporary server in this process so cleanup cannot leave a shell child.
  let server;
  if (!base) {
    const dist = path.resolve(process.env.ASG_FEEDBACK_DIST || path.join(directory, 'dist'));
    server = require('node:http').createServer((request, response) => {
      const pathname = new URL(request.url, 'http://local').pathname;
      const file = path.resolve(dist, pathname === '/' ? 'index.html' : '.' + pathname);
      if (!file.startsWith(dist + path.sep)) { response.writeHead(403).end(); return; }
      try {
        response.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' })[path.extname(file)] || 'application/octet-stream');
        response.end(fs.readFileSync(file));
      } catch { response.writeHead(404).end(); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    base = 'http://127.0.0.1:' + server.address().port;
  }
  let browser;
  try {
    browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'en-US' });
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', async route => {
      const request = route.request(), url = new URL(request.url()), endpoint = url.pathname;
      const respond = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
      if (request.method() === 'POST') {
        commands.push({ path: endpoint, body: request.postDataJSON() });
        if (endpoint.endsWith('/responses')) { waiting = false; return respond({ revision: 2 }); }
        if (endpoint.endsWith('/runs')) return respond({ run_id: runId, session_id: sessionId });
        if (endpoint.endsWith('/view')) return respond({ revision: 2 });
        unexpected.push(endpoint); return route.fulfill({ status: 404, body: '{}' });
      }
      if (endpoint === '/api/session') return respond({ token: 'browser-fixture-token' });
      if (endpoint.endsWith('/execution-summary')) return respond({ work_id: workId, requirement_version: requirement,
        work_revision: requirement, observed_at: '2026-10-15T01:00:00Z', has_more: false,
        counts: waiting ? { WAITING_INPUT: 1 } : {}, items: waiting ? [{ id: runId, session_id: sessionId, provider: 'CODEX',
          state: 'WAITING_INPUT', requirement_version: 1, updated_at: '2026-10-15T01:00:00Z' }] : [] });
      if (endpoint === `/api/v1/works/${workId}`) return respond({ id: workId, title: 'Work feedback verification', owner: 'Local test',
        revision: requirement, requirement_version: requirement, requirement: 'Review the requested changes', tasks: [],
        artifacts: [{ id: artifactId, title: 'Verified candidate', version: 2, requirement_version: 1 }] });
      if (endpoint.endsWith('/view')) return respond({ revision: 1, state: null });
      if (endpoint.includes('/requirements/')) return respond({ body: 'Requirement fixture ' + requirement });
      if (endpoint.endsWith('/agents')) return respond({ items: [] });
      if (endpoint.endsWith('/sessions')) return respond({ items: [session], has_more: false });
      if (endpoint.endsWith('/sessions/' + sessionId)) return respond(session);
      if (endpoint.endsWith('/sessions/' + sessionId + '/runs')) return respond({ items: [execution()], has_more: false });
      if (endpoint.endsWith('/runs/' + runId)) return respond(execution());
      if (endpoint.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
      unexpected.push(endpoint); return route.fulfill({ status: 404, body: '{}' });
    });
    await page.goto(`${base}/?workspace=1&work=${workId}`); await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: '1 execution needs attention' }).click();
    await page.getByRole('button', { name: /CODEX.*Needs input/ }).click();
    await page.getByLabel('Which target?', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(directory, 'desktop-question.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    const panel = await page.locator('.work-sessions').boundingBox();
    assert(panel.x >= 0 && panel.x + panel.width <= 391);
    await page.getByRole('button', { name: 'Close session management' }).click();
    assert(await page.getByRole('button', { name: '1 execution needs attention' }).isVisible());
    await page.getByRole('button', { name: '1 execution needs attention' }).click();
    await page.getByRole('button', { name: /CODEX.*Needs input/ }).click();
    await page.getByLabel('Which target?', { exact: true }).waitFor();
    await page.waitForFunction(() => {
      const answer = document.querySelector('[aria-label="Which target?"]');
      const submit = document.querySelector('.work-run-response button');
      const panel = document.querySelector('.work-sessions');
      if (!answer || !submit || !panel) return false;
      const a = answer.getBoundingClientRect(), b = submit.getBoundingClientRect(), p = panel.getBoundingClientRect();
      return a.top >= p.top && b.bottom <= p.bottom;
    }, null, { timeout: 5000 });
    await page.screenshot({ path: path.join(directory, 'mobile-question.png') });
    await page.getByLabel('Which target?', { exact: true }).fill('Preview');
    await page.getByRole('button', { name: 'Submit answer' }).click();
    await page.getByRole('button', { name: 'Close session management' }).click();
    requirement = 2;
    await page.getByRole('button', { name: 'More actions' }).click();
    await page.getByRole('button', { name: 'Session management', exact: true }).click();
    await page.getByRole('button', { name: 'Use current requirement for next execution' }).waitFor({ timeout: 10000 });
    await page.getByLabel('Execution input', { exact: true }).fill('Apply the revised requirement');
    assert(await page.getByRole('button', { name: 'Start execution' }).isDisabled());
    await page.getByText('Context to send', { exact: false }).click();
    await page.getByLabel('Verified candidate · v2').check();
    await page.getByRole('button', { name: 'Use current requirement for next execution' }).click();
    await page.screenshot({ path: path.join(directory, 'mobile-context.png') });
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/runs') && response.request().method() === 'POST'),
      page.getByRole('button', { name: 'Start execution' }).click(),
    ]);
    const sent = commands.find(command => command.path.endsWith('/runs'));
    assert.equal(sent.body.requirement_version, 2);
    assert.deepEqual(sent.body.context_refs, [{ kind: 'artifact', ref_id: artifactId, version: 2 }]);
    await page.getByRole('button', { name: 'Close session management' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: path.join(directory, 'desktop-return.png') });
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
    fs.writeFileSync(path.join(directory, 'browser.json'), JSON.stringify({ result: 'PASS', mode: 'browser-with-fixture-API',
      checks: ['closed-panel-attention', 'exact-question', 'answer', 'return', 'requirement-change', 'explicit-context', '390px-fit', '390px-answer-and-submit-visible'],
      page_errors: errors, unexpected_requests: unexpected }, null, 2));
    console.log('Browser feedback checks PASS (fixture API; no model calls)');
    await context.close();
  } finally { if (browser) await browser.close(); if (server) await new Promise(resolve => server.close(resolve)); }
})().catch(error => {
  fs.writeFileSync(path.join(directory, 'browser.json'), JSON.stringify({ result: 'FAIL', mode: 'browser-with-fixture-API', reason: error.message }, null, 2));
  console.error(error); process.exitCode = 1;
});
