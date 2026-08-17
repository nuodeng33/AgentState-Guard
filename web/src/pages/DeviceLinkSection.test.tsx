/**
 * Device Link lifecycle section tests (real backend wiring).
 *
 * Covers: GET /api/v1/devices rendered verbatim; enable/disable/refresh post
 * exactly {}; revoke posts exactly {} to the device's own route; failure
 * surfaces the typed reason_code only; the disable≠unpair note is present.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { createApiClient, ApiActionError } from '../api/client';
import { DeviceLinkSection } from './DeviceLinkSection';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ENABLED_LINK = {
  schema_version: 'device-link-lifecycle-1',
  enabled: true,
  status: 'ENABLED',
  endpoint: 'https://192.168.1.42:8788',
  address: '192.168.1.42',
  subnet: '192.168.1.0/24',
  reason_code: 'DEVICE_LINK_ENABLED',
  desktop_uuid: 'uuid-desktop-1',
  desktop_signing_fingerprint: 'ab'.repeat(32),
  tls_spki_fingerprint: 'cd'.repeat(32),
  bound_devices: [
    {
      uuid: 'dev-android-1',
      fingerprint: 'ef'.repeat(32),
      display_name: 'Pixel 7',
      last_seen: '2026-08-16T10:00:00Z',
    },
  ],
  active_pair_sessions: 0,
  firewall: {
    operation: 'APPLY',
    status: 'APPLIED',
    reason_code: 'DEVICE_FIREWALL_APPLIED',
    scope_digest: '12'.repeat(32),
    recorded_at: '2026-08-16T09:59:00Z',
  },
};

const DISABLED_LINK = {
  ...ENABLED_LINK,
  enabled: false,
  status: 'DISABLED',
  endpoint: null,
  address: null,
  subnet: null,
  reason_code: 'DEVICE_LINK_DISABLED',
  active_pair_sessions: 0,
};


const DEGRADED_LINK = {
  ...DISABLED_LINK,
  enabled: undefined,
  status: 'DEGRADED',
  reason_code: 'DEVICE_FIREWALL_REMOVE_FAILED',
  active_pair_sessions: undefined,
  bound_devices: undefined,
  firewall: {
    operation: 'REMOVE',
    status: 'FAILED',
    reason_code: 'DEVICE_FIREWALL_REMOVE_FAILED',
    scope_digest: '34'.repeat(32),
    recorded_at: '2026-08-16T11:00:00Z',
  },
};
interface Recorded {
  path: string;
  method: string;
  body: unknown;
}

function buildLinkFetch(link: object, posts: Recorded[]) {
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === '/api/session') return json({ token: 't' });
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body ?? '{}')) as unknown;
      posts.push({ path, method: 'POST', body });
      if (path.includes('/revoke')) {
        return json({ schema_version: 'device-link-device-action-1', status: 'revoked', device_uuid: 'dev-android-1' });
      }
      return json(ENABLED_LINK);
    }
    return json(link);
  });
  return createApiClient(fetchImpl as unknown as typeof fetch);
}

describe('DeviceLinkSection', () => {
  it('renders the lifecycle DTO verbatim, including disable≠unpair note', async () => {
    const posts: Recorded[] = [];
    render(<DeviceLinkSection client={buildLinkFetch(ENABLED_LINK, posts)} />);

    expect(await screen.findByText('ENABLED')).toBeTruthy();
    expect(screen.getByText('DEVICE_LINK_ENABLED')).toBeTruthy();
    expect(screen.getByText('https://192.168.1.42:8788')).toBeTruthy();
    expect(screen.getByText('192.168.1.0/24')).toBeTruthy();
    expect(screen.getByText('uuid-desktop-1')).toBeTruthy();
    expect(screen.getByText('ab'.repeat(32))).toBeTruthy();
    expect(screen.getByText('cd'.repeat(32))).toBeTruthy();
    expect(screen.getByText('Pixel 7')).toBeTruthy();
    expect(screen.getByText(/2026-08-16T10:00:00Z/)).toBeTruthy();
    expect(screen.getByText(/keeps the durable binding/)).toBeTruthy();
    expect(screen.queryByText(/VPN|cloud|relay/)).toBeNull();
    expect(screen.getByText('APPLY')).toBeTruthy();
    expect(screen.getByText('APPLIED')).toBeTruthy();
    expect(screen.getByText('DEVICE_FIREWALL_APPLIED')).toBeTruthy();
    expect(screen.getByText('12'.repeat(32))).toBeTruthy();
    expect(screen.getByText('2026-08-16T09:59:00Z')).toBeTruthy();
  });

  it('ENABLE → POST {} to /enable then re-reads', async () => {
    const posts: Recorded[] = [];
    render(<DeviceLinkSection client={buildLinkFetch(DISABLED_LINK, posts)} />);

    expect(await screen.findByText('DISABLED')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Enable' }));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].path).toBe('/api/v1/device-link/enable');
    expect(posts[0].body).toEqual({});
  });

  it('preserves DEGRADED and renders missing active sessions as unknown', async () => {
    const posts: Recorded[] = [];
    render(<DeviceLinkSection client={buildLinkFetch(DEGRADED_LINK, posts)} />);

    const status = await screen.findByText('DEGRADED');
    expect(status.className).toContain('badge-warn');
    expect(screen.queryByText('DISABLED')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Enable' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Disable' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Refresh network' })).toBeNull();
    expect(screen.getAllByText('DEVICE_FIREWALL_REMOVE_FAILED').length).toBeGreaterThan(0);
    expect(screen.getByText('REMOVE')).toBeTruthy();
    expect(screen.getByText('FAILED')).toBeTruthy();
    expect(screen.getByText('34'.repeat(32))).toBeTruthy();
    expect(screen.queryByText('No mobile device is currently bound.')).toBeNull();
    const boundDevices = screen.getByText('Bound devices').closest('.card')?.querySelector('.bound-device-facts');
    expect(boundDevices?.textContent).toContain('—');
    const sessions = screen.getByText('active pair sessions').closest('.kv-row');
    expect(sessions?.textContent).toContain('—');
    expect(sessions?.textContent).not.toContain('0');
  });

  it('DISABLED shows only Enable; ENABLED exposes Disable + Refresh network', async () => {
    const posts: Recorded[] = [];
    render(<DeviceLinkSection client={buildLinkFetch(ENABLED_LINK, posts)} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Disable' }));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].path).toBe('/api/v1/device-link/disable');
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh network' }));
    await waitFor(() => expect(posts.length).toBe(2));
    expect(posts[1].path).toBe('/api/v1/device-link/network/refresh');
    expect(posts[1].body).toEqual({});
  });

  it('revoke posts exactly {} to the device route and reloads', async () => {
    const posts: Recorded[] = [];
    render(<DeviceLinkSection client={buildLinkFetch(ENABLED_LINK, posts)} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].path).toBe('/api/v1/device-link/devices/dev-android-1/revoke');
    expect(posts[0].body).toEqual({});
  });

  it('failure surfaces only the stable reason_code', async () => {
    const posts: Recorded[] = [];
    let failNext = true;
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/session') return json({ token: 't' });
      if (init?.method === 'POST' && failNext) {
        failNext = false;
        posts.push({ path, method: 'POST', body: {} });
        return json({ reason_code: 'DEVICE_LISTENER_FAILED' }, 503);
      }
      if (init?.method === 'POST') posts.push({ path, method: 'POST', body: {} });
      return json(DISABLED_LINK);
    });
    render(<DeviceLinkSection client={createApiClient(fetchImpl as unknown as typeof fetch)} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Enable' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('DEVICE_LISTENER_FAILED');
  });
});
