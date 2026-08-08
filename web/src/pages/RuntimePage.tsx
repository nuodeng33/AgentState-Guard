import { apiClient, type ApiClient } from '../api/client';
import type { RuntimeItem, RuntimeView } from '../api/types';
import { useR4View } from '../api/useR4View';
import { EmptyState } from '../components/EmptyState';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { availabilityTone, StateBadge, viewStatusTone } from '../components/StateBadge';
import { ViewGate } from '../components/ViewGate';

export default function RuntimePage({ client = apiClient }: { client?: ApiClient }) {
  const state = useR4View<RuntimeView>('runtime', client);
  return (
    <ViewGate state={state} label="Runtime">
      {(data) => <RuntimeViewBody data={data} />}
    </ViewGate>
  );
}

export function RuntimeViewBody({ data }: { data: RuntimeView }) {
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
          title="No runtime records"
          detail="The backend holds no verified runtime facts for this view."
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && (
        <div className="panel panel-bad" role="alert">
          <p className="panel-title">Runtime view degraded</p>
          <p>
            The backend could not project authoritative runtime state (
            <code>{data.reason_code}</code>). Nothing below is projected authority.
          </p>
        </div>
      )}
      {data.status === 'UNKNOWN' && (
        <div className="panel panel-warn" role="alert">
          <p className="panel-title">Runtime state unknown</p>
          <p>
            The backend reports this view as UNKNOWN (<code>{data.reason_code}</code>). Do not
            treat it as healthy.
          </p>
        </div>
      )}

      {data.items.map((item) => (
        <RuntimeCard key={`${item.execution_domain_id}:${item.runtime_type}:${item.evidence_refs[0]}`} item={item} />
      ))}

      <EvidenceRefs refs={data.evidence_refs} />
    </div>
  );
}

function RuntimeCard({ item }: { item: RuntimeItem }) {
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{item.runtime_type ?? 'Unknown runtime'}</span>
        <span className="card-badges">
          <StateBadge label={item.availability} tone={availabilityTone(item.availability)} />
          {item.uncertainty && <StateBadge label="uncertain" tone="warn" />}
        </span>
      </div>
      <KeyValueGrid>
        <KeyValue k="Execution domain" v={orDash(item.execution_domain_id)} />
        <KeyValue
          k="Capabilities"
          v={
            item.capabilities.length > 0 ? (
              <span className="chips">
                {item.capabilities.map((cap) => (
                  <span key={cap} className="chip">
                    {cap}
                  </span>
                ))}
              </span>
            ) : (
              <span className="muted">None reported</span>
            )
          }
        />
        <KeyValue k="reason_code" v={<code>{item.reason_code}</code>} />
      </KeyValueGrid>
      <EvidenceRefs refs={item.evidence_refs} />
    </section>
  );
}
