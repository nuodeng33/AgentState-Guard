/**
 * Full-app smoke through the real production wiring: App shell, navigation,
 * session bootstrap, and all four P8 views against a mocked r4-p8-1 backend.
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
  const match = path.match(/^\/api\/v1\/(runtime|agents|supervision|recovery)$/);
  if (match) return Promise.resolve(json(emptyView(match[1])));
  return Promise.resolve(new Response('not found', { status: 404 }));
}

describe('App smoke (four P8 views)', () => {
  beforeEach(() => {
    fetchCalls.length = 0;
    vi.stubGlobal('fetch', smokeFetch);
  });

  it('navigates all four views without a white screen and never persists the token', async () => {
    render(<App />);

    // Runtime is the default landing view.
    expect(await screen.findByText('No runtime records')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Agents' }));
    expect(await screen.findByText('No agents detected')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Supervision' }));
    expect(await screen.findByText('No supervision sessions')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Recovery' }));
    expect(await screen.findByText('No recovery checkpoints')).toBeTruthy();
    expect(screen.getAllByText('Trusted Baseline').length).toBeGreaterThan(0);

    // Session bootstrap happened first; the wide legacy /api/status is never used.
    expect(fetchCalls[0]).toBe('/api/session');
    expect(fetchCalls).not.toContain('/api/status');
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
