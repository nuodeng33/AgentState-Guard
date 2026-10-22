/**
 * Language preference selector: follow-system, English, or 简体中文.
 * A local UI preference only — switching language never changes any
 * authority, security, or protocol state. Native radio inputs keep it
 * keyboard accessible.
 */

import { useI18n } from '../i18n/I18nProvider';
import type { LanguagePreference } from '../i18n/locale';

const OPTIONS: Array<{ value: LanguagePreference; labelKey: 'language.system' | 'language.en-US' | 'language.zh-CN' }> = [
  { value: 'system', labelKey: 'language.system' },
  { value: 'en-US', labelKey: 'language.en-US' },
  { value: 'zh-CN', labelKey: 'language.zh-CN' },
];

export function LanguageSelector({ compact = false }: { compact?: boolean }) {
  const { preference, setPreference, t } = useI18n();
  if (compact) return (
    <select className="language-switch" aria-label={t('settings.language')} title={t('settings.language')}
      value={preference} onChange={event => setPreference(event.target.value as LanguagePreference)}>
      {OPTIONS.map(option => <option key={option.value} value={option.value}>{t(option.labelKey)}</option>)}
    </select>
  );
  return (
    <fieldset className="language-selector">
      <legend className="language-label">{t('settings.language')}</legend>
      {OPTIONS.map((option) => (
        <label key={option.value} className="language-option">
          <input
            type="radio"
            name="language-preference"
            value={option.value}
            checked={preference === option.value}
            onChange={() => setPreference(option.value)}
          />
          <span>{t(option.labelKey)}</span>
        </label>
      ))}
      <p className="language-note">{t('settings.languageNote')}</p>
    </fieldset>
  );
}
