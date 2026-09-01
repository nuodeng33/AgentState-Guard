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
import type {
  AgentSupervisionSummary,
  ChangeItem,
  EvidenceDetail,
  SupervisionItem,
  SupervisionView,
} from '../api/types';
import { useR4View } from '../api/useR4View';
import { AnalyzeSection } from '../components/AnalyzeSection';
import { ActivityTimeline } from '../components/ActivityTimeline';
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
import { useI18n, useT } from '../i18n/I18nProvider';
import {
  agentIdentityTitle,
  agentInstanceId,
  agentRoleDisplay,
  productTokenDisplay,
  reasonCodeDisplay,
  workspaceStatusDisplay,
} from '../presentation/productLanguage';
import { ControlledChangePanel } from './ControlledChangePanel';
import { isActionable, SupervisionActions } from './SupervisionActions';

type EvidencePanelState = {
  phase: 'idle' | 'loading' | 'error';
  detail: EvidenceDetail | null;
  error: string | null;
};

const IDLE_PANEL: EvidencePanelState = { phase: 'idle', detail: null, error: null };

export default function SupervisionPage({
  client = apiClient,
  onOpenChanges,
  onOpenRecovery,
}: {
  client?: ApiClient;
  onOpenChanges?: (workspaceId: string, checkpointId: string | null) => void;
  onOpenRecovery?: (workspaceId: string, checkpointId: string | null) => void;
}) {
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
          onOpenChanges={onOpenChanges}
          onOpenRecovery={onOpenRecovery}
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

function agentLifecycleTone(lifecycle: string): BadgeTone {
  switch (lifecycle) {
    case 'UNKNOWN':
      return 'unknown';
    case 'DETECTED':
    case 'OBSERVED':
      return 'info';
    default:
      return 'neutral';
  }
}

function agentSupervisionTone(status: string): BadgeTone {
  switch (status) {
    case 'SUPERVISED':
      return 'ok';
    case 'WORKSPACE_BOUND':
      return 'info';
    case 'OBSERVED_ONLY':
      return 'warn';
    case 'UNKNOWN':
      return 'unknown';
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
  onOpenChanges,
  onOpenRecovery,
  onCloseEvidence,
}: {
  data: SupervisionView;
  client?: ApiClient;
  onChanged?: () => void;
  busy?: boolean;
  selectedEvidence?: string | null;
  evidencePanel?: EvidencePanelState;
  onOpenEvidence?: (eventId: string) => void;
  onOpenChanges?: (workspaceId: string, checkpointId: string | null) => void;
  onOpenRecovery?: (workspaceId: string, checkpointId: string | null) => void;
  onCloseEvidence?: () => void;
}) {
  const t = useT();
  const { locale } = useI18n();
  const label = t('nav.supervision');
  const hasObservedAgents = Boolean(data.observed_agents?.length);
  const yesNo = (value: boolean | null | undefined) =>
    value == null ? '—' : value ? t('common.yes') : t('common.no');
  const showEvidencePanel =
    selectedEvidence !== null && (evidencePanel.phase !== 'idle' || evidencePanel.detail !== null);
  return (
    <div>
      <div className="view-head">
        <StateBadge label={data.status} tone={viewStatusTone(data.status)} />
      </div>

      {data.status === 'EMPTY' && (
        <EmptyState
          title={t('supervision.empty.title')}
          detail={t('supervision.empty.detail')}
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && !hasObservedAgents && (
        <DegradedPanel label={label} reasonCode={data.reason_code} />
      )}
      {data.status === 'DEGRADED' && hasObservedAgents && (
        <div className="panel panel-warn" role="alert">
          <p className="panel-title">
            {locale === 'zh-CN'
              ? '智能体已观测；部分监管关联不可用。'
              : 'Agents observed; some supervision bindings are unavailable.'}
          </p>
          <p className="panel-diagnostics muted">
            reason_code: <code>{data.reason_code}</code>
          </p>
        </div>
      )}
      {data.status === 'UNKNOWN' && <UnknownPanel label={label} reasonCode={data.reason_code} />}

      {data.observed_agents && data.observed_agents.length > 0 && (
        <div>
          <p className="card-sub">{t('supervision.section.agentStatus')}</p>
          {data.observed_agents.map((agent) => (
            <AgentSupervisionCard
              key={`${agent.agent_ref}:${agent.execution_domain_id}:${agent.reason_code}`}
              item={agent}
              yesNo={yesNo}
              selectedEvidence={selectedEvidence}
              onOpenEvidence={onOpenEvidence}
              onOpenChanges={onOpenChanges}
              onOpenRecovery={onOpenRecovery}
            />
          ))}
        </div>
      )}

      <p className="card-sub">{t('supervision.section.sessionHistory')}</p>
      {data.items.length === 0 && <p className="muted">{t('supervision.sessionHistory.empty')}</p>}
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

function AgentSupervisionCard({
  item,
  yesNo,
  selectedEvidence,
  onOpenEvidence,
  onOpenChanges,
  onOpenRecovery,
}: {
  item: AgentSupervisionSummary;
  yesNo: (value: boolean | null | undefined) => string;
  selectedEvidence?: string | null;
  onOpenEvidence?: (eventId: string) => void;
  onOpenChanges?: (workspaceId: string, checkpointId: string | null) => void;
  onOpenRecovery?: (workspaceId: string, checkpointId: string | null) => void;
}) {
  const { locale, t } = useI18n();
  const label = item.instance_label ?? item.detected_identity;
  const instanceId = agentInstanceId(label);
  const checkpointId = item.latest_checkpoint?.checkpoint_id ?? null;
  const activity = item.latest_activity ?? item.latest_verified_activity;
  const verifiedChange = item.latest_verified_change;
  const diagnostics = [
    item.reason_code,
    item.workspace.reason_code,
    item.activity_reason_code,
  ].filter(
    (value): value is string => Boolean(value),
  );
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">
          {agentIdentityTitle(item.instance_label, item.detected_identity)}
        </span>
        <span className="card-badges">
          <StateBadge
            label={productTokenDisplay(item.lifecycle, locale)}
            tone={agentLifecycleTone(item.lifecycle)}
          />
          <StateBadge
            label={productTokenDisplay(item.supervision_status, locale)}
            tone={agentSupervisionTone(item.supervision_status)}
          />
        </span>
      </div>
      <KeyValueGrid>
        <KeyValue k={t('supervision.field.agentRef')} v={<code>{orDash(item.agent_ref)}</code>} />
        {instanceId && (
          <KeyValue k={t('supervision.field.instanceId')} v={<code>{instanceId}</code>} />
        )}
        <KeyValue k={t('kv.role')} v={agentRoleDisplay(item.role, locale)} />
        <KeyValue
          k={t('supervision.field.lifecycle')}
          v={
            <StateBadge
              label={productTokenDisplay(item.lifecycle, locale)}
              tone={agentLifecycleTone(item.lifecycle)}
            />
          }
        />
        <KeyValue k={t('kv.executionDomain')} v={<code>{orDash(item.execution_domain_id)}</code>} />
        <KeyValue
          k={t('kv.workspaceStatus')}
          v={
            <StateBadge
              label={workspaceStatusDisplay(item.workspace.status, locale)}
              tone={item.workspace.status === 'UNKNOWN' ? 'unknown' : 'neutral'}
            />
          }
        />
        <KeyValue
          k={t('supervision.field.workspace')}
          v={<code>{orDash(item.workspace.workspace_id)}</code>}
        />
        <KeyValue
          k={t('supervision.field.supervisionStatus')}
          v={
            <StateBadge
              label={productTokenDisplay(item.supervision_status, locale)}
              tone={agentSupervisionTone(item.supervision_status)}
            />
          }
        />
        <KeyValue
          k={t('supervision.field.currentSession')}
          v={<code>{orDash(item.supervision_session_id)}</code>}
        />
        <KeyValue
          k={t('supervision.field.policy')}
          v={<code>{orDash(item.policy_decision)}</code>}
        />
        <KeyValue k={t('supervision.field.pendingApproval')} v={yesNo(item.pending_approval)} />
        <KeyValue
          k={t('supervision.field.latestCheckpoint')}
          v={<code>{orDash(checkpointId)}</code>}
        />
        <KeyValue k="storage_kind" v={<code>{orDash(item.storage_kind)}</code>} />
        <KeyValue k="protection_state" v={<code>{orDash(item.protection_state)}</code>} />
        <KeyValue k="verification_state" v={<code>{orDash(item.verification_state)}</code>} />
        <KeyValue k="recovery_disposition" v={<code>{orDash(item.recovery_disposition)}</code>} />
        {verifiedChange && (
          <KeyValue
            k={locale === 'zh-CN' ? '最近已验证变更' : 'Latest verified change'}
            v={
              <span>
                <code>{verifiedChange.change_kind ?? verifiedChange.type}</code>{' '}
                {verifiedChange.observed_at ?? verifiedChange.timestamp}
              </span>
            }
          />
        )}
        <KeyValue
          k={t('supervision.field.latestActivity')}
          v={
            activity ? (
              <span>
                <code>{activity.type}</code> {activity.timestamp}
              </span>
            ) : (
              '—'
            )
          }
        />
        <KeyValue
          k={locale === 'zh-CN' ? '活动可观测性' : 'Activity observability'}
          v={
            <StateBadge
              label={productTokenDisplay(item.activity_observability ?? 'UNKNOWN', locale)}
              tone={item.activity_observability === 'OBSERVABLE' ? 'info' : 'unknown'}
            />
          }
        />
        <KeyValue
          k={locale === 'zh-CN' ? '近期活动数' : 'Recent activity count'}
          v={item.recent_activity_count ?? 0}
        />
      </KeyValueGrid>
      {item.recent_verified_activities && item.recent_verified_activities.length > 0 && (
        <ActivityTimeline
          activities={item.recent_verified_activities}
          selectedEvidence={selectedEvidence}
          onOpenEvidence={onOpenEvidence ? (entry) => onOpenEvidence(entry.event_id) : undefined}
        />
      )}
      {item.workspace.workspace_id && (onOpenChanges || onOpenRecovery) && (
        <div className="trace-actions">
          {onOpenChanges && (
            <button
              type="button"
              className="btn"
              onClick={() => onOpenChanges(item.workspace.workspace_id!, checkpointId)}
            >
              {locale === 'zh-CN' ? '查看关联变更' : 'Related changes'}
            </button>
          )}
          {onOpenRecovery && (
            <button
              type="button"
              className="btn"
              onClick={() => onOpenRecovery(item.workspace.workspace_id!, checkpointId)}
            >
              {locale === 'zh-CN' ? '查看恢复链' : 'Recovery trace'}
            </button>
          )}
        </div>
      )}
      <EvidenceRefs
        refs={item.evidence_refs}
        diagnostics={diagnostics.map((code) => reasonCodeDisplay(code, locale))}
      />
    </section>
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
      <ActivityTimeline
        activities={activities}
        selectedEvidence={selectedEvidence}
        onOpenEvidence={onOpenEvidence ? (activity) => onOpenEvidence(activity.event_id) : undefined}
      />
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
