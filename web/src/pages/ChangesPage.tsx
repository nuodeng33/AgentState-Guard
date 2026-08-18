/**
 * Changes product surface: the verified activity feed.
 *
 * Consumes only GET /api/v1/changes (r4-p8-1). Every field is rendered
 * verbatim from the backend projection — timestamps stay source times, no
 * lifecycle is derived, and reason_code/status tokens are never translated.
 *
 * Evidence detail is opened only from an item's own event_id, which per the
 * backend contract is a safe, queryable id for GET /api/v1/evidence/{event_id}.
 * Related evidence_refs are shown as references only — never dereferenced.
 */

import { useState } from 'react';

import { apiClient, ApiRequestError, SessionUnavailableError, type ApiClient } from '../api/client';
import { getEvidence } from '../api/product';
import type { ChangeItem, ChangesView, EvidenceDetail } from '../api/types';
import { useR4View } from '../api/useR4View';
import { EmptyState } from '../components/EmptyState';
import { EvidenceDetailPanel } from '../components/EvidenceDetailPanel';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { SectionHeader } from '../components/SectionHeader';
import { StateBadge, viewStatusTone } from '../components/StateBadge';
import { DegradedPanel, UnknownPanel } from '../components/StatePanels';
import { ViewGate } from '../components/ViewGate';
import { useT } from '../i18n/I18nProvider';

type PanelState = {
  phase: 'idle' | 'loading' | 'error';
  detail: EvidenceDetail | null;
  error: string | null;
};

const IDLE_PANEL: PanelState = { phase: 'idle', detail: null, error: null };

export default function ChangesPage({ client = apiClient }: { client?: ApiClient }) {
  const state = useR4View<ChangesView>('changes', client);
  const t = useT();
  const [selected, setSelected] = useState<string | null>(null);
  const [panel, setPanel] = useState<PanelState>(IDLE_PANEL);

  const openEvidence = (item: ChangeItem) => {
    const eventId = item.event_id;
    setSelected(eventId);
    setPanel({ phase: 'loading', detail: null, error: null });
    getEvidence(eventId, client).then(
      (detail) => setPanel({ phase: 'idle', detail, error: null }),
      (err: unknown) => {
        if (err instanceof SessionUnavailableError) {
          setPanel({ phase: 'error', detail: null, error: 'SESSION_UNAVAILABLE' });
          return;
        }
        if (err instanceof ApiRequestError && err.status === 404) {
          // The frozen backend contract binds HTTP 404 on this endpoint to
          // exactly one meaning: the event is NOT_FOUND. The stable
          // reason_code is recovered from the failure body when whitelisted.
          setPanel({
            phase: 'idle',
            detail: {
              schema_version: 'r4-product-evidence-1',
              status: 'NOT_FOUND',
              reason_code: err.reasonCode ?? 'EVIDENCE_EVENT_NOT_FOUND',
              event_id: eventId,
            },
            error: null,
          });
          return;
        }
        setPanel({
          phase: 'error',
          detail: null,
          error: err instanceof ApiRequestError ? err.message : 'API request failed',
        });
      },
    );
  };

  const closePanel = () => {
    setSelected(null);
    setPanel(IDLE_PANEL);
  };

  return (
    <ViewGate state={state} label={t('nav.changes')}>
      {(data) => (
        <ChangesViewBody
          data={data}
          selected={selected}
          panel={panel}
          onOpenEvidence={openEvidence}
          onClosePanel={closePanel}
        />
      )}
    </ViewGate>
  );
}

export function ChangesViewBody({
  data,
  selected = null,
  panel = IDLE_PANEL,
  onOpenEvidence,
  onClosePanel,
}: {
  data: ChangesView;
  selected?: string | null;
  panel?: PanelState;
  onOpenEvidence?: (item: ChangeItem) => void;
  onClosePanel?: () => void;
}) {
  const t = useT();
  const showPanel = selected !== null && (panel.phase !== 'idle' || panel.detail !== null);
  return (
    <div>
      <div className="view-head">
        <StateBadge label={data.status} tone={viewStatusTone(data.status)} />
      </div>

      {data.status === 'EMPTY' && (
        <EmptyState
          title={t('changes.empty.title')}
          detail={t('changes.empty.detail')}
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && (
        <DegradedPanel label={t('nav.changes')} reasonCode={data.reason_code} />
      )}
      {data.status === 'UNKNOWN' && (
        <UnknownPanel label={t('nav.changes')} reasonCode={data.reason_code} />
      )}

      <SectionHeader title={t('changes.section.recent')} />
      {data.items.map((item) => (
        <ChangeCard
          key={item.event_id}
          item={item}
          selected={selected === item.event_id}
          onOpenEvidence={onOpenEvidence}
        />
      ))}

      <EvidenceRefs refs={data.evidence_refs} />

      {showPanel && (
        <div>
          <EvidenceDetailPanel
            detail={panel.detail}
            phase={panel.phase}
            error={panel.error}
            onClose={onClosePanel}
          />
        </div>
      )}
    </div>
  );
}

function ChangeCard({
  item,
  selected,
  onOpenEvidence,
}: {
  item: ChangeItem;
  selected: boolean;
  onOpenEvidence?: (item: ChangeItem) => void;
}) {
  const t = useT();
  return (
    <section className={`card${selected ? ' card-selected' : ''}`}>
      <div className="card-head">
        <span className="card-title">{item.type}</span>
        <span className="card-badges">
          <StateBadge label={item.result} tone="neutral" />
          {onOpenEvidence && (
            <button
              type="button"
              className="btn"
              aria-pressed={selected}
              onClick={() => onOpenEvidence(item)}
            >
              {t('changes.openEvidence')}
            </button>
          )}
        </span>
      </div>
      <KeyValueGrid>
        <KeyValue k={t('changes.col.time')} v={item.timestamp} />
        <KeyValue k={t('changes.col.actor')} v={item.actor} />
        <KeyValue k={t('changes.col.subject')} v={orDash(item.subject)} />
        <KeyValue k="execution_domain_id" v={orDash(item.execution_domain_id)} />
        <KeyValue k="attribution" v={orDash(item.attribution)} />
        <KeyValue k="change_kind" v={orDash(item.change_kind)} />
        <KeyValue k="coverage_before" v={orDash(item.coverage_before)} />
        <KeyValue k="coverage_after" v={orDash(item.coverage_after)} />
        <KeyValue k="recovery_disposition" v={orDash(item.recovery_disposition)} />
        <KeyValue k="workspace_id" v={orDash(item.workspace_id)} />
        {item.policy_summary != null && (
          <KeyValue k={t('changes.field.policy')} v={item.policy_summary} />
        )}
        {item.approval_summary != null && (
          <KeyValue k={t('changes.field.approval')} v={item.approval_summary} />
        )}
        <KeyValue k={t('evidence.field.verification')} v={orDash(item.verification_summary)} />
        <KeyValue k={t('changes.col.checkpoint')} v={orDash(item.checkpoint_id)} />
        <KeyValue k={t('evidence.field.session')} v={orDash(item.supervision_session_id)} />
        <KeyValue k={t('evidence.field.change')} v={orDash(item.change_id)} />
        <KeyValue
          k={t('evidence.field.affectedObjects')}
          v={
            item.affected_objects.length === 0 ? (
              <span className="muted">—</span>
            ) : (
              <span className="chips">
                {item.affected_objects.map((obj) => (
                  <code key={obj} className="chip">
                    {obj}
                  </code>
                ))}
              </span>
            )
          }
        />
        <KeyValue k="reason_code" v={<code>{item.reason_code}</code>} />
      </KeyValueGrid>
      <EvidenceRefs refs={item.evidence_refs} />
    </section>
  );
}
