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
 * - Both devices display the same server-owned SAS before explicit confirmation.
 *   Consumed means the server completed the durable binding.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

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
  const [busy, setBusy] = useState(false);
  const [devicesRevision, setDevicesRevision] = useState(0);
  const attempt = useRef(0), busyRef = useRef(false);
  useEffect(() => () => { attempt.current++; }, []);
  const validSas = pairing?.phase === 'SAS_PENDING' && /^\d{3} \d{3}$/.test(pairing.sasCode ?? '');
  function applyView(next: PairingViewState) {
    setPairing(previous => {
      const same = previous?.pairingId === next.pairingId;
      const invitation = ['PAIRING_CREATED', 'WAITING_FOR_MOBILE'].includes(next.phase);
      return { ...(same ? previous : {}), ...next,
        qrPayload: invitation ? next.qrPayload ?? (same ? previous?.qrPayload : undefined) : undefined,
        expiresAt: next.expiresAt ?? (same ? previous?.expiresAt : undefined),
        desktopName: next.desktopName ?? (same ? previous?.desktopName : undefined),
        sasCode: next.phase === 'SAS_PENDING' ? next.sasCode : undefined };
    });
    if (next.phase === 'PAIRED') setDevicesRevision(value => value + 1);
  }

  // Countdown display ticker (presentation only).
  useEffect(() => {
    if (!pairing?.expiresAt || !IN_FLIGHT.has(pairing.phase)) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [pairing?.expiresAt, pairing?.phase]);

  // Late replies must never reopen a cancelled or replaced attempt.
  useEffect(() => {
    if (busy || !pairing?.pairingId || !IN_FLIGHT.has(pairing.phase)) return;
    const id = pairing.pairingId, epoch = attempt.current;
    let active = true;
    const timer = setTimeout(async () => {
      try {
        const next = await resolved.pollPairing(id);
        if (active && epoch === attempt.current) applyView(next);
      } catch (err) {
        if (active && epoch === attempt.current) setPairing({ phase: 'ERROR', pairingId: id, reasonCode: reasonCodeOf(err) });
      }
    }, pollIntervalMs);
    return () => { active = false; clearTimeout(timer); };
  }, [pairing, resolved, pollIntervalMs, busy]);

  async function startPairing() {
    if (busyRef.current) return;
    const epoch = ++attempt.current;
    busyRef.current = true; setBusy(true); setUnsupported(false);
    try {
      const next = await resolved.startPairing();
      if (epoch === attempt.current) applyView(next);
    } catch (err) {
      if (epoch !== attempt.current) return;
      if (err instanceof DeviceLinkUnsupportedError) setUnsupported(true);
      else setPairing({ phase: 'ERROR', reasonCode: reasonCodeOf(err) });
    } finally {
      if (epoch === attempt.current) { busyRef.current = false; setBusy(false); }
    }
  }
  async function confirmSas() {
    if (busyRef.current || !validSas || !pairing?.pairingId) return;
    const id = pairing.pairingId, epoch = ++attempt.current;
    busyRef.current = true; setBusy(true);
    setPairing({ ...pairing, phase: 'CONFIRMING', sasCode: undefined });
    try {
      const next = await resolved.confirmSas(id);
      if (epoch === attempt.current) applyView(next);
    } catch (err) {
      if (epoch === attempt.current) setPairing({ phase: 'ERROR', pairingId: id, reasonCode: reasonCodeOf(err) });
    } finally {
      if (epoch === attempt.current) { busyRef.current = false; setBusy(false); }
    }
  }
  async function rejectSas() {
    if (busyRef.current || !pairing?.pairingId) return;
    const id = pairing.pairingId, epoch = ++attempt.current;
    busyRef.current = true; setBusy(true);
    try {
      const next = await resolved.rejectSas(id);
      if (epoch === attempt.current) applyView(next);
    } catch (err) {
      if (epoch === attempt.current) setPairing({ phase: 'ERROR', pairingId: id, reasonCode: reasonCodeOf(err) });
    } finally {
      if (epoch === attempt.current) { busyRef.current = false; setBusy(false); }
    }
  }
  async function cancelPairing() {
    const id = pairing?.pairingId;
    attempt.current++; busyRef.current = false; setBusy(false); setPairing(null);
    if (id) { try { await resolved.cancelPairing(id); } catch { /* The server expires abandoned invitations. */ } }
  }

  const secondsLeft = pairing?.expiresAt
    ? Math.max(0, Math.round((pairing.expiresAt - now) / 1000))
    : null;

  return (
    <div>
      {adapter === undefined && client && <DeviceLinkSection client={client} refreshKey={devicesRevision} />}

      <SectionHeader title={t('devices.section.mobile')} />

      {pairing === null && (
        <div className="action-row">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={startPairing}>
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
              {validSas ? <p className="sas-code" aria-label="SAS">{pairing.sasCode}</p>
                : <p className="device-note" role="status">{t('devices.sasWaiting')}</p>}
              {secondsLeft !== null && (
                <p className="sas-meta">{t('devices.expires', { seconds: secondsLeft })}</p>
              )}
              <div className="action-row">
                <button type="button" className="btn btn-primary" disabled={busy || !validSas} onClick={confirmSas}>
                  {t('devices.sas.confirm')}
                </button>
                <button type="button" className="btn btn-danger" disabled={busy} onClick={rejectSas}>
                  {t('devices.sas.reject')}
                </button>
                <button type="button" className="btn" onClick={cancelPairing}>
                  {t('devices.cancel')}
                </button>
              </div>
            </div>
          )}

          {pairing.phase === 'CONFIRMING' && <div>
            <p className="device-note" role="status">{t('devices.confirming')}</p>
            <button type="button" className="btn" onClick={cancelPairing}>{t('devices.cancel')}</button>
          </div>}

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
