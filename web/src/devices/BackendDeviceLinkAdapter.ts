/**
 * Production Device Link adapter: maps the frozen Core endpoints
 * (/api/v1/devices, /api/v1/device-link/*) onto the pairing UI state model.
 *
 * Backend owns every pairing fact, including the human-formatted SAS when
 * `sas_pending`; it is projected verbatim and only while the session is still
 * in that state. This adapter translates; it never derives, invents, or
 * stores the code.
 */

import { ApiActionError, ApiRequestError, type ApiClient } from '../api/client';
import {
  buildPairUri,
  cancelPairing,
  confirmPairing,
  createPairing,
  getDevices,
  getPairing,
} from '../api/product';
import type { PairingStateResult } from '../api/types';
import type { DeviceLinkAdapter } from './DeviceLinkAdapter';
import { DeviceLinkUnsupportedError } from './DeviceLinkAdapter';
import type { LinkedDevice, PairingPhase, PairingViewState } from './types';

/** Pairing-endpoint pairs whose failure DTO is the canonical pairing-1 schema. */
interface PairingFailureDto {
  schema_version?: unknown;
  status?: unknown;
  reason_code?: unknown;
}

function pairingFailureReason(err: unknown): string | null {
  if (err instanceof ApiActionError) {
    const dto = (err as { dto?: PairingFailureDto | null }).dto;
    if (dto && typeof dto.reason_code === 'string') return dto.reason_code;
    return err.reasonCode;
  }
  return null;
}

function toPhase(state: string): PairingPhase {
  switch (state) {
    case 'created':
      return 'PAIRING_CREATED';
    case 'first_connection':
      return 'WAITING_FOR_MOBILE';
    case 'sas_pending':
      return 'SAS_PENDING';
    case 'confirmed_both':
    case 'consumed':
      return 'CONFIRMING';
    case 'expired':
      return 'EXPIRED';
    case 'rejected':
      return 'REJECTED';
    case 'failed':
    case 'cancelled':
      return 'ERROR';
    default:
      return 'ERROR';
  }
}

function isLinkUnavailable(err: unknown): boolean {
  return err instanceof DeviceLinkUnsupportedError;
}

export function reasonOrUnsupported(err: unknown): never {
  if (err instanceof ApiActionError) {
    if (err.reasonCode === 'DEVICE_LINK_DISABLED' || err.reasonCode === 'DEVICE_LINK_AUTHORITY_UNAVAILABLE') {
      throw new DeviceLinkUnsupportedError();
    }
    throw new ApiActionError(err.status, err.reasonCode, err.dto);
  }
  throw err;
}

export class BackendDeviceLinkAdapter implements DeviceLinkAdapter {
  constructor(private readonly client: ApiClient) {}

  async listDevices(): Promise<LinkedDevice[]> {
    const link = await getDevices(this.client);
    return (link.bound_devices ?? []).map((device) => ({
      id: device.uuid,
      displayName: device.display_name,
    }));
  }

  async startPairing(): Promise<PairingViewState> {
    try {
      const invitation = await createPairing(this.client);
      return {
        phase: 'PAIRING_CREATED',
        pairingId: invitation.session_id,
        qrPayload: buildPairUri(invitation),
        expiresAt: invitation.expires_at_epoch * 1000,
        desktopName: invitation.desktop_signing_fingerprint ?? invitation.desktop_uuid,
      };
    } catch (err) {
      reasonOrUnsupported(err);
    }
  }

  async pollPairing(pairingId: string): Promise<PairingViewState> {
    try {
      const state = await getPairing(pairingId, this.client);
      return viewFromState(pairingId, state);
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 404) {
        return { phase: 'EXPIRED', pairingId, reasonCode: err.reasonCode ?? 'PAIR_SESSION_NOT_FOUND' };
      }
      throw err;
    }
  }

  async confirmSas(pairingId: string): Promise<PairingViewState> {
    return this.confirm(pairingId, true);
  }

  async rejectSas(pairingId: string): Promise<PairingViewState> {
    return this.confirm(pairingId, false);
  }

  private async confirm(pairingId: string, confirm: boolean): Promise<PairingViewState> {
    try {
      const state = await confirmPairing(pairingId, confirm, this.client);
      return viewFromState(pairingId, state);
    } catch (err) {
      const reason = pairingFailureReason(err);
      // 409/4xx with the typed reason pair is the observable failure surface.
      if (reason !== null) {
        return { phase: confirm ? 'ERROR' : 'REJECTED', pairingId, reasonCode: reason };
      }
      reasonOrUnsupported(err);
    }
  }

  async cancelPairing(pairingId: string): Promise<void> {
    try {
      await cancelPairing(pairingId, this.client);
    } catch {
      // Cancel is best-effort; the shell returns to idle regardless.
    }
  }
}

function viewFromState(pairingId: string, state: PairingStateResult): PairingViewState {
  const view: PairingViewState = { phase: toPhase(state.state), pairingId };
  // Backend terminal states surface through the poll; only reason codes from
  // typed failures are attested — never invented here.
  if (state.state === 'expired') view.reasonCode = 'PAIRING_EXPIRED';
  // Serve the Core-owned formatted SAS verbatim, and only while the backend
  // is actually in SAS_PENDING — the terminal projections must never surface
  // a stale code.
  if (state.state === 'sas_pending' && typeof state.sas === 'string') {
    view.sasCode = state.sas;
  }
  return view;
}
