/**
 * Pure presentational evidence detail panel (r4-product-evidence-1).
 *
 * Renders only the sanitized projection the backend already allows: event
 * metadata, chain_ref, related refs and sanitized_detail. Raw payloads are
 * never shown. Status and reason_code stay verbatim machine tokens.
 */

import type { EvidenceDetail } from '../api/types';
import { useT } from '../i18n/I18nProvider';
import { EmptyState } from './EmptyState';
import { KeyValue, KeyValueGrid, orDash } from './KeyValue';
import { StateBadge, type BadgeTone } from './StateBadge';

export interface EvidenceDetailPanelProps {
  detail: EvidenceDetail | null;
  phase: 'idle' | 'loading' | 'error';
  error?: string | null;
  onClose?: () => void;
}

/** Evidence detail status is backend-owned (AVAILABLE/DEGRADED/NOT_FOUND). */
function evidenceStatusTone(status: EvidenceDetail['status']): BadgeTone {
  switch (status) {
    case 'AVAILABLE':
      return 'ok';
    case 'NOT_FOUND':
      return 'neutral';
    default:
      return 'bad';
  }
}

export function EvidenceDetailPanel({ detail, phase, error, onClose }: EvidenceDetailPanelProps) {
  const t = useT();

  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{t('evidence.detail.title')}</span>
        {onClose && (
          <span className="card-badges">
            <button type="button" className="btn" onClick={onClose}>
              {t('common.close')}
            </button>
          </span>
        )}
      </div>

      {phase === 'loading' && (
        <div className="view-state" role="status">
          <span className="spinner" aria-hidden="true" />
          <p>{t('view.loading', { label: t('evidence.detail.title') })}</p>
        </div>
      )}

      {phase === 'error' && (
        <div className="panel panel-bad" role="alert">
          <p>{error ?? t('view.apiFailed')}</p>
        </div>
      )}

      {phase === 'idle' && detail === null && (
        <EmptyState title={t('evidence.detail.title')} detail={t('evidence.detail.empty')} />
      )}

      {detail !== null && (
        <>
          <KeyValueGrid>
            <KeyValue
              k={t('evidence.field.status')}
              v={<StateBadge label={detail.status} tone={evidenceStatusTone(detail.status)} />}
            />
            <KeyValue k="reason_code" v={<code>{detail.reason_code}</code>} />
            <KeyValue k={t('evidence.field.eventId')} v={<code>{detail.event_id}</code>} />
          </KeyValueGrid>

          {detail.status !== 'AVAILABLE' ? (
            <EmptyState
              title={detail.status}
              detail={
                // NOT_FOUND/DEGRADED carry no further authoritative content.
                t('evidence.detail.empty')
              }
              reasonCode={detail.reason_code}
            />
          ) : (
            <>
              <KeyValueGrid>
                <KeyValue k={t('evidence.field.type')} v={orDash(detail.event_type)} />
                <KeyValue k={t('kv.observedAt')} v={orDash(detail.observed_at)} />
                <KeyValue k={t('evidence.field.recordedAt')} v={orDash(detail.recorded_at)} />
                <KeyValue k={t('evidence.field.source')} v={orDash(detail.source)} />
                <KeyValue k={t('evidence.field.subject')} v={orDash(detail.subject ?? null)} />
                <KeyValue k={t('evidence.field.result')} v={orDash(detail.result)} />
                <KeyValue
                  k={t('evidence.field.verification')}
                  v={orDash(detail.verification_summary ?? null)}
                />
                <KeyValue k={t('evidence.field.checkpoint')} v={orDash(detail.checkpoint_id ?? null)} />
                <KeyValue k={t('evidence.field.change')} v={orDash(detail.change_id ?? null)} />
                <KeyValue k={t('evidence.field.chainRef')} v={orDash(detail.chain_ref)} />
              </KeyValueGrid>

              {detail.sanitized_detail && (
                <>
                  <p>{t('evidence.detail.title')}</p>
                  <KeyValueGrid>
                    {detail.sanitized_detail.affected_objects !== undefined && (
                      <KeyValue
                        k={t('evidence.field.affectedObjects')}
                        v={
                          detail.sanitized_detail.affected_objects.length === 0 ? (
                            <span className="muted">—</span>
                          ) : (
                            detail.sanitized_detail.affected_objects.map((obj) => (
                              <code key={obj}>{obj}</code>
                            ))
                          )
                        }
                      />
                    )}
                    {detail.sanitized_detail.verification !== undefined && (
                      <KeyValue
                        k={t('evidence.field.verification')}
                        v={detail.sanitized_detail.verification}
                      />
                    )}
                  </KeyValueGrid>
                </>
              )}

              <p>{t('evidence.related')}</p>
              {detail.related_evidence_refs === undefined || detail.related_evidence_refs.length === 0 ? (
                <span className="evidence-none">{t('evidence.none')}</span>
              ) : (
                <ul className="evidence-list">
                  {detail.related_evidence_refs.map((ref) => (
                    <li key={ref}>
                      <code>{ref}</code>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
