/**
 * Environment surface tests: the V1 slice folds Runtime + Agents into one page
 * that additionally renders the real /api/status and /api/doctor payloads and
 * the POST /api/v1/discovery/refresh action — all against scripted fetch.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import EnvironmentPage from './EnvironmentPage';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const RUNTIME_VIEW = {
  schema_version: 'r4-p8-1',
  view: 'runtime',
  status: 'AVAILABLE',
  reason_code: 'R4_RUNTIME_AVAILABLE',
  evidence_refs: ['evt-runtime-1'],
  observed_at: '2025-05-17T14:05:10Z',
  items: [
    {
      runtime_type: 'python',
      execution_domain_id: 'self-runtime',
      availability: 'AVAILABLE',
      capabilities: ['local-exec', 'fs-read'],
      reason_code: 'RUNTIME_DETECTED',
      uncertainty: false,
      observed_at: '2025-05-17T14:05:10Z',
      evidence_refs: ['evt-runtime-1'],
    },
  ],
};

const AGENTS_VIEW = {
  schema_version: 'r4-p8-1',
  view: 'agents',
  status: 'AVAILABLE',
  reason_code: 'R4_AGENTS_AVAILABLE',
  evidence_refs: ['evt-agent-1'],
  observed_at: '2025-05-17T14:05:12Z',
  items: [
    {
      detected_identity: 'claude-code',
      role: 'detected',
      lifecycle: 'DETECTED',
      confidence: 0.87,
      execution_domain_id: 'edge-domain',
      workspace: { status: 'BOUND', binding_ref: 'ws-binding-1' },
      reason_code: 'AGENT_DETECTED',
      uncertainty: false,
      observed_at: '2025-05-17T14:05:12Z',
      evidence_refs: ['evt-agent-1', 'ws-binding-1'],
    },
  ],
};

const STATUS_PAYLOAD = {
  timestamp_utc: '2025-05-17T14:06:00Z',
  checks: { docker: false, port_3001: true },
  versions: { node: 'v22.3.1', python: 'Python 3.11.2', docker: null },
};

const DOCTOR_PAYLOAD = [
  { check: 'container', status: 'INFO', message: 'Running inside container (no Docker socket access)' },
  {
    check: 'docker-cli',
    status: 'UNREACHABLE',
    message: 'Container is isolated (no Docker socket mounted). This is expected and correct.',
  },
  { check: 'node', status: 'OK', message: 'node v22.3.1' },
];

const REFRESH_RESULT = {
  schema_version: 'product-discovery-1',
  status: 'AVAILABLE',
  reason_code: 'DISCOVERY_REFRESHED',
  snapshot_id: 'snap-1',
  observed_at: '2025-05-17T14:07:00Z',
  affected_views: ['runtime', 'agents'],
  runtime_count: 1,
  agent_count: 1,
  evidence_refs: ['evt-discovery-1'],
};

function envFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const path = String(input);
  if (path === '/api/session') return Promise.resolve(json({ token: 'env-token' }));
  if (path === '/api/status') return Promise.resolve(json(STATUS_PAYLOAD));
  if (path === '/api/doctor') return Promise.resolve(json(DOCTOR_PAYLOAD));
  if (path === '/api/v1/runtime') return Promise.resolve(json(RUNTIME_VIEW));
  if (path === '/api/v1/agents') return Promise.resolve(json(AGENTS_VIEW));
  if (path === '/api/v1/discovery/refresh') {
    // The frozen contract pins the request body to exactly {}.
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({});
    return Promise.resolve(json(REFRESH_RESULT));
  }
  return Promise.resolve(new Response('not found', { status: 404 }));
}

describe('EnvironmentPage real backend wiring', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(envFetch));
  });

  it('renders status/doctor facts and reuses the Runtime/Agents bodies', async () => {
    render(<EnvironmentPage />);

    // Runtime + Agents projection bodies (unchanged K3 presentation).
    expect((await screen.findAllByText('python')).length).toBeGreaterThan(0);
    expect(screen.getByText('claude-code')).toBeTruthy();
    expect(screen.getByText('R4_RUNTIME_AVAILABLE')).toBeTruthy();
    expect(screen.getByText('R4_AGENTS_AVAILABLE')).toBeTruthy();
    // Source times come from the backend projection, never from elapsed-time math.
    expect(screen.getAllByText(/observed at 2025-05-17T14:05/).length).toBeGreaterThan(0);

    // /api/status payload rendered verbatim.
    expect(screen.getByText('2025-05-17T14:06:00Z')).toBeTruthy();
    expect(screen.getByText('v22.3.1')).toBeTruthy();
    expect(screen.getByText('port_3001')).toBeTruthy();

    // /api/doctor rows keep the backend status token verbatim.
    expect(screen.getByText('docker-cli')).toBeTruthy();
    expect(screen.getByText('UNREACHABLE')).toBeTruthy();
    expect(screen.getByText(/Container is isolated/)).toBeTruthy();
  });

  it('refresh posts exactly {} then re-reads the mounted projections', async () => {
    const fetchMock = vi.fn(envFetch);
    vi.stubGlobal('fetch', fetchMock);

    render(<EnvironmentPage />);
    await screen.findAllByText('python');

    fireEvent.click(screen.getByRole('button', { name: 'Refresh discovery' }));

    expect(await screen.findByText('DISCOVERY_REFRESHED')).toBeTruthy();

    const calls = fetchMock.mock.calls.map(([input]) => String(input));
    const posts = calls.filter((p) => p === '/api/v1/discovery/refresh');
    expect(posts).toHaveLength(1);
    // After the refresh, runtime/agents/status/doctor are re-read.
    expect(calls.filter((p) => p === '/api/v1/runtime').length).toBeGreaterThanOrEqual(2);
    expect(calls.filter((p) => p === '/api/v1/agents').length).toBeGreaterThanOrEqual(2);
    expect(calls.filter((p) => p === '/api/status').length).toBeGreaterThanOrEqual(2);
    expect(calls.filter((p) => p === '/api/doctor').length).toBeGreaterThanOrEqual(2);
  });

  it('reports a failed refresh without touching the displayed state', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path === '/api/v1/discovery/refresh') {
          return Promise.resolve(json({ reason_code: 'DISCOVERY_REFRESH_UNAVAILABLE' }, 503));
        }
        return envFetch(input, init);
      }),
    );

    render(<EnvironmentPage />);
    await screen.findAllByText('python');

    fireEvent.click(screen.getByRole('button', { name: 'Refresh discovery' }));
    expect((await screen.findAllByRole('alert')).some((el) => el.textContent?.includes('Discovery refresh failed'))).toBe(
      true,
    );
    // The previously loaded projection stays visible; nothing is rewritten to a guess.
    expect(screen.getAllByText('python').length).toBeGreaterThan(0);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Refresh discovery' })).toBeTruthy();
    });
  });
});
