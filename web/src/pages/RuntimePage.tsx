import type { RuntimeItem, RuntimeView } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { availabilityTone, StateBadge, viewStatusTone } from '../components/StateBadge';
import { DegradedPanel, UnknownPanel } from '../components/StatePanels';
import { useT } from '../i18n/I18nProvider';

export function RuntimeViewBody({ data }: { data: RuntimeView }) {
  const t = useT();
  const label = t('nav.runtime');
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
          title={t('runtime.empty.title')}
          detail={t('runtime.empty.detail')}
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && (
        <DegradedPanel label={label} reasonCode={data.reason_code} />
      )}
      {data.status === 'UNKNOWN' && <UnknownPanel label={label} reasonCode={data.reason_code} />}

      {data.items.map((item) => (
        <RuntimeCard key={`${item.execution_domain_id}:${item.runtime_type}:${item.evidence_refs[0]}`} item={item} />
      ))}

      <EvidenceRefs refs={data.evidence_refs} />
    </div>
  );
}

function RuntimeCard({ item }: { item: RuntimeItem }) {
  const t = useT();
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{item.runtime_type ?? t('runtime.unknownType')}</span>
        <span className="card-badges">
          <StateBadge label={item.availability} tone={availabilityTone(item.availability)} />
          {item.uncertainty && <StateBadge label={t('runtime.uncertain')} tone="warn" />}
        </span>
      </div>
      <KeyValueGrid>
        <KeyValue k={t('kv.executionDomain')} v={orDash(item.execution_domain_id)} />
        <KeyValue
          k={t('kv.capabilities')}
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
              <span className="muted">{t('runtime.noCapabilities')}</span>
            )
          }
        />
        <KeyValue k="reason_code" v={<code>{item.reason_code}</code>} />
      </KeyValueGrid>
      <EvidenceRefs refs={item.evidence_refs} />
    </section>
  );
}
