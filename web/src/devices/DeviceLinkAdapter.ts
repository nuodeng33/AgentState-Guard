/**
 * Boundary between the Devices UI and the future Device Link implementation.
 *
 * The UI only ever talks to this typed interface. Implementations must
 * return UI-safe projections (PairingViewState / LinkedDevice) and must
 * never leak protocol internals, raw exceptions, or secrets into the UI.
 */

import type { LinkedDevice, PairingViewState } from './types';

/** Thrown when this build has no Device Link backend wired in. */
export class DeviceLinkUnsupportedError extends Error {
  readonly code = 'DEVICE_LINK_UNSUPPORTED' as const;
  constructor() {
    super('DEVICE_LINK_UNSUPPORTED');
    this.name = 'DeviceLinkUnsupportedError';
  }
}

export interface DeviceLinkAdapter {
  /** Currently linked devices; empty when unpaired. */
  listDevices(): Promise<LinkedDevice[]>;
  /** Begin a pairing attempt; resolves to the initial observable state. */
  startPairing(): Promise<PairingViewState>;
  /** Poll the current pairing state (progress, SAS availability, expiry). */
  pollPairing(pairingId: string): Promise<PairingViewState>;
  /** User confirmed the SAS matches on both devices. */
  confirmSas(pairingId: string): Promise<PairingViewState>;
  /** User reported the SAS does not match. */
  rejectSas(pairingId: string): Promise<PairingViewState>;
  /** Abandon an in-flight pairing attempt. */
  cancelPairing(pairingId: string): Promise<void>;
}

/**
 * Default adapter for builds without Device Link: reports zero devices and
 * marks pairing as unsupported. Performs no I/O of any kind.
 */
export class NullDeviceLinkAdapter implements DeviceLinkAdapter {
  listDevices(): Promise<LinkedDevice[]> {
    return Promise.resolve([]);
  }
  startPairing(): Promise<PairingViewState> {
    return Promise.reject(new DeviceLinkUnsupportedError());
  }
  pollPairing(): Promise<PairingViewState> {
    return Promise.reject(new DeviceLinkUnsupportedError());
  }
  confirmSas(): Promise<PairingViewState> {
    return Promise.reject(new DeviceLinkUnsupportedError());
  }
  rejectSas(): Promise<PairingViewState> {
    return Promise.reject(new DeviceLinkUnsupportedError());
  }
  cancelPairing(): Promise<void> {
    return Promise.resolve();
  }
}

export const nullDeviceLinkAdapter = new NullDeviceLinkAdapter();
