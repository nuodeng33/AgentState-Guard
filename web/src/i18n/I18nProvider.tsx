/**
 * i18n context: one active language at a time, follow-system by default.
 *
 * The default context value is a static en-US translator so tests and
 * embedded usages work without a provider; the app root installs the real
 * provider which tracks the persisted UI preference.
 */

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { resolveLocale } from './detect';
import {
  loadLanguagePreference,
  saveLanguagePreference,
  type LanguagePreference,
  type Locale,
} from './locale';
import { MESSAGES, type MessageKey } from './messages';

export type Translate = (key: MessageKey, params?: Record<string, string | number>) => string;

export interface I18nValue {
  locale: Locale;
  preference: LanguagePreference;
  setPreference: (preference: LanguagePreference) => void;
  t: Translate;
}

function translate(
  locale: Locale,
  key: MessageKey,
  params?: Record<string, string | number>,
): string {
  let text = MESSAGES[locale][key] ?? MESSAGES['en-US'][key] ?? key;
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      text = text.split(`{${name}}`).join(String(value));
    }
  }
  return text;
}

const defaultValue: I18nValue = {
  locale: 'en-US',
  preference: 'system',
  setPreference: () => {},
  t: (key, params) => translate('en-US', key, params),
};

const I18nContext = createContext<I18nValue>(defaultValue);

export function I18nProvider({
  children,
  systemLanguage,
}: {
  children: ReactNode;
  /** Injectable for tests; defaults to navigator.language. */
  systemLanguage?: string;
}) {
  const [preference, setPreferenceState] = useState<LanguagePreference>(() =>
    loadLanguagePreference(),
  );
  const detected =
    systemLanguage ?? (typeof navigator !== 'undefined' ? navigator.language : 'en-US');
  const locale = resolveLocale(preference, detected);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value = useMemo<I18nValue>(
    () => ({
      locale,
      preference,
      setPreference: (next) => {
        saveLanguagePreference(next);
        setPreferenceState(next);
      },
      t: (key, params) => translate(locale, key, params),
    }),
    [locale, preference],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  return useContext(I18nContext);
}

export function useT(): Translate {
  return useContext(I18nContext).t;
}
