import { apiClient, type ApiClient } from '../api/client';
import type { SupervisionItem, SupervisionView } from '../api/types';
import { useR4View } from '../api/useR4View';
import { EmptyState } from '../components/EmptyState';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
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
import { isActionable, SupervisionActions } from './SupervisionActions';

export default function SupervisionPage({ client = apiClient }: { client?: ApiClient }) {
  const state = useR4View<SupervisionView>('supervision', client);
  const t = useT();
  return (
    <ViewGate state={state} label={t('nav.supervision')}>
      {(data) => (
        <SupervisionViewBody
          data={data}
          client={client}
          onChanged={state.reload}
          busy={state.refreshing}
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
}: {
  data: SupervisionView;
  client?: ApiClient;
  onChanged?: () => void;
  busy?: boolean;
}) {
  const t = useT();
  const label = t('nav.supervision');
  const yesNo = (value: boolean) => (value ? t('common.yes') : t('common.no'));
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
        />
      ))}

      <EvidenceRefs refs={data.evidence_refs} />

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
}: {
  item: SupervisionItem;
  client?: ApiClient;
  onChanged?: () => void;
  busy?: boolean;
  yesNo: (value: boolean) => string;
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
        <KeyValue k={t('kv.requiresManualApproval')} v={yesNo(item.requires_manual_approval)} />
        <KeyValue k={t('kv.manualApprovalGranted')} v={yesNo(item.manual_approval)} />
        <KeyValue k={t('kv.requiresCheckpoint')} v={yesNo(item.requires_checkpoint)} />
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
      <EvidenceRefs refs={item.evidence_refs} />
    </section>
  );
}

function RecoveryFacts({
  facts,
  yesNo,
}: {
  facts: NonNullable<SupervisionItem['recovery_facts']>;
  yesNo: (value: boolean) => string;
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
        <KeyValue k={t('kv.r1Verified')} v={yesNo(facts.r1_verified === true)} />
        <KeyValue k={t('kv.r2Verified')} v={yesNo(facts.r2_verified === true)} />
        <KeyValue k={t('kv.r3Verified')} v={yesNo(facts.r3_verified === true)} />
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
