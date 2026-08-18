/**
 * Home: final V1 aggregation over the authoritative child projections.
 *
 * Every card surfaces exactly what its child endpoint returned — status, item
 * counts, source timestamps, plus the device-link lifecycle summary. Machine
 * reason codes and raw firewall records stay inside collapsed secondary
 * diagnostics; the primary card language is user-readable product language.
 * Home never synthesizes an aggregate health verdict and never infers
 * freshness from elapsed time; observed_at values render as source times only.
 */

import { useState } from 'react';

import { apiClient, type ApiClient } from '../api/client';
import { getDevices } from '../api/product';
import type { DeviceLinkStatus, R4ViewDto, ViewName } from '../api/types';
import { useAsync } from '../api/useAsync';
import { useR4View } from '../api/useR4View';
import { SectionHeader } from '../components/SectionHeader';
import { StateBadge, viewStatusTone, type BadgeTone } from '../components/StateBadge';
import { StatusCard } from '../components/StatusCard';
import { useT, type Translate } from '../i18n/I18nProvider';
import type { MessageKey } from '../i18n/messages';
import { deviceFirewallLabelKey, deviceFirewallState } from '../presentation/deviceFirewall';

const VIEWS: Array<{ view: ViewName; labelKey: MessageKey }> = [
  { view: 'runtime', labelKey: 'nav.environment' },
  { view: 'agents', labelKey: 'nav.agents' },
  { view: 'supervision', labelKey: 'nav.supervision' },
  { view: 'recovery', labelKey: 'nav.recovery' },
  { view: 'changes', labelKey: 'nav.changes' },
];

export default function HomePage({ client = apiClient }: { client?: ApiClient }) {
  const t = useT();
  const devices = useAsync(() => getDevices(client), [client]);
  return (
    <div>
      <p className="home-subtitle">{t('home.subtitle')}</p>
      <SectionHeader title={t('home.section.overview')} />
      <div className="status-card-grid">
        {VIEWS.map(({ view, labelKey }) => (
          <ViewSummaryCard key={view} view={view} labelKey={labelKey} client={client} t={t} />
        ))}
        <DeviceLinkSummaryCard devices={devices} t={t} />
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
      <p className="status-card-meta muted">{t('common.items', { count: data.items.length })}</p>
      {data.observed_at && (
        <p className="status-card-meta muted">{t('env.observedAt', { time: data.observed_at })}</p>
      )}
    </StatusCard>
  );
}

function deviceLinkTone(data: DeviceLinkStatus): BadgeTone {
  switch (data.status) {
    case 'ENABLED':
      return 'ok';
    case 'DISABLED':
      return 'neutral';
    case 'DEGRADED':
      return 'warn';
  }
}

function DeviceLinkSummaryCard({
  devices,
  t,
}: {
  devices: ReturnType<typeof useAsync<DeviceLinkStatus>>;
  t: Translate;
}) {
  if (devices.phase !== 'ready' || !devices.data) {
    return (
      <StatusCard label={t('home.deviceLink')}>
        <p className="status-card-meta muted">
          {devices.phase === 'loading'
            ? t('view.loading', { label: t('home.deviceLink') })
            : t('view.unavailable', { label: t('home.deviceLink') })}
        </p>
      </StatusCard>
    );
  }
  const data = devices.data;
  return (
    <StatusCard
      label={t('home.deviceLink')}
      badge={{
        label: data.status,
        tone: deviceLinkTone(data),
      }}
    >
      <p className="status-card-meta muted">
        {t('devices.firewall.state')}: {t(deviceFirewallLabelKey(deviceFirewallState(data.firewall)))}
      </p>
      <p className="status-card-meta muted">
        {t('devices.boundTitle')}: {data.bound_devices === undefined ? '—' : data.bound_devices.length}
      </p>
      <p className="status-card-meta muted">
        {t('devices.activeSessions')}: {data.active_pair_sessions === undefined ? '—' : data.active_pair_sessions}
      </p>
      <DeviceFirewallDiagnostics data={data} t={t} />
    </StatusCard>
  );
}

/**
 * Collapsed secondary diagnostics for the device-link card: the raw backend
 * authority fields stay verbatim here — machine reason codes, opaque scope
 * digests, and recorded sources — never in the primary card language.
 */
export function DeviceFirewallDiagnostics({
  data,
  t,
}: {
  data: DeviceLinkStatus;
  t: Translate;
}) {
  const [open, setOpen] = useState(false);
  const firewall = data.firewall;
  return (
    <div className="evidence">
      <button
        type="button"
        className="evidence-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {t('devices.firewall.diagnostics')}
      </button>
      {open && (
        <div className="evidence-detail">
          <p className="evidence-diagnostic muted">
            reason_code: <code>{data.reason_code}</code>
          </p>
          {firewall && (
            <>
              <p className="evidence-diagnostic muted">
                firewall.operation: <code>{firewall.operation}</code>
              </p>
              <p className="evidence-diagnostic muted">
                firewall.status: <code>{firewall.status}</code>
              </p>
              <p className="evidence-diagnostic muted">
                firewall.reason_code: <code>{firewall.reason_code}</code>
              </p>
              {firewall.scope_digest && (
                <p className="evidence-diagnostic muted">
                  firewall.scope_digest: <code>{firewall.scope_digest}</code>
                </p>
              )}
              {firewall.recorded_at && (
                <p className="evidence-diagnostic muted">
                  firewall.recorded_at: <code>{firewall.recorded_at}</code>
                </p>
              )}
            </>
          )}
          {data.endpoint && (
            <p className="evidence-diagnostic muted">
              endpoint: <code>{data.endpoint}</code>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
