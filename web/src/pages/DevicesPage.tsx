/**
 * Devices page: real Device Link wiring (production default).
 *
 * - Link lifecycle (status/endpoint/identity/bound devices/revoke/enable/
 *   disable/refresh) lives in DeviceLinkSection, which consumes GET
 *   /api/v1/devices verbatim.
 * - Pairing starts with POST /api/v1/device-link/pairings, renders the
 *   canonical QR (QrCodeCard) and tracks the pairing state machine through
 *   GET /pairings/{id}: created → first_connection → sas_pending →
 *   confirmed_both/consumed, with expired/rejected/failed shown verbatim.
 * - Desktop confirm sends {confirm:true|false}. While sas_pending the Core
 *   returns a server-owned formatted SAS (e.g. "123 456") via the status
 *   poll; the page displays it verbatim and drops it the moment a terminal
 *   phase arrives. An absent SAS fails closed — no placeholder is shown.
 */

import { useEffect, useMemo, useState } from 'react';

import { apiClient, type ApiClient } from '../api/client';
import { QrCodeCard } from '../components/devices/QrCodeCard';
import { SectionHeader } from '../components/SectionHeader';
import { BackendDeviceLinkAdapter } from '../devices/BackendDeviceLinkAdapter';
import {
  DeviceLinkUnsupportedError,
  type DeviceLinkAdapter,
} from '../devices/DeviceLinkAdapter';
import type { PairingViewState } from '../devices/types';
import { useT } from '../i18n/I18nProvider';
import { DeviceLinkSection } from './DeviceLinkSection';

const IN_FLIGHT = new Set(['PAIRING_CREATED', 'WAITING_FOR_MOBILE', 'SAS_PENDING', 'CONFIRMING']);

export default function DevicesPage({
  client = apiClient as ApiClient | undefined,
  adapter,
  pollIntervalMs = 800,
}: {
  client?: ApiClient;
  /** Test seam; production uses the backend adapter built from the client. */
  adapter?: DeviceLinkAdapter;
  /** Test seam: how often an in-flight pairing is re-polled. */
  pollIntervalMs?: number;
}) {
  const t = useT();
  const resolved = useMemo<DeviceLinkAdapter>(
    () => adapter ?? new BackendDeviceLinkAdapter(client as ApiClient),
    [adapter, client],
  );
  const [pairing, setPairing] = useState<PairingViewState | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [unsupported, setUnsupported] = useState(false);

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
        setPairing(await resolved.pollPairing(id));
      } catch (err) {
        setPairing({ phase: 'ERROR', reasonCode: reasonCodeOf(err) });
      }
    }, pollIntervalMs);
    return () => clearTimeout(timer);
  }, [pairing, resolved, pollIntervalMs]);

  async function startPairing() {
    try {
      setPairing(await resolved.startPairing());
    } catch (err) {
      if (err instanceof DeviceLinkUnsupportedError) {
        setUnsupported(true);
      } else {
        setPairing({ phase: 'ERROR', reasonCode: reasonCodeOf(err) });
      }
    }
  }

  async function confirmSas() {
    if (!pairing?.pairingId) return;
    const id = pairing.pairingId;
    setPairing({ ...pairing, phase: 'CONFIRMING' });
    try {
      setPairing(await resolved.confirmSas(id));
    } catch (err) {
      setPairing({ phase: 'ERROR', reasonCode: reasonCodeOf(err) });
    }
  }

  async function rejectSas() {
    if (!pairing?.pairingId) return;
    const id = pairing.pairingId;
    try {
      setPairing(await resolved.rejectSas(id));
    } catch (err) {
      setPairing({ phase: 'ERROR', reasonCode: reasonCodeOf(err) });
    }
  }

  async function cancelPairing() {
    if (pairing?.pairingId) {
      try {
        await resolved.cancelPairing(pairing.pairingId);
      } catch {
        // Cancellation is best-effort; the shell returns directly.
      }
    }
    setPairing(null);
  }

  const secondsLeft = pairing?.expiresAt
    ? Math.max(0, Math.round((pairing.expiresAt - now) / 1000))
    : null;

  return (
    <div>
      {adapter === undefined && client && <DeviceLinkSection client={client} />}

      <SectionHeader title={t('devices.section.mobile')} />

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
      {pairing === null && (
        <div>
          <p className="device-note">{t('devices.unpaired.title')}</p>
          <p className="device-note">{t('devices.unpaired.detail')}</p>
          <p className="device-note">{t('devices.firstHint')}</p>
        </div>
      )}

      {pairing && (
        <section className="card pairing-card">
          <div className="card-head">
            <span className="card-title">{t('devices.pairing.title')}</span>
          </div>

          {(pairing.phase === 'PAIRING_CREATED' || pairing.phase === 'WAITING_FOR_MOBILE') && (
            <div>
              {pairing.qrPayload ? (
                <QrCodeCard payload={pairing.qrPayload} />
              ) : (
                <p className="device-note">{t('devices.qr.awaiting')}</p>
              )}
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

          {pairing.phase === 'SAS_PENDING' && (
            <div>
              <p className="sas-prompt">{t('devices.sas.prompt')}</p>
              {pairing.sasCode !== undefined && (
                <p className="sas-code-label">
                  <span className="device-note">{t('devices.sas.label')}:</span>{' '}
                  <span className="sas-code" role="note">{pairing.sasCode}</span>
                </p>
              )}
              <p className="device-note">{t('devices.sasWaiting')}</p>
              {secondsLeft !== null && (
                <p className="sas-meta">{t('devices.expires', { seconds: secondsLeft })}</p>
              )}
              <div className="action-row">
                <button type="button" className="btn btn-primary" onClick={confirmSas}>
                  {t('devices.sas.confirm')}
                </button>
                <button type="button" className="btn btn-danger" onClick={rejectSas}>
                  {t('devices.sas.reject')}
                </button>
                <button type="button" className="btn" onClick={cancelPairing}>
                  {t('devices.cancel')}
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

function reasonCodeOf(err: unknown): string {
  if (err instanceof DeviceLinkUnsupportedError) return 'DEVICE_LINK_UNSUPPORTED';
  const errAny = err as { reasonCode?: unknown };
  return typeof errAny.reasonCode === 'string' ? errAny.reasonCode : 'DEVICE_LINK_ACTION_FAILED';
}
