/**
 * AI Provider settings (Settings surface).
 *
 * Sends only the three provider fields to /api/ai/models and /api/ai/test.
 * The API key is held in component state in memory only: it is never written
 * to storage, never included in any read-back, and never echoed — the backend
 * keeps the provider in process memory as well. Results render the backend's
 * own sanitized fields (ok/latency/model ids + bounded error text).
 */

import { useState } from 'react';

import type { ApiClient } from '../api/client';
import { failureText } from '../api/errorText';
import { aiModels, aiTest } from '../api/product';
import { KeyValue, KeyValueGrid } from '../components/KeyValue';
import { SectionHeader } from '../components/SectionHeader';
import { useT } from '../i18n/I18nProvider';

interface ModelRow {
  id: string;
  provider: string;
}

export function SettingsAiSection({ client }: { client: ApiClient }) {
  const t = useT();
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [models, setModels] = useState<ModelRow[] | null>(null);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<'none' | 'models' | 'test'>('none');

  const disabled = busy !== 'none' || baseUrl.trim() === '';

  const fetchModels = () => {
    setBusy('models');
    setModelsError(null);
    setTestResult(null);
    aiModels(baseUrl.trim(), apiKey, client).then(
      (result) => {
        setBusy('none');
        setModels(result.models);
        setModelsError(result.error ?? null);
      },
      (err: unknown) => {
        setBusy('none');
        setModelsError(failureText(err));
      },
    );
  };

  const runTest = () => {
    setBusy('test');
    setTestResult(null);
    aiTest(baseUrl.trim(), apiKey, model.trim(), client).then(
      (result) => {
        setBusy('none');
        if (result.ok) {
          setTestResult({
            ok: true,
            text: t('settings.provider.ok', {
              models: String(result.models_available ?? '—'),
              latency: String(result.latency_ms ?? '—'),
            }),
          });
        } else {
          // Backend error text is already bounded/sanitized by the provider layer.
          setTestResult({
            ok: false,
            text: t('settings.provider.failed', { error: result.error ?? 'TEST_FAILED' }),
          });
        }
      },
      (err: unknown) => {
        setBusy('none');
        setTestResult({ ok: false, text: t('settings.provider.failed', { error: failureText(err) }) });
      },
    );
  };

  return (
    <section className="card" aria-label={t('settings.section.ai')}>
      <SectionHeader title={t('settings.section.ai')} />
      <div className="settings-ai-grid">
        <label className="settings-field">
          <span>{t('settings.provider.baseUrl')}</span>
          <input
            className="cc-content settings-input"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="http://localhost:11434/v1"
            autoComplete="off"
          />
        </label>
        <label className="settings-field">
          <span>{t('settings.provider.apiKey')}</span>
          <input
            className="cc-content settings-input"
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            autoComplete="new-password"
          />
        </label>
        <label className="settings-field">
          <span>{t('settings.provider.model')}</span>
          <input
            className="cc-content settings-input"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            autoComplete="off"
            list="ai-models"
          />
        </label>
        {models !== null && models.length > 0 && (
          <datalist id="ai-models">
            {models.map((row) => (
              <option key={row.id} value={row.id} />
            ))}
          </datalist>
        )}
        <div className="action-row">
          <button
            type="button"
            className="btn"
            onClick={fetchModels}
            disabled={disabled}
          >
            {busy === 'models' ? t('settings.provider.fetching') : t('settings.provider.fetchModels')}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={runTest}
            disabled={disabled}
          >
            {busy === 'test' ? t('settings.provider.testing') : t('settings.provider.test')}
          </button>
        </div>
      </div>
      {models !== null && (
        <KeyValueGrid>
          <KeyValue
            k={t('settings.provider.modelCount', { count: models.length })}
            v={
              models.length === 0 ? (
                <span className="muted">—</span>
              ) : (
                <code>{models[0].id}</code>
              )
            }
          />
        </KeyValueGrid>
      )}
      {modelsError !== null && (
        <p className="panel-bad-inline recovery-action-fail" role="alert">
          {modelsError}
        </p>
      )}
      {testResult !== null && (
        <p className={testResult.ok ? 'muted' : 'panel-bad-inline recovery-action-fail'} role="status">
          {testResult.text}
        </p>
      )}
      <p className="muted">{t('settings.provider.note')}</p>
    </section>
  );
}
