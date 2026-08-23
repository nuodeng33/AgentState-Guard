/**
 * Devices page pairing-shell tests: visibility of every pairing phase,
 * confirm/reject/cancel flows, terminal states, and the unsupported-adapter
 * honesty path. All data comes from a scripted DeviceLinkAdapter — no network.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { DeviceLinkAdapter } from '../devices/DeviceLinkAdapter';
import type { PairingViewState } from '../devices/types';
import DevicesPage from './DevicesPage';

/** Scripted adapter: startPairing/pollPairing return queued states in order. */
function scriptedAdapter(script: {
  start?: PairingViewState;
  polls?: PairingViewState[];
  confirm?: PairingViewState;
  reject?: PairingViewState;
}) {
  const calls: string[] = [];
  const polls = [...(script.polls ?? [])];
  let last: PairingViewState = script.start ?? { phase: 'WAITING_FOR_MOBILE', pairingId: 'p-1' };
  const adapter: DeviceLinkAdapter = {
    listDevices: async () => [],
    startPairing: async () => {
      calls.push('start');
      return last;
    },
    pollPairing: async () => {
      calls.push('poll');
      if (polls.length > 0) last = polls.shift()!;
      return last; // hold the latest state once the script is exhausted
    },
    confirmSas: async () => {
      calls.push('confirm');
      return script.confirm ?? { phase: 'PAIRED', pairingId: 'p-1' };
    },
    rejectSas: async () => {
      calls.push('reject');
      return script.reject ?? { phase: 'REJECTED', pairingId: 'p-1' };
    },
    cancelPairing: async () => {
      calls.push('cancel');
    },
  };
  return { adapter, calls };
}

const BASE: PairingViewState = {
  phase: 'WAITING_FOR_MOBILE',
  pairingId: 'p-1',
  qrPayload: 'agentstate://pair?…',
  expiresAt: Date.now() + 300_000,
  desktopName: 'DESKTOP-K3',
};

describe('DevicesPage unpaired state', () => {
  it('shows the honest unpaired state with an add action', async () => {
    const { adapter } = scriptedAdapter({});
    render(<DevicesPage adapter={adapter} />);
    expect(await screen.findByText('No mobile device connected')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add mobile device' })).toBeTruthy();
    expect(screen.getByText(/6-digit security code/)).toBeTruthy();
  });

  it('shows the disabled-state note when the backend reports pairing as disabled/unavailable', async () => {
    const { adapter } = scriptedAdapter({ reject: { phase: 'ERROR' } });
    const erring = {
      ...adapter,
      startPairing: async () => {
        const { DeviceLinkUnsupportedError } = await import('../devices/DeviceLinkAdapter');
        throw new DeviceLinkUnsupportedError();
      },
    };
    render(<DevicesPage adapter={erring} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));
    expect(await screen.findByText(/cannot start until the link is enabled/)).toBeTruthy();
    expect(screen.queryByText('Waiting for the mobile device…')).toBeNull();
  });
});

describe('DevicesPage pairing flow', () => {
  it('renders the QR SVG (not placeholder text), identity, expiry and waiting state', async () => {
    const { adapter } = scriptedAdapter({ start: BASE });
    render(<DevicesPage adapter={adapter} pollIntervalMs={10_000} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));

    expect(await screen.findByText('Pair a mobile device')).toBeTruthy();
    // Real QR SVG rendered from the canonical payload; no placeholder copy.
    expect(document.querySelector('.qr-card svg')).not.toBeNull();
    expect(screen.queryByText(/will appear here/)).toBeNull();
    expect(screen.getByText('DESKTOP-K3')).toBeTruthy();
    expect(screen.getByText(/expires in/)).toBeTruthy();
    expect(screen.getByText('Waiting for the mobile device…')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy();
  });

  it('shows "QR payload pending" honestly when no payload arrived yet', async () => {
    const { adapter } = scriptedAdapter({
      start: { phase: 'WAITING_FOR_MOBILE', pairingId: 'p-0' },
    });
    render(<DevicesPage adapter={adapter} pollIntervalMs={10_000} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));
    expect(await screen.findByText('QR payload pending…')).toBeTruthy();
    expect(document.querySelector('.qr-card svg')).toBeNull();
  });

  it('keeps the live invitation QR across polling transitions while pairing still waits', async () => {
    // Mirrors the real backend adapter contract: poll results report the
    // phase only and never repeat invitation metadata (qrPayload/expires/
    // desktopName). Regression for the physical dogfood failure where the
    // first ~800ms poll erased the QR and the UI fell back to "QR pending".
    const { adapter, calls } = scriptedAdapter({
      start: BASE,
      polls: [
        { phase: 'WAITING_FOR_MOBILE', pairingId: 'p-1' },
        { phase: 'WAITING_FOR_MOBILE', pairingId: 'p-1' },
      ],
    });
    render(<DevicesPage adapter={adapter} pollIntervalMs={0} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));

    expect(await screen.findByText('Pair a mobile device')).toBeTruthy();
    await waitFor(() => expect(document.querySelector('.qr-card svg')).not.toBeNull());
    await waitFor(() => expect(calls.filter((c) => c === 'poll').length).toBeGreaterThanOrEqual(1));
    // QR, identity and expiry survive real polling transitions untouched.
    expect(document.querySelector('.qr-card svg')).not.toBeNull();
    expect(screen.queryByText('QR payload pending…')).toBeNull();
    expect(screen.getByText('DESKTOP-K3')).toBeTruthy();
    expect(screen.getByText(/expires in/)).toBeTruthy();
  });

  it('does not leak the previous invitation QR into a terminal phase', async () => {
    const { adapter } = scriptedAdapter({
      start: BASE,
      polls: [{ phase: 'EXPIRED', pairingId: 'p-1', reasonCode: 'PAIRING_EXPIRED' }],
    });
    render(<DevicesPage adapter={adapter} pollIntervalMs={0} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));

    await waitFor(() => expect(screen.getByText('The pairing offer expired.')).toBeTruthy());
    // Terminal phase replaces the invitation wholesale: no QR card, no
    // identity metadata, no lingering countdown from the dead invitation.
    expect(document.querySelector('.qr-card svg')).toBeNull();
    expect(screen.queryByText('DESKTOP-K3')).toBeNull();
    expect(screen.queryByText(/expires in/)).toBeNull();
    expect(screen.getByText('PAIRING_EXPIRED')).toBeTruthy();
  });

  it('moves to SAS_PENDING via polling, renders the backend-supplied SAS, and never invents one', async () => {
    const { adapter } = scriptedAdapter({
      start: BASE,
      polls: [{ ...BASE, phase: 'SAS_PENDING', sasCode: '123 456' }],
    });
    render(<DevicesPage adapter={adapter} pollIntervalMs={0} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));

    expect(await screen.findByRole('button', { name: 'Codes match' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /cancel|Codes do not match/ })).toBeTruthy();
    // The server-owned code is projected verbatim; the adapter never formats it.
    expect(screen.getByText('123 456')).toBeTruthy();
    const sasLabel = document.querySelector('.sas-code-label');
    expect(sasLabel).not.toBeNull();
    expect(sasLabel!.textContent).toContain('123 456');
  });

  it('fails closed silently when SAS_PENDING carries no SAS code (no placeholder)', async () => {
    const { adapter } = scriptedAdapter({
      start: BASE,
      polls: [{ ...BASE, phase: 'SAS_PENDING' }],
    });
    render(<DevicesPage adapter={adapter} pollIntervalMs={0} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));

    expect(await screen.findByRole('button', { name: 'Codes match' })).toBeTruthy();
    // A missing SAS must show nothing — not a fake code and not a placeholder.
    expect(screen.queryByRole('note')).toBeNull();
    expect(screen.queryByText(/^\d{3} \d{3}$/)).toBeNull();
  });

  it('drops the SAS display immediately once a terminal phase arrives', async () => {
    const { adapter } = scriptedAdapter({
      start: { ...BASE, phase: 'SAS_PENDING', sasCode: '123 456' },
      polls: [{ phase: 'REJECTED', pairingId: 'p-1', reasonCode: 'SAS_MISMATCH' }],
    });
    render(<DevicesPage adapter={adapter} pollIntervalMs={0} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));
    expect(await screen.findByText('123 456')).toBeTruthy();

    await waitFor(() => expect(screen.queryByText('123 456')).toBeNull());
    expect(screen.getByText('Pairing rejected')).toBeTruthy();
  });

  it('confirm leads to PAIRED and refreshes the device list', async () => {
    const { adapter, calls } = scriptedAdapter({
      start: { ...BASE, phase: 'SAS_PENDING', sasCode: '123456' },
      confirm: { phase: 'PAIRED', pairingId: 'p-1' },
    });
    render(<DevicesPage adapter={adapter} pollIntervalMs={10_000} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Codes match' }));

    expect(await screen.findByText('Device paired')).toBeTruthy();
    expect(calls).toContain('confirm');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByText('Device paired')).toBeNull());
  });

  it('reject leads to the REJECTED terminal state', async () => {
    const { adapter, calls } = scriptedAdapter({
      start: { ...BASE, phase: 'SAS_PENDING', sasCode: '123456' },
      reject: { phase: 'REJECTED', pairingId: 'p-1', reasonCode: 'SAS_MISMATCH' },
    });
    render(<DevicesPage adapter={adapter} pollIntervalMs={10_000} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Codes do not match — cancel' }));

    expect(await screen.findByText('Pairing rejected')).toBeTruthy();
    expect(screen.getByText('SAS_MISMATCH')).toBeTruthy();
    expect(calls).toContain('reject');
  });

  it('renders EXPIRED as a terminal state with start-over', async () => {
    const { adapter } = scriptedAdapter({
      start: BASE,
      polls: [{ phase: 'EXPIRED', pairingId: 'p-1', reasonCode: 'PAIRING_EXPIRED' }],
    });
    render(<DevicesPage adapter={adapter} pollIntervalMs={0} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));

    expect(await screen.findByText('The pairing offer expired.')).toBeTruthy();
    expect(screen.getByText('PAIRING_EXPIRED')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start over' })).toBeTruthy();
  });

  it('renders ERROR without inventing a cause', async () => {
    const { adapter } = scriptedAdapter({
      start: BASE,
      polls: [{ phase: 'ERROR', reasonCode: 'DEVICE_LINK_UNAVAILABLE' }],
    });
    render(<DevicesPage adapter={adapter} pollIntervalMs={0} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));

    expect(await screen.findByText('Pairing failed')).toBeTruthy();
    expect(screen.getByText('DEVICE_LINK_UNAVAILABLE')).toBeTruthy();
  });

  it('cancel returns to the idle unpaired state', async () => {
    const { adapter, calls } = scriptedAdapter({ start: BASE });
    render(<DevicesPage adapter={adapter} pollIntervalMs={10_000} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));

    expect(await screen.findByText('No mobile device connected')).toBeTruthy();
    expect(calls).toContain('cancel');
  });
});
