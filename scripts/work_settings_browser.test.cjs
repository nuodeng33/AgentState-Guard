const assert = require('node:assert/strict');
const { test } = require('node:test');
const { createWorkFromSidebar, changeLanguageViaSettings } = require('./work_settings_browser.cjs');

function fixture() {
  const calls = [];
  const node = prefix => ({
    getByRole: (role, options) => {
      const key = `${prefix}:${role}:${String(options.name)}`;
      return { click: async () => calls.push(`click:${key}`), check: async () => calls.push(`check:${key}`),
        isChecked: async () => true, marker: key };
    },
    waitFor: async () => calls.push(`wait:${prefix}`),
  });
  const page = { getByRole: (...args) => node('page').getByRole(...args), locator: selector => node(selector) };
  return { page, calls };
}

test('language change visits the avatar Settings and explicit preference before returning to Work', async () => {
  const f = fixture();
  let inspected = false;
  await changeLanguageViaSettings(f.page, 'zh-CN', { inspectPreference: async radio => {
    assert.match(radio.marker, /radio:简体中文$/); inspected = true;
    assert.equal(f.calls.length, 3);
  } });
  assert.equal(inspected, true);
  assert.equal(f.calls.length, 5);
  assert.match(f.calls[0], /Workspace account/);
  assert.match(f.calls[1], /work-profile-popover:button/);
  assert.match(f.calls[2], /check:page:radio:简体中文$/);
  assert.match(f.calls[3], /My work/);
  assert.equal(f.calls[4], 'wait:.work-layout:visible');
});

test('English preference and New work use real named UI controls', async () => {
  const f = fixture();
  await changeLanguageViaSettings(f.page, 'en-US');
  assert.match(f.calls[2], /check:page:radio:English$/);
  await createWorkFromSidebar(f.page);
  assert.match(f.calls[5], /work-layout:visible \.work-sidebar:button/);
  assert.match(f.calls[5], /New work/);
});

test('unsupported locale is rejected before any UI action', async () => {
  const f = fixture();
  await assert.rejects(changeLanguageViaSettings(f.page, 'unsupported'), /Unsupported acceptance locale/);
  assert.deepEqual(f.calls, []);
});
