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
import { SectionHeader } from '../components/SectionHeader';
import {
  baselineTone,
  recoveryCoverageTone,
  recoveryLevelTone,
  StateBadge,
  viewStatusTone,
} from '../components/StateBadge';
import { ViewGate } from '../components/ViewGate';
import { useT } from '../i18n/I18nProvider';
import { CreateCheckpointAction, RecoveryItemActions } from './RecoveryActions';

export default function RecoveryPage({ client = apiClient }: { client?: ApiClient }) {
  const state = useR4View<RecoveryView>('recovery', client);
  const t = useT();
  return (
    <ViewGate state={state} label={t('nav.recovery')}>
      {(data) => <RecoveryViewBody data={data} actions={{ client, reload: state.reload }} />}
    </ViewGate>
  );
}

const CHAIN_LEVELS: RecoveryLevel[] = ['R0', 'R1', 'R2', 'R3'];

function verifiedFor(data: RecoveryView, level: RecoveryLevel): boolean | null {
  switch (level) {
    case 'R1':
      return data.r1_verified ?? null;
    case 'R2':
      return data.r2_verified ?? null;
    case 'R3':
      return data.r3_verified ?? null;
    default:
      return null; // R0 is the absence of verified recovery; nothing to claim.
  }
}

function percent(ratio: number | null): string {
  return ratio === null ? '—' : `${(ratio * 100).toFixed(1)}%`;
}
function factNumber(value: number | null | undefined) {
  return value == null ? <span className="muted">—</span> : String(value);
}

function CoverageFacts({
  coverage,
  yesNo,
}: {
  coverage: RecoveryView['coverage'];
  yesNo: (value: boolean | null | undefined) => string;
}) {
  if (coverage == null) {
    return <KeyValue k="coverage" v={<span className="muted">—</span>} />;
  }
  return (
    <>
      <KeyValue k="coverage.restorable" v={factNumber(coverage.counts?.restorable)} />
      <KeyValue k="coverage.audit_only" v={factNumber(coverage.counts?.audit_only)} />
      <KeyValue k="coverage.excluded" v={factNumber(coverage.counts?.excluded)} />
      <KeyValue k="coverage.unreachable" v={factNumber(coverage.counts?.unreachable)} />
      <KeyValue k="coverage.scan_complete" v={yesNo(coverage.scan_complete)} />
      <KeyValue k="coverage.scan_reason_code" v={orDash(coverage.scan_reason_code)} />
      {Object.entries(coverage.reason_counts).map(([reason, count]) => (
        <KeyValue key={reason} k={reason} v={String(count)} />
      ))}
    </>
  );
}

function RecoveryScopeSummary({
  data,
  yesNo,
}: {
  data: RecoveryView;
  yesNo: (value: boolean | null | undefined) => string;
}) {
  const t = useT();
  const limitations = data.limitations ?? [];
  return (
    <>
      <SectionHeader title={t('recovery.section.scope')} />
      <section className="card">
        <KeyValueGrid>
          <KeyValue k="scope_kind" v={orDash(data.scope_kind)} />
          <KeyValue k="workspace_id" v={orDash(data.workspace_id)} />
          <KeyValue k="actual_restore_status" v={orDash(data.actual_restore_status)} />
          <KeyValue k="recovery_verified" v={yesNo(data.recovery_verified)} />
          <KeyValue k="verified_at" v={orDash(data.verified_at)} />
          <KeyValue k="capabilities.create_checkpoint" v={yesNo(data.capabilities?.create_checkpoint)} />
          <KeyValue k="capabilities.test_restore" v={yesNo(data.capabilities?.test_restore)} />
          <KeyValue k="capabilities.restore" v={yesNo(data.capabilities?.restore)} />
          <CoverageFacts coverage={data.coverage} yesNo={yesNo} />
        </KeyValueGrid>
        <p className="card-sub">limitations</p>
        {limitations.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          <ul className="evidence-list">
            {limitations.map((limitation) => (
              <li key={limitation}>
                <code>{limitation}</code>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}



export function RecoveryViewBody({
  data,
  actions,
}: {
  data: RecoveryView;
  /** Optional mutation seam; page-scope tests render read-only by default. */
  actions?: { client: ApiClient; reload: () => void };
}) {
  const t = useT();
  const levelTone = recoveryLevelTone(data.recovery_level ?? 'UNKNOWN');
  const yesNo = (value: boolean | null | undefined) =>
    value == null ? '—' : value ? t('common.yes') : t('common.no');

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
          title={t('recovery.empty.title')}
          detail={t('recovery.empty.detail')}
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && (
        <div className="panel panel-bad" role="alert">
          <p className="panel-title">{t('recovery.degraded.title')}</p>
          <p>{t('recovery.degraded.body', { reasonCode: data.reason_code })}</p>
        </div>
      )}
      {data.status === 'UNKNOWN' && (
        <div className="panel panel-warn" role="alert">
          <p className="panel-title">{t('recovery.unknown.title')}</p>
          <p>{t('recovery.unknown.body', { reasonCode: data.reason_code })}</p>
        </div>
      )}
      {data.status === 'AVAILABLE' && data.recovery_level === 'R0' && (
        <div className="panel panel-bad" role="alert">
          <p className="panel-title">{t('recovery.r0.title')}</p>
          <p>{t('recovery.r0.body')}</p>
        </div>
      )}

      <RecoveryScopeSummary data={data} yesNo={yesNo} />
      {actions && data.capabilities?.create_checkpoint === true && (
        <CreateCheckpointAction client={actions.client} onChanged={actions.reload} />
      )}

      <SectionHeader title={t('recovery.section.level')} />
      <div className="recovery-chain" aria-label={t('recovery.chain.ariaLabel')}>
        {CHAIN_LEVELS.map((level) => {
          const isCurrent = level === data.recovery_level;
          const verified = verifiedFor(data, level);
          const stateText =
            verified === null
              ? isCurrent
                ? t('recovery.chain.current')
                : '—'
              : `${verified ? t('recovery.chain.verified') : t('recovery.chain.notVerified')}${isCurrent ? t('recovery.chain.currentSuffix') : ''}`;
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
        <KeyValue k={t('kv.testRestoreStatus')} v={orDash(data.test_restore_status)} />
      </KeyValueGrid>

      <SectionHeader title={t('recovery.section.baseline')} />
      <TrustedBaselinePanel
        status={data.trusted_baseline_status}
        baselineId={data.trusted_baseline_id}
      />

      {data.items.length > 0 && <SectionHeader title={t('recovery.section.checkpoints')} />}
      {data.items.map((item) => (
        <RecoveryCard key={item.checkpoint_id} item={item} yesNo={yesNo} actions={actions} />
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
  status: TrustedBaselineStatus | null | undefined;
  baselineId: string | null | undefined;
}) {
  const t = useT();
  const displayStatus = status ?? 'UNKNOWN';
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
        <span className="card-title">{t('recovery.section.baseline')}</span>
        <StateBadge label={displayStatus} tone={baselineTone(displayStatus)} />
      </div>
      <KeyValueGrid>
        <KeyValue k={t('kv.baselineId')} v={orDash(baselineId)} />
      </KeyValueGrid>
      <p className="baseline-note">{t('recovery.baseline.note')}</p>
    </section>
  );
}

function RecoveryCard({
  item,
  yesNo,
  actions,
}: {
  item: RecoveryItem;
  yesNo: (value: boolean | null | undefined) => string;
  actions?: { client: ApiClient; reload: () => void };
}) {
  const t = useT();
  const itemFailClosed =
    item.recovery_level === 'R0' ||
    item.status === 'EVIDENCE_INSUFFICIENT' ||
    item.status === 'UNREACHABLE' ||
    item.status === 'MISSING';
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{t('recovery.card.title', { id: item.checkpoint_id })}</span>
        <span className="card-badges">
          <StateBadge label={item.status} tone={recoveryCoverageTone(item.status)} />
          <StateBadge label={item.recovery_level} tone={recoveryLevelTone(item.recovery_level)} />
        </span>
      </div>
      {itemFailClosed && (
        <div className="panel panel-bad" role="alert">
          <p>{t('recovery.card.failClosed', { reasonCode: item.reason_code })}</p>
        </div>
      )}
      <KeyValueGrid>
        <KeyValue k={t('kv.executionDomain')} v={orDash(item.execution_domain_id)} />
        <KeyValue k="scope_kind" v={<code>{item.scope_kind}</code>} />
        <KeyValue k="workspace_id" v={orDash(item.workspace_id)} />
        <KeyValue k="actual_restore_status" v={<code>{item.actual_restore_status}</code>} />
        <KeyValue k="actual_restore_verified_at" v={orDash(item.actual_restore_verified_at)} />
        <KeyValue k="created_at" v={orDash(item.created_at)} />
        <KeyValue k="manifest_integrity" v={orDash(item.manifest_integrity)} />
        <CoverageFacts coverage={item.coverage} yesNo={yesNo} />
        <KeyValue k={t('kv.r1Verified')} v={yesNo(item.r1_verified)} />
        <KeyValue k={t('kv.r2Verified')} v={yesNo(item.r2_verified)} />
        <KeyValue k={t('kv.r3Verified')} v={yesNo(item.r3_verified)} />
        <KeyValue k={t('kv.requestedTargets')} v={String(item.requested_targets)} />
        <KeyValue
          k={t('kv.authorizedSnapshotTargets')}
          v={`${item.authorized_snapshot_targets} (${percent(item.authorized_snapshot_coverage)})`}
        />
        <KeyValue
          k={t('kv.intactManifestBlobs')}
          v={`${item.intact_manifest_blob_targets} (${percent(item.manifest_blob_coverage)})`}
        />
        <KeyValue k={t('kv.testRestoreStatus')} v={<code>{item.test_restore_status}</code>} />
        <KeyValue
          k={t('kv.testRestoreVerifiedTargets')}
          v={item.test_restore_verified_targets === null ? '—' : String(item.test_restore_verified_targets)}
        />
        <KeyValue
          k={t('kv.trustedBaseline')}
          v={<StateBadge label={item.trusted_baseline_status} tone={baselineTone(item.trusted_baseline_status)} />}
        />
        <KeyValue k={t('kv.trustedBaselineId')} v={orDash(item.trusted_baseline_id)} />
        <KeyValue k="reason_code" v={<code>{item.reason_code}</code>} />
        <KeyValue
          k="actual_restore_evidence_refs"
          v={
            item.actual_restore_evidence_refs.length === 0 ? (
              <span className="muted">—</span>
            ) : (
              <span className="chips">
                {item.actual_restore_evidence_refs.map((ref) => (
                  <code key={ref} className="chip">
                    {ref}
                  </code>
                ))}
              </span>
            )
          }
        />
      </KeyValueGrid>
      {actions && (
        <RecoveryItemActions
          checkpointId={item.checkpoint_id}
          client={actions.client}
          onChanged={actions.reload}
          disabled={itemFailClosed}
        />
      )}
      <EvidenceRefs refs={item.evidence_refs} />
    </section>
  );
}
