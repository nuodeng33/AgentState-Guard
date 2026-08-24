import type { AgentItem, AgentsView } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { StateBadge, viewStatusTone, type BadgeTone } from '../components/StateBadge';
import { DegradedPanel, UnknownPanel } from '../components/StatePanels';
import { useI18n } from '../i18n/I18nProvider';
import {
  agentDisplayName,
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
      {data.status === 'DEGRADED' && (
        <DegradedPanel label={label} reasonCode={data.reason_code} />
      )}
      {data.status === 'UNKNOWN' && <UnknownPanel label={label} reasonCode={data.reason_code} />}

      {data.items.map((item, index) => (
        <AgentCard
          key={`${item.execution_domain_id}:${item.detected_identity}:${item.evidence_refs[0]}`}
          item={item}
          ordinal={index + 1}
        />
      ))}

      <EvidenceRefs refs={data.evidence_refs} />
    </div>
  );
}

export function AgentCard({ item, ordinal = 1 }: { item: AgentItem; ordinal?: number }) {
  const { locale, t } = useI18n();
  const label = item.instance_label ?? item.detected_identity;
  const instanceId = agentInstanceId(label);
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{locale === 'zh-CN' ? `智能体 ${ordinal}` : `Agent ${ordinal}`}</span>
        <span className="card-badges">
          <StateBadge
            label={productTokenDisplay(item.lifecycle, locale)}
            tone={lifecycleTone(item.lifecycle)}
          />
          {item.uncertainty && <StateBadge label={t('runtime.uncertain')} tone="warn" />}
        </span>
      </div>
      <p className="card-sub">{agentDisplayName(label)}</p>
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
      </KeyValueGrid>
      <EvidenceRefs refs={item.evidence_refs} diagnostics={[item.reason_code]} />
    </section>
  );
}
