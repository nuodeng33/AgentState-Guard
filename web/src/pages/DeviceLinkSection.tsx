/**
 * Device Link binding/lifecycle section of the Devices page.
 *
 * Renders GET /api/v1/devices verbatim: enabled flag, status, endpoint,
 * address, subnet, durable desktop identity (uuid + fingerprints), bound
 * devices, and active pair-session count. Enable / Disable / Refresh post
 * exactly {}; Revoke posts exactly {} to the device's own revoke route.
 *
 * Disable ≠ Unpair: disable removes exposure but keeps the binding (the
 * section shows this note); revoke removes the durable binding.
 * "Same-LAN only" is a backend guarantee rendered as copy, not a UI filter.
 */

import { useState } from 'react';

import type { ApiClient } from '../api/client';
import { failureText } from '../api/errorText';
import {
  disableDeviceLink,
  enableDeviceLink,
  getDevices,
  refreshDeviceNetwork,
  revokeDevice,
} from '../api/product';
import type { DeviceLinkStatus } from '../api/types';
import { useAsync } from '../api/useAsync';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { SectionHeader } from '../components/SectionHeader';
import { StateBadge, type BadgeTone } from '../components/StateBadge';
import { ViewGate } from '../components/ViewGate';
import { useT, type Translate } from '../i18n/I18nProvider';

function linkTone(status: DeviceLinkStatus['status']): BadgeTone {
  switch (status) {
    case 'ENABLED':
      return 'ok';
    case 'DISABLED':
      return 'neutral';
    default:
      return 'warn';
  }
}

export function DeviceLinkSection({ client }: { client: ApiClient }) {
  const t = useT();
  const link = useAsync(() => getDevices(client), [client]);
  return (
    <div>
      <SectionHeader title={t('devices.section.binding')} />
      <ViewGate state={link} label={t('devices.section.binding')}>
        {(data) => <DeviceLinkBody data={data} client={client} reload={link.reload} />}
      </ViewGate>
    </div>
  );
}

function DeviceLinkBody({
  data,
  client,
  reload,
}: {
  data: DeviceLinkStatus;
  client: ApiClient;
  reload: () => void;
}) {
  const t = useT();
  const bound = data.bound_devices ?? [];
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{t('devices.section.binding')}</span>
        <StateBadge label={data.enabled ? t('devices.enabled') : t('devices.disabled')} tone={linkTone(data.status)} />
      </div>
      <KeyValueGrid>
        <KeyValue k="reason_code" v={<code>{data.reason_code}</code>} />
        <KeyValue k={t('devices.endpoint')} v={orDash(data.endpoint)} />
        <KeyValue k={t('devices.address')} v={orDash(data.address)} />
        <KeyValue k={t('devices.subnet')} v={orDash(data.subnet)} />
        <KeyValue k={t('devices.desktopUuid')} v={orDash(data.desktop_uuid)} />
        <KeyValue k={t('devices.signFp')} v={orDash(data.desktop_signing_fingerprint)} />
        <KeyValue k={t('devices.tlsFp')} v={orDash(data.tls_spki_fingerprint)} />
        <KeyValue
          k={t('devices.activeSessions')}
          v={<code>{String(data.active_pair_sessions ?? 0)}</code>}
        />
      </KeyValueGrid>
      <LifecycleActions
        enabled={data.enabled}
        client={client}
        onChanged={reload}
        t={t}
      />
      <h3 className="card-sub">{t('devices.boundTitle')}</h3>
      {bound.length === 0 ? (
        <p className="muted">{t('devices.noBound')}</p>
      ) : (
        <ul className="evidence-list">
          {bound.map((device) => (
            <BoundDeviceRow
              key={device.uuid}
              device={device}
              client={client}
              onChanged={reload}
            />
          ))}
        </ul>
      )}
      <p className="muted">{t('devices.disableNote')}</p>
    </section>
  );
}

function LifecycleActions({
  enabled,
  client,
  onChanged,
  t,
}: {
  enabled: boolean;
  client: ApiClient;
  onChanged: () => void;
  t: Translate;
}) {
  const [busy, setBusy] = useState<'enable' | 'disable' | 'refresh' | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const run = (
    kind: 'enable' | 'disable' | 'refresh',
    action: (c: ApiClient) => Promise<unknown>,
  ) => {
    setBusy(kind);
    setFailure(null);
    action(client).then(
      () => {
        setBusy(null);
        onChanged();
      },
      (err: unknown) => {
        setBusy(null);
        setFailure(t('devices.actionFailed', { reasonCode: failureText(err) }));
      },
    );
  };

  return (
    <div className="action-row">
      {!enabled && (
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => run('enable', enableDeviceLink)}
          disabled={busy !== null}
        >
          {busy === 'enable' ? t('devices.enableRunning') : t('devices.enable')}
        </button>
      )}
      {enabled && (
        <button
          type="button"
          className="btn"
          onClick={() => run('disable', disableDeviceLink)}
          disabled={busy !== null}
        >
          {busy === 'disable' ? t('devices.disableRunning') : t('devices.disable')}
        </button>
      )}
      {enabled && (
        <button
          type="button"
          className="btn"
          onClick={() => run('refresh', refreshDeviceNetwork)}
          disabled={busy !== null}
        >
          {busy === 'refresh' ? t('devices.refreshRunning') : t('devices.refresh')}
        </button>
      )}
      {failure && (
        <span className="muted recovery-action-fail" role="alert">
          {failure}
        </span>
      )}
    </div>
  );
}

function BoundDeviceRow({
  device,
  client,
  onChanged,
}: {
  device: NonNullable<DeviceLinkStatus['bound_devices']>[number];
  client: ApiClient;
  onChanged: () => void;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const revoke = () => {
    setBusy(true);
    setFailure(null);
    revokeDevice(device.uuid, client).then(
      () => {
        setBusy(false);
        onChanged();
      },
      (err: unknown) => {
        setBusy(false);
        setFailure(t('devices.actionFailed', { reasonCode: failureText(err) }));
      },
    );
  };

  return (
    <li className="bound-device-row">
      <code>{device.display_name}</code> <code>{device.uuid}</code>
      {device.last_seen && (
        <span className="muted"> · {t('devices.lastSeen', { time: device.last_seen })}</span>
      )}
      <button
        type="button"
        className="btn btn-danger"
        onClick={revoke}
        disabled={busy}
      >
        {busy ? t('devices.revoking') : t('devices.revoke')}
      </button>
      {failure && (
        <span className="muted recovery-action-fail" role="alert">
          {failure}
        </span>
      )}
    </li>
  );
}
