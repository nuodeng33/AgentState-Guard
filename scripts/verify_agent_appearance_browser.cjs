// Real Core/SQLite + fixture Agent metadata. No provider startup or model call.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = fs.mkdtempSync(path.resolve('artifacts/appearance-browser-'));
const python = path.resolve('.venv/bin/python');
const agent = 'agent-appearance-fixture';
const checks = [], errors = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let core, browser, token, work, port;
const check = (name, value) => { assert.equal(value, true, name); checks.push(name); };
const api = async (endpoint, body) => {
  const response = await fetch(`http://127.0.0.1:${port}${endpoint}`, { method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Session-Token': token } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const result = await response.json(); assert(response.ok, JSON.stringify(result)); return result;
};
async function startCore() {
  const out = fs.openSync(path.join(directory, 'core.log'), 'a');
  core = spawn(python, ['-u', '-c', 'import sys,uvicorn; from pathlib import Path; from agentguard.api.server import create_app; root=Path(sys.argv[1]); uvicorn.run(create_app(state_db_path=root/"state.db",config={"base_dir":str(root)}),host="127.0.0.1",port=int(sys.argv[2]),log_level="error")', directory, String(port)],
    { stdio: ['ignore', out, out] });
  fs.closeSync(out);
  for (let i = 0; i < 100; i++) {
    if (core.exitCode !== null) throw new Error('Isolated Core exited before readiness');
    try { if ((await fetch(`http://127.0.0.1:${port}/api/health`)).ok) return; } catch {}
    await pause(50);
  }
  throw new Error('Core readiness timeout');
}
async function stopCore() {
  if (!core || core.exitCode !== null) return;
  const owned = core; owned.kill('SIGTERM');
  await Promise.race([new Promise(resolve => owned.once('exit', resolve)), pause(6000).then(() => { throw new Error('Owned Core did not stop'); })]);
  assert(owned.exitCode !== null || owned.signalCode !== null);
}
(async () => {
  const listener = net.createServer(); await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  port = listener.address().port; await new Promise(resolve => listener.close(resolve));
  try {
    await startCore(); token = (await api('/api/session')).token;
    work = (await api('/api/v1/works', { request_id: crypto.randomUUID(), title: 'Saved Agent color acceptance', owner: 'Fixture', requirement: 'Persistent display preference only.' })).work_id;
    execFileSync(python, ['-c', 'import sys; from pathlib import Path; from tests.test_work_api import _record_agent; _record_agent(Path(sys.argv[1]),"appearance-browser-snapshot",sys.argv[2])', directory, agent]);
    await api(`/api/v1/works/${work}/agents`, { request_id: crypto.randomUUID(), expected_revision: 1, agent_id: agent });
    browser = await chromium.launch({ ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}), headless: true,
      args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'en-US', reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    const open = async target => { await target.goto(`http://127.0.0.1:${port}/?work=${work}`); await target.getByRole('button', { name: 'Agent colors', exact: true }).click(); await target.getByText('Synced with server').waitFor(); };
    await open(page);
    check('exact six accessible palette choices', await page.getByRole('radio').count() === 6);
    await page.getByRole('radio', { name: 'Lavender' }).click();
    await page.waitForFunction(() => document.querySelector('input[value="#b9a6f4"]')?.checked);
    await page.getByText('Synced with server').waitFor();
    check('real API saved selected palette color', (await api(`/api/v1/works/${work}/agent-appearances`)).items[0].color === '#b9a6f4');
    await page.getByRole('button', { name: 'Close color settings' }).click();
    check('2D film uses saved color', await page.locator('.work-film path[stroke="#b9a6f4"]').count() === 1);
    await page.getByRole('button', { name: 'Agent colors', exact: true }).click(); await page.getByText('Synced with server').waitFor();
    const second = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'en-US' }); await open(second);
    await page.getByRole('radio', { name: 'Rose' }).click(); await page.getByText('Synced with server').waitFor();
    await second.getByRole('radio', { name: 'Green' }).click(); await second.getByText(/changed in another window/).waitFor();
    check('stale window cannot overwrite server color', (await api(`/api/v1/works/${work}/agent-appearances`)).items[0].color === '#e3a5bf');
    await second.getByRole('button', { name: 'Refresh saved colors' }).click(); await second.getByText('Synced with server').waitFor();
    await second.getByRole('radio', { name: 'Green' }).click(); await second.getByText('Synced with server').waitFor();
    await second.close();
    await page.getByRole('button', { name: 'Refresh saved colors' }).click(); await page.getByText('Synced with server').waitFor();
    check('refresh reads another window confirmed choice', await page.getByRole('radio', { name: 'Green' }).isChecked());
    await page.screenshot({ path: path.join(directory, 'colors-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    check('narrow color dialog stays within viewport', await page.locator('.work-appearance-form').evaluate(node => { const box = node.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth && box.bottom <= innerHeight; }));
    await page.screenshot({ path: path.join(directory, 'colors-mobile.png') });
    await page.getByRole('button', { name: 'Close color settings' }).click();
    await stopCore(); await startCore(); token = (await api('/api/session')).token;
    await open(page);
    check('actual Core restart retains color and revision', await page.getByRole('radio', { name: 'Green' }).isChecked() && (await api(`/api/v1/works/${work}/agent-appearances`)).items[0].revision === 3);
    await page.getByRole('button', { name: 'Use default color' }).click(); await page.getByText('Synced with server').waitFor();
    const cleared = (await api(`/api/v1/works/${work}/agent-appearances`)).items[0];
    check('reset retains revisioned NULL preference', cleared.color === null && cleared.revision === 4);
    check('color updates never bump Work revision', (await api(`/api/v1/works/${work}`)).revision === 2);
    check('browser has no page errors', errors.length === 0);
    fs.writeFileSync(path.join(directory, 'report.json'), JSON.stringify({ status: 'PASS', checks, errors, work, scope: 'Real isolated Core/SQLite, deterministic fixture Agent, browser rendering; no model/provider or native Windows UI', directory }, null, 2));
    console.log(JSON.stringify({ status: 'PASS', checks: checks.length, report: path.join(directory, 'report.json') }));
  } catch (error) { fs.writeFileSync(path.join(directory, 'report.json'), JSON.stringify({ status: 'FAIL', checks, errors, failure: String(error), directory }, null, 2)); throw error; }
  finally { await browser?.close(); await stopCore(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
