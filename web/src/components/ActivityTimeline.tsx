import type { ChangeItem } from '../api/types';
import { useI18n } from '../i18n/I18nProvider';
import { EvidenceRefs } from './EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from './KeyValue';
import { StateBadge } from './StateBadge';

type BucketKey = 'recent' | 'today' | 'yesterday' | 'earlier' | 'clockSkew';

const PROCESS_TYPES = new Set([
  'PROCESS_STARTED',
  'PROCESS_EXITED',
  'SANDBOX_PROCESS_STARTED',
  'SANDBOX_PROCESS_EXITED',
  'SANDBOX_NETWORK_ACTIVITY',
]);

function activityTime(item: ChangeItem): Date | null {
  const raw = item.observed_at ?? item.timestamp;
  const value = new Date(raw);
  return Number.isNaN(value.getTime()) ? null : value;
}

function startOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

function bucketFor(item: ChangeItem, now: Date): BucketKey {
  const value = activityTime(item);
  if (value === null) return 'earlier';
  const delta = now.getTime() - value.getTime();
  if (delta < -5 * 60 * 1000) return 'clockSkew';
  if (delta >= 0 && delta <= 60 * 60 * 1000) return 'recent';
  const today = startOfDay(now).getTime();
  const observedDay = startOfDay(value).getTime();
  if (observedDay === today) return 'today';
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1).getTime();
  if (observedDay === yesterday) return 'yesterday';
  return 'earlier';
}

function isProcessActivity(item: ChangeItem): boolean {
  return item.category === 'PROCESS_ACTIVITY' || PROCESS_TYPES.has(item.type);
}

function sourceFor(item: ChangeItem): string {
  return item.source ?? item.actor ?? 'UNKNOWN';
}

function displayTime(item: ChangeItem): string {
  const value = activityTime(item);
  return value === null ? item.observed_at ?? item.timestamp : value.toLocaleString();
}

export function ActivityTimeline({
  activities,
  mode = 'activity',
  selectedEvidence = null,
  onOpenEvidence,
  onOpenRecovery,
}: {
  activities: ChangeItem[];
  mode?: 'changes' | 'activity';
  selectedEvidence?: string | null;
  onOpenEvidence?: (item: ChangeItem) => void;
  onOpenRecovery?: (checkpointId: string, workspaceId: string | null) => void;
}) {
  const { locale } = useI18n();
  const now = new Date();
  const labels: Record<BucketKey, string> = locale === 'zh-CN'
    ? { recent: '最近 1 小时', today: '今天', yesterday: '昨天', earlier: '更早', clockSkew: '时钟偏差' }
    : { recent: 'Recent 1 hour', today: 'Today', yesterday: 'Yesterday', earlier: 'Earlier', clockSkew: 'Clock skew' };
  const order: BucketKey[] = ['clockSkew', 'recent', 'today', 'yesterday', 'earlier'];
  const visible = activities
    .filter((item) => mode !== 'changes' || !isProcessActivity(item))
    .slice()
    .sort((a, b) => (activityTime(b)?.getTime() ?? 0) - (activityTime(a)?.getTime() ?? 0));

  return (
    <div className="activity-timeline">
      {order.map((bucket) => {
        const bucketItems = visible.filter((item) => bucketFor(item, now) === bucket);
        if (bucketItems.length === 0) return null;
        const groups = new Map<string, ChangeItem[]>();
        for (const item of bucketItems) {
          const source = sourceFor(item);
          groups.set(source, [...(groups.get(source) ?? []), item]);
        }
        return (
          <section className="activity-bucket" key={bucket}>
            <h3 className="activity-bucket-title">{labels[bucket]}</h3>
            {[...groups.entries()].map(([source, items]) => (
              <details className="activity-group" key={source} open>
                <summary>
                  {source} · {items.length} {locale === 'zh-CN' ? '项' : items.length === 1 ? 'item' : 'items'}
                </summary>
                <div className="activity-rows">
                  {items.map((item) => (
                    <article className="activity-row" key={item.event_id}>
                      <div className="activity-row-main">
                        <code>{item.type}</code>
                        <StateBadge label={item.result} tone="neutral" />
                        <time dateTime={item.observed_at ?? item.timestamp}>{displayTime(item)}</time>
                        {onOpenEvidence && (
                          <button
                            type="button"
                            className="btn btn-link"
                            aria-pressed={selectedEvidence === item.event_id}
                            onClick={() => onOpenEvidence(item)}
                          >
                            {locale === 'zh-CN' ? '证据' : 'Evidence'}
                          </button>
                        )}
                        {onOpenRecovery && item.checkpoint_id && (
                          <button
                            type="button"
                            className="btn btn-link"
                            onClick={() => onOpenRecovery(item.checkpoint_id!, item.workspace_id)}
                          >
                            {locale === 'zh-CN' ? '恢复链' : 'Recovery trace'}
                          </button>
                        )}
                      </div>
                      <details className="activity-detail">
                        <summary>{locale === 'zh-CN' ? '详情' : 'Details'}</summary>
                        <KeyValueGrid>
                          <KeyValue k="source" v={sourceFor(item)} />
                          <KeyValue
                            k="actor"
                            v={item.actor_attribution === 'UNATTRIBUTED' ? 'UNKNOWN' : orDash(item.actor)}
                          />
                          <KeyValue k="actor_attribution" v={orDash(item.actor_attribution ?? item.attribution)} />
                          <KeyValue k="subject" v={orDash(item.subject)} />
                          <KeyValue k="execution_domain_id" v={orDash(item.execution_domain_id)} />
                          <KeyValue k="workspace_id" v={orDash(item.workspace_id)} />
                          <KeyValue k="agent_ref" v={orDash(item.agent_ref)} />
                          <KeyValue k="checkpoint_id" v={orDash(item.checkpoint_id)} />
                          <KeyValue k="change_id" v={orDash(item.change_id)} />
                          <KeyValue k="change_kind" v={orDash(item.change_kind)} />
                          <KeyValue k="coverage_before" v={orDash(item.coverage_before)} />
                          <KeyValue k="coverage_after" v={orDash(item.coverage_after)} />
                          <KeyValue k="storage_kind" v={orDash(item.storage_kind)} />
                          <KeyValue k="protection_state" v={orDash(item.protection_state)} />
                          <KeyValue k="verification_state" v={orDash(item.verification_state ?? item.verification_summary)} />
                          <KeyValue k="recovery_disposition" v={orDash(item.recovery_disposition)} />
                          <KeyValue k="supervision_session_id" v={orDash(item.supervision_session_id)} />
                          {item.policy_summary != null && <KeyValue k="policy_summary" v={item.policy_summary} />}
                          {item.approval_summary != null && <KeyValue k="approval_summary" v={item.approval_summary} />}
                          <KeyValue
                            k="affected_objects"
                            v={item.affected_objects.length ? item.affected_objects.join(', ') : '—'}
                          />
                          <KeyValue k="reason_code" v={<code>{item.reason_code}</code>} />
                          <KeyValue k="recorded_at" v={item.recorded_at} />
                        </KeyValueGrid>
                        <EvidenceRefs refs={item.evidence_refs} />
                      </details>
                    </article>
                  ))}
                </div>
              </details>
            ))}
          </section>
        );
      })}
    </div>
  );
}
