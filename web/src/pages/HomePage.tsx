/**
 * Home: overview of the four authoritative read views.
 *
 * Every card renders exactly what GET /api/v1/{view} returned — status and
 * reason_code verbatim, item count, and the view's own loading/error phase.
 * Home never synthesizes an aggregate health verdict.
 */

import { apiClient, type ApiClient } from '../api/client';
import type { R4ViewDto, ViewName } from '../api/types';
import { useR4View } from '../api/useR4View';
import { SectionHeader } from '../components/SectionHeader';
import { StateBadge, viewStatusTone } from '../components/StateBadge';
import { StatusCard } from '../components/StatusCard';
import { useT, type Translate } from '../i18n/I18nProvider';
import type { MessageKey } from '../i18n/messages';

const VIEWS: Array<{ view: ViewName; labelKey: MessageKey }> = [
  { view: 'runtime', labelKey: 'nav.runtime' },
  { view: 'agents', labelKey: 'nav.agents' },
  { view: 'supervision', labelKey: 'nav.supervision' },
  { view: 'recovery', labelKey: 'nav.recovery' },
];

export default function HomePage({ client = apiClient }: { client?: ApiClient }) {
  const t = useT();
  return (
    <div>
      <p className="home-subtitle">{t('home.subtitle')}</p>
      <SectionHeader title={t('home.section.overview')} />
      <div className="status-card-grid">
        {VIEWS.map(({ view, labelKey }) => (
          <ViewSummaryCard key={view} view={view} labelKey={labelKey} client={client} t={t} />
        ))}
      </div>
    </div>
  );
}

function ViewSummaryCard({
  view,
  labelKey,
  client,
  t,
}: {
  view: ViewName;
  labelKey: MessageKey;
  client: ApiClient;
  t: Translate;
}) {
  const state = useR4View<R4ViewDto<unknown>>(view, client);
  const label = t(labelKey);

  if (state.phase !== 'ready' || !state.data) {
    return (
      <StatusCard label={label}>
        <p className="status-card-meta muted">
          {state.phase === 'loading' ? t('view.loading', { label }) : t('view.unavailable', { label })}
        </p>
      </StatusCard>
    );
  }

  const data = state.data;
  return (
    <StatusCard label={label} badge={{ label: data.status, tone: viewStatusTone(data.status) }}>
      <p className="status-card-meta">
        <code>{data.reason_code}</code>
      </p>
      <p className="status-card-meta muted">{t('common.items', { count: data.items.length })}</p>
    </StatusCard>
  );
}
