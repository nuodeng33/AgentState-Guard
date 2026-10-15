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
      {(data) => (
        <div>
          {client && data.capabilities?.create_checkpoint && (
            <CreateCheckpointAction client={client} onChanged={state.reload} />
          )}
          <RecoveryViewBody data={data} actions={{ client, reload: state.reload }} />
        </div>
      )}
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

export function RecoveryViewBody({
  data,
  actions,
}: {
  data: RecoveryView;
  /** Optional mutation seam; page-scope tests render read-only by default. */
  actions?: { client: ApiClient; reload: () => void };
}) {
  const t = useT();
  const levelTone = recoveryLevelTone(data.recovery_level);
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
        <KeyValue k={t('kv.testRestoreStatus')} v={<code>{data.test_restore_status}</code>} />
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
  status: TrustedBaselineStatus;
  baselineId: string | null;
}) {
  const t = useT();
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
        <StateBadge label={status} tone={baselineTone(status)} />
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
  yesNo: (value: boolean) => string;
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
