import { apiClient, type ApiClient } from '../api/client';
import type { AgentItem, AgentsView } from '../api/types';
import { useR4View } from '../api/useR4View';
import { EmptyState } from '../components/EmptyState';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { StateBadge, viewStatusTone, type BadgeTone } from '../components/StateBadge';
import { ViewGate } from '../components/ViewGate';

export default function AgentsPage({ client = apiClient }: { client?: ApiClient }) {
  const state = useR4View<AgentsView>('agents', client);
  return (
    <ViewGate state={state} label="Agents">
      {(data) => <AgentsViewBody data={data} />}
    </ViewGate>
  );
}

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
          title="No agents detected"
          detail="The backend holds no verified agent detection records."
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && (
        <div className="panel panel-bad" role="alert">
          <p className="panel-title">Agents view degraded</p>
          <p>
            The backend could not project authoritative agent state (
            <code>{data.reason_code}</code>). Nothing below is projected authority.
          </p>
        </div>
      )}
      {data.status === 'UNKNOWN' && (
        <div className="panel panel-warn" role="alert">
          <p className="panel-title">Agents state unknown</p>
          <p>
            The backend reports this view as UNKNOWN (<code>{data.reason_code}</code>). Do not
            treat it as healthy.
          </p>
        </div>
      )}

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

function AgentCard({ item }: { item: AgentItem }) {
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{item.detected_identity}</span>
        <span className="card-badges">
          <StateBadge label={item.lifecycle} tone={lifecycleTone(item.lifecycle)} />
          {item.uncertainty && <StateBadge label="uncertain" tone="warn" />}
        </span>
      </div>
      <KeyValueGrid>
        <KeyValue k="Role" v={item.role} />
        <KeyValue k="Confidence" v={item.confidence.toFixed(2)} />
        <KeyValue k="Execution domain" v={orDash(item.execution_domain_id)} />
        <KeyValue
          k="Workspace status"
          v={<StateBadge label={item.workspace.status} tone={item.workspace.status === 'UNKNOWN' ? 'unknown' : 'neutral'} />}
        />
        <KeyValue k="Workspace binding" v={orDash(item.workspace.binding_ref)} />
        <KeyValue k="reason_code" v={<code>{item.reason_code}</code>} />
      </KeyValueGrid>
      <EvidenceRefs refs={item.evidence_refs} />
    </section>
  );
}
