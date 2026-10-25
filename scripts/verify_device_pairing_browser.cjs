// Supplemental actual browser/Core pairing. No demo API, model call or physical-phone claim.
const assert = require('node:assert/strict');
const fs = require('node:fs'); const path = require('node:path'); const { pathToFileURL } = require('node:url');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const directory = path.resolve(process.argv[3]); fs.mkdirSync(directory, { recursive: true });
const root = path.resolve(__dirname, '..');
const report = { result: 'RUNNING', checks: {}, mode: 'real Core / Vite source / scoped ASGI mobile driver; supplemental only' };
const destination = path.join(directory, 'device-browser.json');
assert.equal(new URL(fixture.core_url).hostname, '127.0.0.1');
assert.equal(new URL(fixture.driver_url).hostname, '127.0.0.1');
(async () => {
 let browser, page, vite; let stage = 'VITE_START'; const errors = [], posts = [];
 const drive = async action => {
   const response = await fetch(fixture.driver_url, { method: 'POST',
     headers: { 'Content-Type': 'application/json', 'X-Verify-Token': fixture.driver_token }, body: JSON.stringify({ action }) });
   assert(response.ok, 'DRIVER_' + action.toUpperCase() + '_FAILED'); return response.json();
 };
 const postAction = async (suffix, action) => {
   const [response] = await Promise.all([page.waitForResponse(response => response.request().method() === 'POST' &&
     new URL(response.url()).pathname.endsWith(suffix)), action()]);
   assert(response.ok(), 'CORE_ACTION_FAILED'); return response.json();
 };
 const poll = id => page.waitForResponse(response => response.request().method() === 'GET' &&
   new URL(response.url()).pathname === '/api/v1/device-link/pairings/' + id);
 const start = async () => postAction('/device-link/pairings',
   () => page.getByRole('button', { name: 'Add mobile device', exact: true }).click());
 const confirm = async id => postAction('/pairings/' + id + '/confirm',
   () => page.getByRole('button', { name: 'Codes match', exact: true }).click());
 const cancel = async id => postAction('/pairings/' + id + '/cancel',
   () => page.getByRole('button', { name: 'Cancel', exact: true }).click());
 const waitInspect = async predicate => {
   const deadline = Date.now() + 15000;
   while (Date.now() < deadline) {
     const state = await drive('inspect');
     if (predicate(state)) return state;
     await new Promise(resolve => setTimeout(resolve, 50));
   }
   throw new Error('DRIVER_STATE_TIMEOUT');
 };
 try {
   const viteModule = await import(pathToFileURL(require.resolve('vite', { paths: [path.join(root, 'web')] })).href);
   vite = await viteModule.createServer({ root: path.join(root, 'web'), configFile: path.join(root, 'web/vite.config.ts'),
     logLevel: 'error', mode: 'development',
     server: { host: '127.0.0.1', port: 0, strictPort: false, proxy: { '/api': fixture.core_url } } });
   await vite.listen();
   const url = 'http://127.0.0.1:' + vite.httpServer.address().port;
   stage = 'BROWSER_START';
   browser = await chromium.launch({ headless: true, chromiumSandbox: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
   page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, locale: 'en-US', reducedMotion: 'reduce' });
   page.on('pageerror', () => errors.push('PAGE_ERROR'));
   page.on('request', request => { if (request.method() === 'POST') posts.push(new URL(request.url()).pathname); });
   stage = 'PAGE_LOAD';
   await page.goto(url);
   await page.getByRole('button', { name: 'Workspace account', exact: true }).click();
   await page.getByRole('button', { name: 'Settings', exact: true }).click();
   await page.getByRole('button', { name: 'Devices', exact: true }).click();
   stage = 'LINK_ENABLE';
   await postAction('/device-link/enable', () => page.getByRole('button', { name: 'Enable Mobile Link', exact: true }).click());
   report.checks.explicit_enable = true;
   stage = 'PAIRING';
   for (const order of ['android_first', 'desktop_first']) {
     const invitation = await start();
     const id = invitation.session_id;
     if (order === 'android_first') {
       for (let i = 0; i < 3; i++) await poll(id);
       assert(await page.locator('.qr-card svg').isVisible(), 'QR_DROPPED');
       assert(await page.getByText(/Pairing offer expires in/).isVisible(), 'EXPIRY_DROPPED');
       report.checks.qr_retained_across_polls = true;
     }
     const mobile = await drive('connect');
     await page.getByLabel('SAS', { exact: true }).waitFor();
     assert((await page.getByLabel('SAS', { exact: true }).innerText()) === mobile.sas, 'SAS_MISMATCH');
     report.checks['sas_exact_' + order] = true;
     if (order === 'android_first') {
       assert((await drive('confirm')).state === 'sas_pending', 'EARLY_ANDROID_CONFIRM');
       assert((await confirm(id)).state === 'confirmed_both', 'DUAL_CONFIRM_MISSING');
     } else {
       assert((await confirm(id)).state === 'sas_pending', 'EARLY_DESKTOP_CONFIRM');
       assert((await drive('confirm')).state === 'confirmed_both', 'DUAL_CONFIRM_MISSING');
     }
     const completed = await drive('complete');
     assert(completed.bound && completed.registry_count === 1, 'BINDING_NOT_DURABLE');
     await page.getByText('Device paired', { exact: true }).waitFor();
     report.checks[order + '_bound'] = true;
     await page.getByText('Acceptance Android', { exact: true }).waitFor();
     report.checks['bound_list_' + order] = true;
     const authenticated = await drive('authenticate');
     assert(authenticated.verified && authenticated.authenticated_read, 'MUTUAL_AUTH_FAILED');
     report.checks['mutual_auth_' + order] = true;
     await page.screenshot({ path: path.join(directory, 'paired-' + order + '.png') });
     await page.getByRole('button', { name: 'Done', exact: true }).click();
     await postAction('/revoke', () => page.getByRole('button', { name: 'Revoke', exact: true }).click());
     await page.getByText('Acceptance Android', { exact: true }).waitFor({ state: 'detached' });
     assert((await drive('revoked_token')).rejected, 'REVOKED_TOKEN_ACCEPTED');
     report.checks.revoke_invalidates_token = true;
   }
   // A genuine failed registry write cannot become UI pairing success.
   const failed = await start(); await drive('connect');
   await page.getByLabel('SAS', { exact: true }).waitFor();
   await drive('confirm'); await confirm(failed.session_id);
   await drive('refuse_binding');
   const refused = await drive('complete');
   assert(!refused.bound && refused.registry_count === 0, 'FAILED_BINDING_STORED');
   for (let i = 0; i < 2; i++) await poll(failed.session_id);
   assert(await page.getByText('Confirming…', { exact: true }).isVisible(), 'FAILED_BINDING_NOT_PENDING');
   assert(await page.getByText('Device paired', { exact: true }).count() === 0, 'FALSE_PAIRING_SUCCESS');
   report.checks.binding_failure_not_paired = true;
   report.checks.binding_failure_registry_empty = (await drive('inspect')).registry_count === 0;
   await cancel(failed.session_id);
   // Hold an actual previously computed Core reply, cancel, then release the stale reply.
   const late = await start(); await drive('connect');
   await page.getByLabel('SAS', { exact: true }).waitFor();
   await drive('delay_poll');
   await waitInspect(state => state.delayed);
   report.checks.late_cancel_response_exercised = true;
   await cancel(late.session_id);
   const staleResponse = poll(late.session_id);
   await drive('release_poll'); await staleResponse;
   await page.getByRole('button', { name: 'Add mobile device', exact: true }).waitFor();
   assert(await page.getByRole('button', { name: 'Codes match', exact: true }).count() === 0, 'STALE_PAIRING_REOPENED');
   assert((await drive('inspect')).state === 'cancelled', 'CANCEL_NOT_DURABLE');
   report.checks.late_cancel_not_reopened = true;
   report.checks.no_model_requests = !posts.some(value => value.startsWith('/api/ai/'));
   report.checks.no_page_errors = errors.length === 0;
   assert(Object.values(report.checks).every(value => value === true), 'CHECK_FAILED');
   report.result = 'PASS';
 } catch (error) {
   // Do not export request bodies, invitation tickets, tokens, QR contents or private keys.
   report.result = 'FAIL'; report.failed_stage = stage; report.error_type = error.name; report.reason = /No usable sandbox|Chromium sandboxing failed/.test(error.message ?? '') ? 'BROWSER_SANDBOX_UNAVAILABLE' : /^[A-Z0-9_]+$/.test(error.message ?? '') ? error.message : 'BROWSER_CHECK_FAILED';
 } finally {
   if (browser) await browser.close();
   if (vite) await vite.close();
   fs.writeFileSync(destination, JSON.stringify(report, null, 2));
 }
 process.exitCode = report.result === 'PASS' ? 0 : 1;
})();
