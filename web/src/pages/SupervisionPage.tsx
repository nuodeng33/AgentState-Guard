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
import { ViewGate } from '../components/ViewGate';

export default function SupervisionPage({ client = apiClient }: { client?: ApiClient }) {
  const state = useR4View<SupervisionView>('supervision', client);
  return (
    <ViewGate state={state} label="Supervision">
      {(data) => <SupervisionViewBody data={data} />}
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

function yesNo(value: boolean): string {
  return value ? 'Yes' : 'No';
}

export function SupervisionViewBody({ data }: { data: SupervisionView }) {
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
          title="No supervision sessions"
          detail="The backend holds no supervision session records."
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && (
        <div className="panel panel-bad" role="alert">
          <p className="panel-title">Supervision view degraded</p>
          <p>
            The backend could not project authoritative supervision state (
            <code>{data.reason_code}</code>). Nothing below is projected authority.
          </p>
        </div>
      )}
      {data.status === 'UNKNOWN' && (
        <div className="panel panel-warn" role="alert">
          <p className="panel-title">Supervision state unknown</p>
          <p>
            The backend reports this view as UNKNOWN (<code>{data.reason_code}</code>). Do not
            treat it as healthy.
          </p>
        </div>
      )}

      {data.items.map((item) => (
        <SupervisionCard key={item.supervision_session_id} item={item} />
      ))}

      <EvidenceRefs refs={data.evidence_refs} />

      <p className="readonly-note">
        Read-only view. Approve Once / Reject are not available in this build: the frozen P8
        backend contract exposes no mutation endpoint.
      </p>
    </div>
  );
}

function SupervisionCard({ item }: { item: SupervisionItem }) {
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{item.supervision_session_id}</span>
        <span className="card-badges">
          <StateBadge label={item.status} tone={sessionStatusTone(item.status)} />
          <StateBadge
            label={item.policy_decision ?? 'NO DECISION'}
            tone={policyTone(item.policy_decision)}
          />
        </span>
      </div>
      <KeyValueGrid>
        <KeyValue k="Requires manual approval" v={yesNo(item.requires_manual_approval)} />
        <KeyValue k="Manual approval granted" v={yesNo(item.manual_approval)} />
        <KeyValue k="Requires checkpoint" v={yesNo(item.requires_checkpoint)} />
        <KeyValue
          k="AI assessment"
          v={
            item.ai_assessment ? (
              <span>
                decision <code>{orDash(item.ai_assessment.decision)}</code>, severity{' '}
                <code>{orDash(item.ai_assessment.severity)}</code>
              </span>
            ) : (
              <span className="muted">No AI assessment</span>
            )
          }
        />
      </KeyValueGrid>
      {item.recovery_facts && <RecoveryFacts facts={item.recovery_facts} />}
      <EvidenceRefs refs={item.evidence_refs} />
    </section>
  );
}

function RecoveryFacts({ facts }: { facts: NonNullable<SupervisionItem['recovery_facts']> }) {
  return (
    <details className="evidence">
      <summary className="evidence-toggle">Authoritative recovery facts</summary>
      <KeyValueGrid>
        <KeyValue
          k="Recovery level"
          v={
            facts.recovery_level ? (
              <StateBadge label={facts.recovery_level} tone={recoveryLevelTone(facts.recovery_level)} />
            ) : (
              <span className="muted">—</span>
            )
          }
        />
        <KeyValue k="R1 verified" v={yesNo(facts.r1_verified === true)} />
        <KeyValue k="R2 verified" v={yesNo(facts.r2_verified === true)} />
        <KeyValue k="R3 verified" v={yesNo(facts.r3_verified === true)} />
        <KeyValue k="Test restore" v={orDash(facts.test_restore_status)} />
        <KeyValue
          k="Trusted baseline"
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
