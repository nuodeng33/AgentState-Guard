/**
 * Desktop i18n tests: follow-system resolution, preference persistence,
 * interpolation/fallback, provider switching, and the language selector.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { LanguageSelector } from '../components/LanguageSelector';
import { resolveLocale } from './detect';
import { I18nProvider, useT } from './I18nProvider';
import {
  LANGUAGE_PREFERENCE_KEY,
  loadLanguagePreference,
  saveLanguagePreference,
} from './locale';

beforeEach(() => {
  window.localStorage.clear();
});

describe('resolveLocale follow-system', () => {
  it('maps Chinese system languages to zh-CN, everything else to en-US', () => {
    expect(resolveLocale('system', 'zh-CN')).toBe('zh-CN');
    expect(resolveLocale('system', 'zh-TW')).toBe('zh-CN');
    expect(resolveLocale('system', 'zh')).toBe('zh-CN');
    expect(resolveLocale('system', 'en-US')).toBe('en-US');
    expect(resolveLocale('system', 'ja-JP')).toBe('en-US');
    expect(resolveLocale('system', '')).toBe('en-US');
  });

  it('an explicit preference always wins over the system language', () => {
    expect(resolveLocale('en-US', 'zh-CN')).toBe('en-US');
    expect(resolveLocale('zh-CN', 'en-US')).toBe('zh-CN');
  });
});

describe('language preference persistence', () => {
  it('round-trips through localStorage and ignores invalid values', () => {
    expect(loadLanguagePreference()).toBe('system');
    saveLanguagePreference('zh-CN');
    expect(loadLanguagePreference()).toBe('zh-CN');
    window.localStorage.setItem(LANGUAGE_PREFERENCE_KEY, 'fr-FR');
    expect(loadLanguagePreference()).toBe('system');
  });
});

function Probe({ messageKey }: { messageKey: Parameters<ReturnType<typeof useT>>[0] }) {
  const t = useT();
  return <p data-testid="probe">{t(messageKey, { label: 'Runtime', count: 2 })}</p>;
}

describe('I18nProvider', () => {
  it('renders zh-CN when the preference says so and sets document lang', () => {
    saveLanguagePreference('zh-CN');
    render(
      <I18nProvider systemLanguage="en-US">
        <Probe messageKey="nav.runtime" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('probe').textContent).toBe('运行环境');
    expect(document.documentElement.lang).toBe('zh-CN');
  });

  it('follows the system language when no preference is stored', () => {
    render(
      <I18nProvider systemLanguage="zh-Hans-CN">
        <Probe messageKey="nav.runtime" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('probe').textContent).toBe('运行环境');
  });

  it('interpolates params in the active locale', () => {
    render(
      <I18nProvider systemLanguage="en-US">
        <Probe messageKey="evidence.toggle" />
      </I18nProvider>,
    );
    expect(screen.getByTestId('probe').textContent).toBe('Evidence (2)');
  });

  it('falls back to en-US rendering without a provider (tests/embedding)', () => {
    render(<Probe messageKey="view.loading" />);
    expect(screen.getByTestId('probe').textContent).toBe('Loading Runtime…');
  });
});

describe('LanguageSelector', () => {
  it('offers system / en-US / zh-CN and persists the choice immediately', () => {
    render(
      <I18nProvider systemLanguage="en-US">
        <LanguageSelector />
      </I18nProvider>,
    );

    const zh = screen.getByRole('radio', { name: '简体中文' });
    fireEvent.click(zh);

    expect(window.localStorage.getItem(LANGUAGE_PREFERENCE_KEY)).toBe('zh-CN');
    // One language at a time: after switching, the legend renders in Chinese.
    expect(screen.getByText('语言')).toBeTruthy();
  });
});
