/**
 * Full-app smoke through the real production wiring: App shell, navigation,
 * session bootstrap, and the V1 surfaces against a mocked r4-p8-1 backend.
 * Runtime and Agents are no longer primary nav items; Environment folds them
 * together with the bounded /api/status and /api/doctor projections, and
 * Changes is the real evidence-backed verified-activity feed.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function emptyView(view: string): Record<string, unknown> {
  const base = {
    schema_version: 'r4-p8-1',
    view,
    status: 'EMPTY',
    reason_code: 'R4_STATE_EMPTY',
    evidence_refs: [],
    items: [],
  };
  if (view === 'recovery') {
    return {
      ...base,
      recovery_level: 'R0',
      r1_verified: false,
      r2_verified: false,
      r3_verified: false,
      test_restore_status: 'NOT_RUN_P6',
      trusted_baseline_status: 'NONE',
      trusted_baseline_id: null,
    };
  }
  return base;
}

const fetchCalls: string[] = [];

function smokeFetch(input: RequestInfo | URL): Promise<Response> {
  const path = String(input);
  fetchCalls.push(path);
  if (path === '/api/session') return Promise.resolve(json({ token: 'smoke-token' }));
  if (path === '/api/readiness') {
    return Promise.resolve(
      json({ status: 'ready', database: 'available', reason_code: 'RUNTIME_READY' }),
    );
  }
  const match = path.match(/^\/api\/v1\/(runtime|agents|supervision|recovery|changes)$/);
  if (match) return Promise.resolve(json(emptyView(match[1])));
  if (path === '/api/v1/devices') {
    return Promise.resolve(
      json({
        schema_version: 'device-link-lifecycle-1',
        enabled: false,
        status: 'DISABLED',
        endpoint: null,
        address: null,
        subnet: null,
        reason_code: 'DEVICE_LINK_DISABLED',
        desktop_uuid: null,
        desktop_signing_fingerprint: null,
        tls_spki_fingerprint: null,
        bound_devices: [],
        active_pair_sessions: 0,
      }),
    );
  }
  if (path === '/api/status') {
    return Promise.resolve(
      json({
        timestamp_utc: '2025-05-17T14:00:00Z',
        checks: { docker: false, port_3001: true },
        versions: { node: 'v22.3.1' },
      }),
    );
  }
  if (path === '/api/doctor') {
    return Promise.resolve(
      json([{ check: 'docker-cli', status: 'UNREACHABLE', message: 'Container is isolated.' }]),
    );
  }
  return Promise.resolve(new Response('not found', { status: 404 }));
}

describe('App smoke (product IA)', () => {
  beforeEach(() => {
    fetchCalls.length = 0;
    vi.stubGlobal('fetch', smokeFetch);
  });

  it('navigates the seven-surface IA without a white screen and never persists the token', async () => {
    render(<App />);

    // Home is the landing view and summarizes the authoritative projections
    // plus the Device Link lifecycle — never an aggregate health verdict.
    expect(await screen.findByText('Overview')).toBeTruthy();
    for (const label of ['Environment', 'Agents', 'Supervision', 'Recovery', 'Changes', 'Device Link']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }

    // Runtime/Agents are no longer primary nav items; Environment folds both.
    expect(screen.queryByRole('button', { name: 'Agents' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Environment' }));
    expect(await screen.findByText('No runtime records')).toBeTruthy();
    expect(screen.getByText('No agents detected')).toBeTruthy();
    // The bounded host projections render verbatim on the same surface.
    expect(screen.getByText('2025-05-17T14:00:00Z')).toBeTruthy();
    expect(screen.getByText('docker-cli')).toBeTruthy();
    expect(screen.getByText('UNREACHABLE')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Supervision' }));
    expect(await screen.findByText('No supervision sessions')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Recovery' }));
    expect(await screen.findByText('No recovery checkpoints')).toBeTruthy();
    expect(screen.getAllByText('Trusted Baseline').length).toBeGreaterThan(0);

    // Changes is the real verified-activity feed; an empty ledger shows the
    // honest empty state with the backend reason_code.
    fireEvent.click(screen.getByRole('button', { name: 'Changes' }));
    expect(await screen.findByText('No verified activity')).toBeTruthy();
    expect(screen.getAllByText('R4_STATE_EMPTY').length).toBeGreaterThan(0);

    // AI Monitor is no longer a primary surface; AI capability has no dead nav.
    expect(screen.queryByRole('button', { name: 'AI Monitor' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(await screen.findByText('System Default')).toBeTruthy();

    // Session bootstrap happened first; /api/status and /api/doctor are used
    // only by the Environment surface (fetched during that navigation above).
    expect(fetchCalls[0]).toBe('/api/session');
    expect(fetchCalls).toContain('/api/status');
    expect(fetchCalls).toContain('/api/doctor');
    expect(fetchCalls.filter((p) => p === '/api/v1/discovery/refresh')).toHaveLength(0);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
