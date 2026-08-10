/**
 * Devices / pairing UI state model (presentation layer only).
 *
 * These are UI-shell states. No protocol fields, keys, or network payloads
 * live here; every value is a UI-safe projection supplied by a
 * DeviceLinkAdapter. The real adapter is wired in later by the Device Link
 * worker; this module must stay free of network and cryptography.
 */

export type PairingPhase =
  | 'IDLE'
  | 'PAIRING_CREATED'
  | 'WAITING_FOR_MOBILE'
  | 'SAS_PENDING'
  | 'CONFIRMING'
  | 'PAIRED'
  | 'EXPIRED'
  | 'REJECTED'
  | 'ERROR';

/** UI-safe view of an in-flight pairing attempt. */
export interface PairingViewState {
  phase: PairingPhase;
  /** Opaque adapter-owned pairing handle. */
  pairingId?: string;
  /** Opaque string a future QR renderer turns into an image; shown as placeholder text now. */
  qrPayload?: string;
  /** Epoch milliseconds when the pairing offer expires. */
  expiresAt?: number;
  /** Desktop display name shown to the user during confirmation. */
  desktopName?: string;
  /** Six-digit SAS from the adapter; never generated client-side. */
  sasCode?: string;
  /** Stable machine reason code for ERROR/EXPIRED/REJECTED; rendered verbatim. */
  reasonCode?: string;
}

/** A previously linked mobile device, as a UI-safe projection. */
export interface LinkedDevice {
  id: string;
  displayName: string;
}
