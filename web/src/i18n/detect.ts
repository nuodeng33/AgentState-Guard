import type { LanguagePreference, Locale } from './locale';

/**
 * Follow-system resolution: any Chinese system language maps to zh-CN,
 * everything else falls back to en-US. An explicit preference always wins.
 */
export function resolveLocale(
  preference: LanguagePreference,
  systemLanguage: string,
): Locale {
  if (preference !== 'system') return preference;
  return systemLanguage.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US';
}
