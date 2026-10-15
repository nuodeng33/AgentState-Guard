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

export function LanguageSelector() {
  const { preference, setPreference, t } = useI18n();
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
