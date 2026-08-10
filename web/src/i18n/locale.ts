/**
 * Desktop localization: supported locales and the follow-system preference.
 *
 * The preference is a local UI setting only. It never influences authority,
 * security, or protocol semantics, and machine tokens (UNKNOWN, REVIEW,
 * reason codes, IDs, …) are never translated.
 */

export type Locale = 'en-US' | 'zh-CN';

/** 'system' follows the OS/browser language; otherwise an explicit locale. */
export type LanguagePreference = 'system' | Locale;

export const LANGUAGE_PREFERENCE_KEY = 'asg.ui.language';

export function loadLanguagePreference(
  storage: Pick<Storage, 'getItem'> = window.localStorage,
): LanguagePreference {
  try {
    const raw = storage.getItem(LANGUAGE_PREFERENCE_KEY);
    return raw === 'en-US' || raw === 'zh-CN' ? raw : 'system';
  } catch {
    return 'system';
  }
}

export function saveLanguagePreference(
  preference: LanguagePreference,
  storage: Pick<Storage, 'setItem'> = window.localStorage,
): void {
  try {
    storage.setItem(LANGUAGE_PREFERENCE_KEY, preference);
  } catch {
    // Preference persistence is best-effort; the UI keeps working in-memory.
  }
}
