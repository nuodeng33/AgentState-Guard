/**
 * Devices page: mobile-device pairing UI shell.
 *
 * UI and state model only. Every byte of pairing data comes from the
 * injected DeviceLinkAdapter; the default NullDeviceLinkAdapter performs no
 * I/O and reports pairing as unsupported, in which case the page says so
 * honestly. No Device Link gateway calls, no protocol fields, no crypto.
 */

import { useCallback, useEffect, useState } from 'react';

import { DeviceCard } from '../components/devices/DeviceCard';
import { QrPlaceholderCard } from '../components/devices/QrPlaceholderCard';
import { SasCodeCard } from '../components/devices/SasCodeCard';
import { EmptyState } from '../components/EmptyState';
import { SectionHeader } from '../components/SectionHeader';
import {
  DeviceLinkUnsupportedError,
  nullDeviceLinkAdapter,
  type DeviceLinkAdapter,
} from '../devices/DeviceLinkAdapter';
import type { LinkedDevice, PairingViewState } from '../devices/types';
import { useT } from '../i18n/I18nProvider';

const IN_FLIGHT = new Set(['PAIRING_CREATED', 'WAITING_FOR_MOBILE', 'SAS_PENDING']);

export default function DevicesPage({
  adapter = nullDeviceLinkAdapter,
  pollIntervalMs = 800,
}: {
  adapter?: DeviceLinkAdapter;
  /** Test seam: how often in-flight pairing state is re-polled. */
  pollIntervalMs?: number;
}) {
  const t = useT();
  const [devices, setDevices] = useState<LinkedDevice[] | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  const [pairing, setPairing] = useState<PairingViewState | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const refreshDevices = useCallback(() => {
    adapter.listDevices().then(setDevices, () => setDevices([]));
  }, [adapter]);

  useEffect(() => {
    refreshDevices();
  }, [refreshDevices]);

  // Countdown display ticker (presentation only).
  useEffect(() => {
    if (!pairing?.expiresAt) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [pairing?.expiresAt]);

  // Progress an in-flight pairing through the adapter; terminal phases stop.
  useEffect(() => {
    if (!pairing?.pairingId || !IN_FLIGHT.has(pairing.phase)) return;
    const id = pairing.pairingId;
    const timer = setTimeout(async () => {
      try {
        setPairing(await adapter.pollPairing(id));
      } catch {
        setPairing({ phase: 'ERROR' });
      }
    }, pollIntervalMs);
    return () => clearTimeout(timer);
  }, [pairing, adapter, pollIntervalMs]);

  async function startPairing() {
    try {
      setPairing(await adapter.startPairing());
    } catch (err) {
      if (err instanceof DeviceLinkUnsupportedError) {
        setUnsupported(true);
      } else {
        setPairing({ phase: 'ERROR' });
      }
    }
  }

  async function confirmSas() {
    if (!pairing?.pairingId) return;
    const id = pairing.pairingId;
    setPairing({ ...pairing, phase: 'CONFIRMING' });
    try {
      const next = await adapter.confirmSas(id);
      setPairing(next);
      if (next.phase === 'PAIRED') refreshDevices();
    } catch {
      setPairing({ phase: 'ERROR' });
    }
  }

  async function rejectSas() {
    if (!pairing?.pairingId) return;
    const id = pairing.pairingId;
    try {
      setPairing(await adapter.rejectSas(id));
    } catch {
      setPairing({ phase: 'ERROR' });
    }
  }

  async function cancelPairing() {
    if (pairing?.pairingId) {
      try {
        await adapter.cancelPairing(pairing.pairingId);
      } catch {
        // Cancellation is best-effort; the shell returns to IDLE either way.
      }
    }
    setPairing(null);
  }

  const secondsLeft = pairing?.expiresAt
    ? Math.max(0, Math.round((pairing.expiresAt - now) / 1000))
    : null;

  return (
    <div>
      <SectionHeader title={t('devices.section.mobile')} />

      {devices !== null && devices.length > 0 && (
        <div>
          {devices.map((device) => (
            <DeviceCard key={device.id} device={device} />
          ))}
        </div>
      )}

      {devices !== null && devices.length === 0 && pairing === null && (
        <EmptyState
          title={t('devices.unpaired.title')}
          detail={t('devices.unpaired.detail')}
        />
      )}

      {pairing === null && (
        <div className="action-row">
          <button type="button" className="btn btn-primary" onClick={startPairing}>
            {t('devices.addDevice')}
          </button>
        </div>
      )}
      {unsupported && pairing === null && (
        <p className="device-note">{t('devices.unavailable')}</p>
      )}
      {pairing === null && <p className="device-note">{t('devices.firstHint')}</p>}

      {pairing && (
        <section className="card pairing-card">
          <div className="card-head">
            <span className="card-title">{t('devices.pairing.title')}</span>
          </div>

          {(pairing.phase === 'PAIRING_CREATED' || pairing.phase === 'WAITING_FOR_MOBILE') && (
            <div>
              <QrPlaceholderCard hasPayload={pairing.qrPayload !== undefined} />
              <p className="sas-meta">
                {t('devices.identity')}: <code>{pairing.desktopName ?? '—'}</code>
              </p>
              {secondsLeft !== null && (
                <p className="sas-meta">{t('devices.expires', { seconds: secondsLeft })}</p>
              )}
              <p className="device-note" role="status">
                {t('devices.waiting')}
              </p>
              <div className="action-row">
                <button type="button" className="btn" onClick={cancelPairing}>
                  {t('devices.cancel')}
                </button>
              </div>
            </div>
          )}

          {pairing.phase === 'SAS_PENDING' && pairing.sasCode && (
            <div>
              <SasCodeCard
                sasCode={pairing.sasCode}
                desktopName={pairing.desktopName}
                secondsLeft={secondsLeft}
              />
              <div className="action-row">
                <button type="button" className="btn btn-primary" onClick={confirmSas}>
                  {t('devices.sas.confirm')}
                </button>
                <button type="button" className="btn btn-danger" onClick={rejectSas}>
                  {t('devices.sas.reject')}
                </button>
              </div>
            </div>
          )}

          {pairing.phase === 'CONFIRMING' && (
            <p className="device-note" role="status">
              {t('devices.confirming')}
            </p>
          )}

          {pairing.phase === 'PAIRED' && (
            <div>
              <p className="pairing-ok-title">{t('devices.paired.title')}</p>
              <p className="device-note">{t('devices.paired.detail')}</p>
              <div className="action-row">
                <button type="button" className="btn" onClick={() => setPairing(null)}>
                  {t('devices.done')}
                </button>
              </div>
            </div>
          )}

          {(pairing.phase === 'EXPIRED' || pairing.phase === 'REJECTED' || pairing.phase === 'ERROR') && (
            <div>
              <p className="pairing-terminal-title">
                {pairing.phase === 'EXPIRED'
                  ? t('devices.expired')
                  : pairing.phase === 'REJECTED'
                    ? t('devices.rejected.title')
                    : t('devices.error.title')}
              </p>
              {pairing.reasonCode && (
                <p className="sas-meta">
                  <code>{pairing.reasonCode}</code>
                </p>
              )}
              <div className="action-row">
                <button type="button" className="btn" onClick={() => setPairing(null)}>
                  {t('devices.startOver')}
                </button>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
