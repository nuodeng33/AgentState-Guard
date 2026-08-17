/**
 * Supervision product surface: Core authority projection + bounded actions.
 *
 * - Read surface: GET /api/v1/supervision items rendered verbatim. The
 *   backend owns status, policy_decision, pending_approval, action_ref and
 *   every session fact; the frontend never re-derives lifecycle or policy.
 * - Actions: one-time Approve Once / Reject using only the server-issued
 *   action_ref (SupervisionActions), and the bounded controlled change flow
 *   (ControlledChangePanel). After every mutation the projection is re-read.
 * - Evidence detail opens only from a verified activity's own event_id.
 */

import { useState } from 'react';

import { apiClient, ApiRequestError, SessionUnavailableError, type ApiClient } from '../api/client';
import { getEvidence } from '../api/product';
import type { ChangeItem, EvidenceDetail, SupervisionItem, SupervisionView } from '../api/types';
import { useR4View } from '../api/useR4View';
import { AnalyzeSection } from '../components/AnalyzeSection';
import { EmptyState } from '../components/EmptyState';
import { EvidenceDetailPanel } from '../components/EvidenceDetailPanel';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { SectionHeader } from '../components/SectionHeader';
import {
  baselineTone,
  policyTone,
  recoveryLevelTone,
  StateBadge,
  viewStatusTone,
  type BadgeTone,
} from '../components/StateBadge';
import { DegradedPanel, UnknownPanel } from '../components/StatePanels';
import { ViewGate } from '../components/ViewGate';
import { useT } from '../i18n/I18nProvider';
import { AgentCard } from './AgentsPage';
import { ControlledChangePanel } from './ControlledChangePanel';
import { isActionable, SupervisionActions } from './SupervisionActions';

type EvidencePanelState = {
  phase: 'idle' | 'loading' | 'error';
  detail: EvidenceDetail | null;
  error: string | null;
};

const IDLE_PANEL: EvidencePanelState = { phase: 'idle', detail: null, error: null };

export default function SupervisionPage({ client = apiClient }: { client?: ApiClient }) {
  const state = useR4View<SupervisionView>('supervision', client);
  const t = useT();
  const [selectedEvidence, setSelectedEvidence] = useState<string | null>(null);
  const [panel, setPanel] = useState<EvidencePanelState>(IDLE_PANEL);

  const openEvidence = (eventId: string) => {
    setSelectedEvidence(eventId);
    setPanel({ phase: 'loading', detail: null, error: null });
    getEvidence(eventId, client).then(
      (detail) => setPanel({ phase: 'idle', detail, error: null }),
      (err: unknown) => {
        if (err instanceof SessionUnavailableError) {
          setPanel({ phase: 'error', detail: null, error: 'SESSION_UNAVAILABLE' });
          return;
        }
        if (err instanceof ApiRequestError && err.status === 404) {
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

  const closeEvidence = () => {
    setSelectedEvidence(null);
    setPanel(IDLE_PANEL);
  };

  return (
    <ViewGate state={state} label={t('nav.supervision')}>
      {(data) => (
        <SupervisionViewBody
          data={data}
          client={client}
          onChanged={state.reload}
          busy={state.refreshing}
          selectedEvidence={selectedEvidence}
          evidencePanel={panel}
          onOpenEvidence={openEvidence}
          onCloseEvidence={closeEvidence}
        />
      )}
    </ViewGate>
  );
}

function sessionStatusTone(status: string): BadgeTone {
  switch (status) {
    case 'APPROVED':
      return 'ok';
    case 'REJECTED':
      return 'bad';
    case 'AWAITING_APPROVAL':
      return 'warn';
    case 'EVALUATED':
      return 'info';
    default:
      return 'neutral';
  }
}

export function SupervisionViewBody({
  data,
  client,
  onChanged,
  busy = false,
  selectedEvidence = null,
  evidencePanel = IDLE_PANEL,
  onOpenEvidence,
  onCloseEvidence,
}: {
  data: SupervisionView;
  client?: ApiClient;
  onChanged?: () => void;
  busy?: boolean;
  selectedEvidence?: string | null;
  evidencePanel?: EvidencePanelState;
  onOpenEvidence?: (eventId: string) => void;
  onCloseEvidence?: () => void;
}) {
  const t = useT();
  const label = t('nav.supervision');
  const yesNo = (value: boolean | null | undefined) =>
    value == null ? '—' : value ? t('common.yes') : t('common.no');
  const showEvidencePanel =
    selectedEvidence !== null && (evidencePanel.phase !== 'idle' || evidencePanel.detail !== null);
  return (
    <div>
      <div className="view-head">
        <StateBadge label={data.status} tone={viewStatusTone(data.status)} />
        <span className="reason">
          reason_code: <code>{data.reason_code}</code>
        </span>
      </div>

      {data.status === 'EMPTY' && (
        <EmptyState
          title={t('supervision.empty.title')}
          detail={t('supervision.empty.detail')}
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && (
        <DegradedPanel label={label} reasonCode={data.reason_code} />
      )}
      {data.status === 'UNKNOWN' && <UnknownPanel label={label} reasonCode={data.reason_code} />}

      {data.items.map((item) => (
        <SupervisionCard
          key={item.supervision_session_id}
          item={item}
          client={client}
          onChanged={onChanged}
          busy={busy}
          yesNo={yesNo}
          selectedEvidence={selectedEvidence}
          onOpenEvidence={onOpenEvidence}
        />
      ))}

      <EvidenceRefs refs={data.evidence_refs} />

      {data.observed_agents && data.observed_agents.length > 0 && (
        <div>
          <p className="card-sub">{t('supervision.section.observedAgents')}</p>
          {data.observed_agents.map((agent) => (
            <AgentCard key={`${agent.detected_identity}:${agent.execution_domain_id}:${agent.reason_code}`} item={agent} />
          ))}
        </div>
      )}

      {showEvidencePanel && (
        <div>
          <EvidenceDetailPanel
            detail={evidencePanel.detail}
            phase={evidencePanel.phase}
            error={evidencePanel.error}
            onClose={onCloseEvidence}
          />
        </div>
      )}

      {client && onChanged && <ControlledChangePanel client={client} onChanged={onChanged} />}

      {client && (
        <div>
          <p className="card-sub">{t('supervision.section.analyze')}</p>
          <AnalyzeSection client={client} />
        </div>
      )}

      <p className="readonly-note">{t('supervision.note')}</p>
    </div>
  );
}

function SupervisionCard({
  item,
  client,
  onChanged,
  busy = false,
  yesNo,
  selectedEvidence,
  onOpenEvidence,
}: {
  item: SupervisionItem;
  client?: ApiClient;
  onChanged?: () => void;
  busy?: boolean;
  yesNo: (value: boolean | null | undefined) => string;
  selectedEvidence?: string | null;
  onOpenEvidence?: (eventId: string) => void;
}) {
  const t = useT();
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{item.supervision_session_id}</span>
        <span className="card-badges">
          <StateBadge label={item.status} tone={sessionStatusTone(item.status)} />
          <StateBadge
            label={item.policy_decision ?? t('supervision.noDecision')}
            tone={policyTone(item.policy_decision)}
          />
        </span>
      </div>
      <KeyValueGrid>
        <KeyValue k={t('supervision.field.pendingApproval')} v={yesNo(item.pending_approval)} />
        <KeyValue k={t('kv.requiresManualApproval')} v={yesNo(item.requires_manual_approval)} />
        <KeyValue k={t('kv.manualApprovalGranted')} v={yesNo(item.manual_approval)} />
        <KeyValue k={t('kv.requiresCheckpoint')} v={yesNo(item.requires_checkpoint)} />
        {item.blocked_or_failed_reason != null && (
          <KeyValue
            k={t('supervision.field.blockedReason')}
            v={<code>{item.blocked_or_failed_reason}</code>}
          />
        )}
        {item.latest_checkpoint != null && item.latest_checkpoint.checkpoint_id != null && (
          <KeyValue
            k={t('supervision.field.latestCheckpoint')}
            v={<code>{item.latest_checkpoint.checkpoint_id}</code>}
          />
        )}
        {item.recent_confirmed_result != null && (
          <KeyValue
            k={t('supervision.field.confirmedResult')}
            v={
              <span>
                <code>{item.recent_confirmed_result.type}</code>{' '}
                <code>{item.recent_confirmed_result.result}</code>
              </span>
            }
          />
        )}
        {item.current_task != null && (
          <KeyValue k="current_task" v={<code>{item.current_task}</code>} />
        )}
        {item.current_phase != null && (
          <KeyValue k="current_phase" v={<code>{item.current_phase}</code>} />
        )}
        {item.current_action != null && (
          <KeyValue k="current_action" v={<code>{item.current_action}</code>} />
        )}
        <KeyValue
          k={t('kv.aiAssessment')}
          v={
            item.ai_assessment ? (
              <span>
                {t('supervision.aiDecision')} <code>{orDash(item.ai_assessment.decision)}</code>,{' '}
                {t('supervision.aiSeverity')} <code>{orDash(item.ai_assessment.severity)}</code>
              </span>
            ) : (
              <span className="muted">{t('supervision.aiAssessmentNone')}</span>
            )
          }
        />
        {item.created_at != null && (
          <KeyValue k={t('supervision.field.createdAt')} v={item.created_at} />
        )}
        {item.updated_at != null && (
          <KeyValue k={t('supervision.field.updatedAt')} v={item.updated_at} />
        )}
        {item.observed_at != null && (
          <KeyValue k={t('kv.observedAt')} v={item.observed_at} />
        )}
      </KeyValueGrid>
      {isActionable(item) && client && onChanged && item.action_ref !== null && (
        <SupervisionActions
          sessionId={item.supervision_session_id}
          actionRef={item.action_ref}
          client={client}
          onChanged={onChanged}
          busy={busy}
        />
      )}
      {item.recovery_facts && <RecoveryFacts facts={item.recovery_facts} yesNo={yesNo} />}
      {item.recent_verified_activities && item.recent_verified_activities.length > 0 && (
        <SessionActivities
          activities={item.recent_verified_activities}
          selectedEvidence={selectedEvidence}
          onOpenEvidence={onOpenEvidence}
        />
      )}
      <EvidenceRefs refs={item.evidence_refs} />
    </section>
  );
}

function SessionActivities({
  activities,
  selectedEvidence,
  onOpenEvidence,
}: {
  activities: ChangeItem[];
  selectedEvidence?: string | null;
  onOpenEvidence?: (eventId: string) => void;
}) {
  const t = useT();
  return (
    <div>
      <p className="card-sub">{t('supervision.section.activity')}</p>
      <ul className="evidence-list supervision-activity-list">
        {activities.map((activity) => (
          <li key={activity.event_id}>
            <code>{activity.timestamp}</code>{' '}
            <code>{activity.type}</code>{' '}
            <code>{activity.result}</code>
            {onOpenEvidence && (
              <button
                type="button"
                className="btn btn-link"
                aria-pressed={selectedEvidence === activity.event_id}
                onClick={() => onOpenEvidence(activity.event_id)}
              >
                {t('changes.openEvidence')}
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RecoveryFacts({
  facts,
  yesNo,
}: {
  facts: NonNullable<SupervisionItem['recovery_facts']>;
  yesNo: (value: boolean | null | undefined) => string;
}) {
  const t = useT();
  return (
    <details className="evidence">
      <summary className="evidence-toggle">{t('supervision.recoveryFacts')}</summary>
      <KeyValueGrid>
        <KeyValue
          k={t('kv.recoveryLevel')}
          v={
            facts.recovery_level ? (
              <StateBadge label={facts.recovery_level} tone={recoveryLevelTone(facts.recovery_level)} />
            ) : (
              <span className="muted">—</span>
            )
          }
        />
        <KeyValue k={t('kv.r1Verified')} v={yesNo(facts.r1_verified)} />
        <KeyValue k={t('kv.r2Verified')} v={yesNo(facts.r2_verified)} />
        <KeyValue k={t('kv.r3Verified')} v={yesNo(facts.r3_verified)} />
        <KeyValue k={t('kv.testRestore')} v={orDash(facts.test_restore_status)} />
        <KeyValue
          k={t('kv.trustedBaseline')}
          v={
            facts.trusted_baseline_status ? (
              <StateBadge
                label={facts.trusted_baseline_status}
                tone={baselineTone(facts.trusted_baseline_status)}
              />
            ) : (
              <span className="muted">—</span>
            )
          }
        />
        <KeyValue k="reason_code" v={facts.reason_code ? <code>{facts.reason_code}</code> : <span className="muted">—</span>} />
      </KeyValueGrid>
    </details>
  );
}
