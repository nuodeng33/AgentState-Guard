/**
 * Settings: local UI preferences only. Nothing on this page touches
 * authority, security, evidence, or network state.
 */

import { LanguageSelector } from '../components/LanguageSelector';
import { useT } from '../i18n/I18nProvider';

export default function SettingsPage() {
  const t = useT();
  return (
    <div>
      <section className="card">
        <LanguageSelector />
      </section>
      <p className="settings-note">{t('settings.preferenceNote')}</p>
    </div>
  );
}
