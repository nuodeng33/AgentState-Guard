import type { AgentItem, AgentsView } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { StateBadge, viewStatusTone, type BadgeTone } from '../components/StateBadge';
import { DegradedPanel, UnknownPanel } from '../components/StatePanels';
import { useT } from '../i18n/I18nProvider';

/**
 * Lifecycle is rendered verbatim from the backend. The UI never upgrades a
 * detected identity (Claude Code, Codex, Kimi, …) to RUNNING/INTEGRATED/
 * ENFORCED on its own; UNKNOWN stays UNKNOWN.
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
  const t = useT();
  const label = t('nav.agents');
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
          title={t('agents.empty.title')}
          detail={t('agents.empty.detail')}
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && (
        <DegradedPanel label={label} reasonCode={data.reason_code} />
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
  const t = useT();
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{item.detected_identity}</span>
        <span className="card-badges">
          <StateBadge label={item.lifecycle} tone={lifecycleTone(item.lifecycle)} />
          {item.uncertainty && <StateBadge label={t('runtime.uncertain')} tone="warn" />}
        </span>
      </div>
      <KeyValueGrid>
        <KeyValue k={t('kv.role')} v={item.role} />
        <KeyValue k={t('kv.confidence')} v={item.confidence.toFixed(2)} />
        <KeyValue k={t('kv.executionDomain')} v={orDash(item.execution_domain_id)} />
        <KeyValue
          k={t('kv.workspaceStatus')}
          v={<StateBadge label={item.workspace.status} tone={item.workspace.status === 'UNKNOWN' ? 'unknown' : 'neutral'} />}
        />
        <KeyValue k={t('kv.workspaceBinding')} v={orDash(item.workspace.binding_ref)} />
        <KeyValue k="reason_code" v={<code>{item.reason_code}</code>} />
      </KeyValueGrid>
      <EvidenceRefs refs={item.evidence_refs} />
    </section>
  );
}
