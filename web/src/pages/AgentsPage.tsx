import type { AgentItem, AgentsView } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { StateBadge, viewStatusTone, type BadgeTone } from '../components/StateBadge';
import { DegradedPanel, UnknownPanel } from '../components/StatePanels';
import { useI18n } from '../i18n/I18nProvider';
import {
  agentIdentityTitle,
  agentInstanceId,
  agentRoleDisplay,
  productTokenDisplay,
  reasonCodeDisplay,
  workspaceStatusDisplay,
} from '../presentation/productLanguage';

/**
 * Lifecycle is rendered verbatim from the backend. The UI never upgrades a
 * detected identity (Claude Code, Codex, Kimi Code, …) to RUNNING/INTEGRATED/
 * ENFORCED on its own; UNKNOWN stays UNKNOWN. Card titles use the bounded
 * backend instance label (or detected identity fallback) translated to
 * product language; several same-product instances stay distinguishable.
 */
function lifecycleTone(lifecycle: string): BadgeTone {
  switch (lifecycle) {
    case 'DETECTED':
      return 'info';
    case 'UNKNOWN':
      return 'unknown';
    default:
      return 'neutral';
  }
}

export function AgentsViewBody({ data }: { data: AgentsView }) {
  const { locale, t } = useI18n();
  const label = t('nav.agents');
  return (
    <div>
      <div className="view-head">
        <StateBadge
          label={productTokenDisplay(data.status, locale)}
          tone={viewStatusTone(data.status)}
        />
      </div>

      {data.status === 'EMPTY' && (
        <EmptyState
          title={t('agents.empty.title')}
          detail={t('agents.empty.detail')}
          reasonCode={reasonCodeDisplay(data.reason_code, locale)}
        />
      )}
      {data.status === 'DEGRADED' && data.items.length === 0 && (
        <DegradedPanel label={label} reasonCode={data.reason_code} />
      )}
      {data.status === 'DEGRADED' && data.items.length > 0 && (
        <div className="panel panel-warn" role="alert">
          <p className="panel-title">
            {locale === 'zh-CN'
              ? `已识别 ${data.identified_count ?? data.items.length} 个智能体`
              : `Identified ${data.identified_count ?? data.items.length} Agent${(data.identified_count ?? data.items.length) === 1 ? '' : 's'}`}
          </p>
          <p className="panel-body">
            {locale === 'zh-CN' ? '部分运行域不可观测：' : 'Partially unobservable domains: '}
            {(data.degradation_scopes ?? [])
              .map((scope) => scope.label ?? scope.execution_domain_id ?? scope.reason_code)
              .join(', ') || data.reason_code}
          </p>
          <p className="panel-diagnostics muted">
            reason_code: <code>{data.reason_code}</code>
          </p>
        </div>
      )}
      {data.status === 'UNKNOWN' && <UnknownPanel label={label} reasonCode={data.reason_code} />}

      {data.items.map((item) => (
        <AgentCard
          key={`${item.execution_domain_id}:${item.detected_identity}:${item.evidence_refs[0]}`}
          item={item}
        />
      ))}

      <EvidenceRefs refs={data.evidence_refs} />
    </div>
  );
}

export function AgentCard({ item }: { item: AgentItem }) {
  const { locale, t } = useI18n();
  const label = item.instance_label ?? item.detected_identity;
  const instanceId = agentInstanceId(label);
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">
          {agentIdentityTitle(item.instance_label, item.detected_identity)}
        </span>
        <span className="card-badges">
          <StateBadge
            label={productTokenDisplay(item.lifecycle, locale)}
            tone={lifecycleTone(item.lifecycle)}
          />
          {item.uncertainty && <StateBadge label={t('runtime.uncertain')} tone="warn" />}
        </span>
      </div>
      <KeyValueGrid>
        {instanceId && (
          <KeyValue k={locale === 'zh-CN' ? '实例编号' : 'Instance ID'} v={<code>{instanceId}</code>} />
        )}
        <KeyValue k={t('kv.role')} v={agentRoleDisplay(item.role, locale)} />
        <KeyValue k={t('kv.confidence')} v={item.confidence.toFixed(2)} />
        <KeyValue k={t('kv.executionDomain')} v={orDash(item.execution_domain_id)} />
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
          k={locale === 'zh-CN' ? '活动可观测性' : 'Activity observability'}
          v={
            <StateBadge
              label={productTokenDisplay(item.activity_observability ?? 'UNKNOWN', locale)}
              tone={item.activity_observability === 'OBSERVABLE' ? 'info' : 'unknown'}
            />
          }
        />
        <KeyValue
          k={locale === 'zh-CN' ? '最近活动' : 'Latest activity'}
          v={
            item.latest_activity ? (
              <span><code>{item.latest_activity.type}</code> {item.latest_activity.timestamp}</span>
            ) : '—'
          }
        />
      </KeyValueGrid>
      <EvidenceRefs
        refs={item.evidence_refs}
        diagnostics={[item.reason_code, item.activity_reason_code].filter(
          (value): value is string => Boolean(value),
        )}
      />
    </section>
  );
}
