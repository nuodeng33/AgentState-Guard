import { apiClient, type ApiClient } from '../api/client';
import type {
  RecoveryItem,
  RecoveryLevel,
  RecoveryView,
  TrustedBaselineStatus,
} from '../api/types';
import { useR4View } from '../api/useR4View';
import { EmptyState } from '../components/EmptyState';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import {
  baselineTone,
  recoveryCoverageTone,
  recoveryLevelTone,
  StateBadge,
  viewStatusTone,
} from '../components/StateBadge';
import { ViewGate } from '../components/ViewGate';

export default function RecoveryPage({ client = apiClient }: { client?: ApiClient }) {
  const state = useR4View<RecoveryView>('recovery', client);
  return (
    <ViewGate state={state} label="Recovery">
      {(data) => <RecoveryViewBody data={data} />}
    </ViewGate>
  );
}

const CHAIN_LEVELS: RecoveryLevel[] = ['R0', 'R1', 'R2', 'R3'];

function verifiedFor(data: RecoveryView, level: RecoveryLevel): boolean | null {
  switch (level) {
    case 'R1':
      return data.r1_verified;
    case 'R2':
      return data.r2_verified;
    case 'R3':
      return data.r3_verified;
    default:
      return null; // R0 is the absence of verified recovery; nothing to claim.
  }
}

function percent(ratio: number | null): string {
  return ratio === null ? '—' : `${(ratio * 100).toFixed(1)}%`;
}

export function RecoveryViewBody({ data }: { data: RecoveryView }) {
  const levelTone = recoveryLevelTone(data.recovery_level);

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
          title="No recovery checkpoints"
          detail="The backend holds no checkpoint records. Recovery level is R0."
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && (
        <div className="panel panel-bad" role="alert">
          <p className="panel-title">Recovery view degraded — fail closed</p>
          <p>
            The backend could not project authoritative recovery state (
            <code>{data.reason_code}</code>). Recoverability cannot be confirmed.
          </p>
        </div>
      )}
      {data.status === 'UNKNOWN' && (
        <div className="panel panel-warn" role="alert">
          <p className="panel-title">Recovery state unknown — fail closed</p>
          <p>
            The backend reports this view as UNKNOWN (<code>{data.reason_code}</code>).
            Recoverability cannot be confirmed.
          </p>
        </div>
      )}
      {data.status === 'AVAILABLE' && data.recovery_level === 'R0' && (
        <div className="panel panel-bad" role="alert">
          <p className="panel-title">No verified recovery (R0) — fail closed</p>
          <p>
            No checkpoint currently meets a verified recovery level. Treat restore capability as
            unavailable.
          </p>
        </div>
      )}

      <h2 className="section-title">Recovery level</h2>
      <div className="recovery-chain" aria-label="Recovery level chain R0 to R3">
        {CHAIN_LEVELS.map((level) => {
          const isCurrent = level === data.recovery_level;
          const verified = verifiedFor(data, level);
          const stateText =
            verified === null
              ? isCurrent
                ? 'current level'
                : '—'
              : `${verified ? 'verified' : 'not verified'}${isCurrent ? ' · current' : ''}`;
          return (
            <div
              key={level}
              className={[
                'chain-step',
                isCurrent ? `is-current tone-${levelTone}` : '',
                verified === true ? 'is-verified' : '',
              ]
                .filter(Boolean)
                .join(' ')}
            >
              <div className="chain-step-level">{level}</div>
              <div className="chain-step-state">{stateText}</div>
            </div>
          );
        })}
      </div>
      <KeyValueGrid>
        <KeyValue k="Test restore status" v={<code>{data.test_restore_status}</code>} />
      </KeyValueGrid>

      <h2 className="section-title">Trusted Baseline</h2>
      <TrustedBaselinePanel
        status={data.trusted_baseline_status}
        baselineId={data.trusted_baseline_id}
      />

      {data.items.length > 0 && <h2 className="section-title">Checkpoints</h2>}
      {data.items.map((item) => (
        <RecoveryCard key={item.checkpoint_id} item={item} />
      ))}

      <EvidenceRefs refs={data.evidence_refs} />
    </div>
  );
}

/**
 * Trusted Baseline is an independent trust state, rendered as its own panel.
 * A recovery level of R3 never implies TRUSTED.
 */
function TrustedBaselinePanel({
  status,
  baselineId,
}: {
  status: TrustedBaselineStatus;
  baselineId: string | null;
}) {
  const panelClass =
    status === 'TRUSTED'
      ? 'is-trusted'
      : status === 'RETIRED'
        ? 'is-retired'
        : status === 'REVOKED'
          ? 'is-revoked'
          : '';
  return (
    <section className={`baseline-panel ${panelClass}`}>
      <div className="card-head">
        <span className="card-title">Trusted Baseline</span>
        <StateBadge label={status} tone={baselineTone(status)} />
      </div>
      <KeyValueGrid>
        <KeyValue k="Baseline ID" v={orDash(baselineId)} />
      </KeyValueGrid>
      <p className="baseline-note">
        Trusted Baseline is an independent trust state. Recovery level R3 does not imply TRUSTED.
      </p>
    </section>
  );
}

function RecoveryCard({ item }: { item: RecoveryItem }) {
  const itemFailClosed =
    item.recovery_level === 'R0' ||
    item.status === 'EVIDENCE_INSUFFICIENT' ||
    item.status === 'UNREACHABLE' ||
    item.status === 'MISSING';
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">checkpoint {item.checkpoint_id}</span>
        <span className="card-badges">
          <StateBadge label={item.status} tone={recoveryCoverageTone(item.status)} />
          <StateBadge label={item.recovery_level} tone={recoveryLevelTone(item.recovery_level)} />
        </span>
      </div>
      {itemFailClosed && (
        <div className="panel panel-bad" role="alert">
          <p>
            Fail closed: this checkpoint has no verified recovery level (
            <code>{item.reason_code}</code>). Do not treat it as restorable.
          </p>
        </div>
      )}
      <KeyValueGrid>
        <KeyValue k="Execution domain" v={orDash(item.execution_domain_id)} />
        <KeyValue k="R1 verified" v={item.r1_verified ? 'Yes' : 'No'} />
        <KeyValue k="R2 verified" v={item.r2_verified ? 'Yes' : 'No'} />
        <KeyValue k="R3 verified" v={item.r3_verified ? 'Yes' : 'No'} />
        <KeyValue k="Requested targets" v={String(item.requested_targets)} />
        <KeyValue
          k="Authorized snapshot targets"
          v={`${item.authorized_snapshot_targets} (${percent(item.authorized_snapshot_coverage)})`}
        />
        <KeyValue
          k="Intact manifest blobs"
          v={`${item.intact_manifest_blob_targets} (${percent(item.manifest_blob_coverage)})`}
        />
        <KeyValue k="Test restore status" v={<code>{item.test_restore_status}</code>} />
        <KeyValue
          k="Test-restore verified targets"
          v={item.test_restore_verified_targets === null ? '—' : String(item.test_restore_verified_targets)}
        />
        <KeyValue
          k="Trusted baseline"
          v={<StateBadge label={item.trusted_baseline_status} tone={baselineTone(item.trusted_baseline_status)} />}
        />
        <KeyValue k="Trusted baseline ID" v={orDash(item.trusted_baseline_id)} />
        <KeyValue k="reason_code" v={<code>{item.reason_code}</code>} />
      </KeyValueGrid>
      <EvidenceRefs refs={item.evidence_refs} />
    </section>
  );
}
