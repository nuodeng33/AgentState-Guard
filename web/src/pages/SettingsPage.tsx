/**
 * Settings: local UI preferences + the AI provider block. The provider
 * section only talks to the frozen /api/ai/models and /api/ai/test endpoints'
 * existing provider DTOs; the API key stays in memory only.
 */

import { apiClient, type ApiClient } from '../api/client';
import { LanguageSelector } from '../components/LanguageSelector';
import { useT } from '../i18n/I18nProvider';
import { SettingsAiSection } from './SettingsAiSection';

export default function SettingsPage({ client = apiClient }: { client?: ApiClient }) {
  const t = useT();
  return (
    <div>
      <section className="card">
        <LanguageSelector />
      </section>
      <p className="settings-note">{t('settings.preferenceNote')}</p>
      {client && <SettingsAiSection client={client} />}
    </div>
  );
}
