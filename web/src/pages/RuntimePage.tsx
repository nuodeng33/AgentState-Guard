import type { RuntimeItem, RuntimeView } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { availabilityTone, StateBadge, viewStatusTone } from '../components/StateBadge';
import { DegradedPanel, UnknownPanel } from '../components/StatePanels';
import { useI18n } from '../i18n/I18nProvider';
import {
  capabilityDisplay,
  productTokenDisplay,
  reasonCodeDisplay,
  runtimeTypeDisplay,
} from '../presentation/productLanguage';

export function RuntimeViewBody({ data }: { data: RuntimeView }) {
  const { locale, t } = useI18n();
  const label = t('nav.runtime');
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
          title={t('runtime.empty.title')}
          detail={t('runtime.empty.detail')}
          reasonCode={reasonCodeDisplay(data.reason_code, locale)}
        />
      )}
      {data.status === 'DEGRADED' && (
        <DegradedPanel label={label} reasonCode={data.reason_code} />
      )}
      {data.status === 'UNKNOWN' && <UnknownPanel label={label} reasonCode={data.reason_code} />}

      {data.items.map((item) => (
        <RuntimeCard
          key={`${item.execution_domain_id}:${item.runtime_type}:${item.evidence_refs[0]}`}
          item={item}
        />
      ))}

      <EvidenceRefs refs={data.evidence_refs} />
    </div>
  );
}

function RuntimeCard({ item }: { item: RuntimeItem }) {
  const { locale, t } = useI18n();
  const runtimeType = item.runtime_type
    ? runtimeTypeDisplay(item.runtime_type, locale)
    : t('runtime.unknownType');
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">
          {item.domain_label ?? item.execution_domain_id ?? runtimeType}
        </span>
        <span className="card-badges">
          <StateBadge
            label={productTokenDisplay(item.availability, locale)}
            tone={availabilityTone(item.availability)}
          />
          {item.uncertainty && <StateBadge label={t('runtime.uncertain')} tone="warn" />}
        </span>
      </div>
      <KeyValueGrid>
        <KeyValue k={locale === 'zh-CN' ? '运行环境类型' : 'Runtime type'} v={runtimeType} />
        <KeyValue k={t('kv.executionDomain')} v={orDash(item.execution_domain_id)} />
        <KeyValue
          k={t('kv.capabilities')}
          v={
            item.capabilities.length > 0 ? (
              <span className="chips">
                {item.capabilities.map((cap) => (
                  <span key={cap} className="chip">
                    {capabilityDisplay(cap, locale)}
                  </span>
                ))}
              </span>
            ) : (
              <span className="muted">{t('runtime.noCapabilities')}</span>
            )
          }
        />
      </KeyValueGrid>
      <EvidenceRefs refs={item.evidence_refs} diagnostics={[item.reason_code]} />
    </section>
  );
}
