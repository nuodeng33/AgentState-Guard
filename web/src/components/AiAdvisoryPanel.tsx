/**
 * Pure presentational AI advisory panel (product-ai-advisory-1).
 *
 * Data is injected via props — this component never fetches and never infers
 * authority. Status, severity, reason_code, provider and model are backend
 * machine tokens and render verbatim in every locale. An advisory is a
 * bounded suggestion surface only; it never upgrades state.
 */

import type { AiAdvisory } from '../api/types';
import { useT } from '../i18n/I18nProvider';
import { EvidenceRefs } from './EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from './KeyValue';
import { StateBadge, type BadgeTone } from './StateBadge';

export interface AiAdvisoryPanelProps {
  advisory: AiAdvisory | null;
  phase: 'idle' | 'loading' | 'error';
  error?: string | null;
  onAnalyze?: () => void;
  analyzeLabel?: string;
}

/** Severity tokens are backend-owned (LOW/MEDIUM/HIGH/CRITICAL/UNKNOWN). */
function severityTone(severity: string): BadgeTone {
  switch (severity) {
    case 'LOW':
      return 'ok';
    case 'MEDIUM':
      return 'warn';
    case 'HIGH':
    case 'CRITICAL':
      return 'bad';
    default:
      return 'unknown';
  }
}

export function AiAdvisoryPanel({
  advisory,
  phase,
  error,
  onAnalyze,
  analyzeLabel,
}: AiAdvisoryPanelProps) {
  const t = useT();

  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{t('analyze.title')}</span>
        {onAnalyze && (
          <span className="card-badges">
            <button
              type="button"
              className="btn"
              onClick={onAnalyze}
              disabled={phase === 'loading'}
            >
              {analyzeLabel ?? t('analyze.action')}
            </button>
          </span>
        )}
      </div>

      {phase === 'loading' && (
        <div className="view-state" role="status">
          <span className="spinner" aria-hidden="true" />
          <p>{t('analyze.running')}</p>
        </div>
      )}

      {phase === 'error' && (
        <div className="panel panel-bad" role="alert">
          <p>{t('analyze.failed', { reasonCode: error ?? t('view.apiFailed') })}</p>
        </div>
      )}

      {phase === 'idle' && advisory === null && (
        <p className="muted">{t('ai.advisory.none')}</p>
      )}

      {advisory !== null && advisory.status !== 'AVAILABLE' && (
        <div className="panel panel-warn" role="alert">
          <p className="panel-title">
            <StateBadge
              label={advisory.status}
              tone={advisory.status === 'UNCHANGED' ? 'neutral' : 'bad'}
            />
          </p>
          <p>
            reason_code: <code>{advisory.reason_code}</code>
          </p>
          {advisory.summary !== null && <p>{advisory.summary}</p>}
        </div>
      )}

      {advisory !== null && advisory.status === 'AVAILABLE' && (
        <>
          <KeyValueGrid>
            <KeyValue
              k={t('analyze.severity')}
              v={<StateBadge label={advisory.severity} tone={severityTone(advisory.severity)} />}
            />
            <KeyValue k={t('analyze.provider')} v={orDash(advisory.provider)} />
            <KeyValue k={t('analyze.model')} v={orDash(advisory.model)} />
            <KeyValue k="reason_code" v={<code>{advisory.reason_code}</code>} />
          </KeyValueGrid>
          {advisory.summary !== null && <p>{advisory.summary}</p>}
          {advisory.uncertainties.length > 0 && (
            <>
              <p>{t('analyze.uncertainties')}</p>
              <ul>
                {advisory.uncertainties.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
          {advisory.recommended_checks.length > 0 && (
            <>
              <p>{t('analyze.checks')}</p>
              <ul>
                {advisory.recommended_checks.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
          {advisory.analyzed_at !== null && (
            <p className="muted">{t('analyze.analyzedAt', { time: advisory.analyzed_at })}</p>
          )}
          <EvidenceRefs refs={advisory.evidence_refs} />
        </>
      )}
    </section>
  );
}
