// Shared acceptance navigation through public UI only. No API or DOM mutation.
const assert = require('node:assert/strict');

async function createWorkFromSidebar(page) {
  await page.locator('.work-layout:visible .work-sidebar').getByRole('button', {
    name: /^(New work|新建工作)$/, exact: true,
  }).click();
}

async function changeLanguageViaSettings(page, locale, { inspectPreference } = {}) {
  assert(['en-US', 'zh-CN'].includes(locale), 'Unsupported acceptance locale');
  await page.getByRole('button', { name: /^(Workspace account|工作区头像)$/, exact: true }).click();
  await page.locator('.work-profile-popover').getByRole('button', { name: /^(Settings|设置)$/, exact: true }).click();
  const radio = page.getByRole('radio', { name: locale === 'en-US' ? 'English' : '简体中文', exact: true });
  await radio.check();
  assert.equal(await radio.isChecked(), true, 'Language preference must be selected in Settings');
  if (inspectPreference) await inspectPreference(radio);
  await page.getByRole('button', { name: /^(My work|我的工作)$/, exact: true }).click();
  await page.locator('.work-layout:visible').waitFor();
}

module.exports = { createWorkFromSidebar, changeLanguageViaSettings };
