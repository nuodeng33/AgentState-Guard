import { describe, expect, it, vi } from 'vitest';

import {
  ApiRequestError,
  createApiClient,
  SessionUnavailableError,
} from './client';

type FetchCall = { path: string; token: string | null };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** Build a fetch mock from a queue of handlers; records every call in order. */
function mockFetch(handlers: Array<() => Response | Promise<Response>>) {
  const calls: FetchCall[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    calls.push({ path: String(input), token: headers.get('X-Session-Token') });
    const handler = handlers.length > 1 ? handlers.shift()! : handlers[0];
    if (!handler) throw new Error('unexpected fetch call');
    return handler();
  });
  return { calls, fetchImpl: fetchImpl as unknown as typeof fetch };
}

const RUNTIME_EMPTY = {
  schema_version: 'r4-p8-1',
  view: 'runtime',
  status: 'EMPTY',
  reason_code: 'R4_STATE_EMPTY',
  evidence_refs: [],
  items: [],
};

describe('api client session bootstrap', () => {
  it('fetches the session token before any protected request and attaches it as X-Session-Token', async () => {
    const { calls, fetchImpl } = mockFetch([
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse(RUNTIME_EMPTY),
    ]);
    const client = createApiClient(fetchImpl);

    const view = await client.get<typeof RUNTIME_EMPTY>('/api/v1/runtime');

    expect(view.status).toBe('EMPTY');
    expect(calls.map((c) => c.path)).toEqual(['/api/session', '/api/v1/runtime']);
    expect(calls[0].token).toBeNull();
    expect(calls[1].token).toBe('token-a');
  });

  it('reuses the in-memory token across requests (single bootstrap)', async () => {
    const { calls, fetchImpl } = mockFetch([
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse(RUNTIME_EMPTY),
      () => jsonResponse(RUNTIME_EMPTY),
    ]);
    const client = createApiClient(fetchImpl);

    await client.get('/api/v1/runtime');
    await client.get('/api/v1/agents');

    expect(calls.filter((c) => c.path === '/api/session')).toHaveLength(1);
    expect(calls[2]).toEqual({ path: '/api/v1/agents', token: 'token-a' });
  });

  it('never persists the token to localStorage or sessionStorage', async () => {
    const { fetchImpl } = mockFetch([
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse(RUNTIME_EMPTY),
    ]);
    const client = createApiClient(fetchImpl);

    await client.get('/api/v1/runtime');

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});

describe('api client 401 handling', () => {
  it('re-bootstraps exactly once on 401 and retries with the new token', async () => {
    const { calls, fetchImpl } = mockFetch([
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse({ error: 'Unauthorized' }, 401),
      () => jsonResponse({ token: 'token-b' }),
      () => jsonResponse(RUNTIME_EMPTY),
    ]);
    const client = createApiClient(fetchImpl);

    const view = await client.get<typeof RUNTIME_EMPTY>('/api/v1/runtime');

    expect(view.status).toBe('EMPTY');
    expect(calls).toEqual([
      { path: '/api/session', token: null },
      { path: '/api/v1/runtime', token: 'token-a' },
      { path: '/api/session', token: null },
      { path: '/api/v1/runtime', token: 'token-b' },
    ]);
  });

  it('surfaces SESSION_UNAVAILABLE after a second 401 without further retries', async () => {
    const { calls, fetchImpl } = mockFetch([
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse({ error: 'Unauthorized' }, 401),
      () => jsonResponse({ token: 'token-b' }),
      () => jsonResponse({ error: 'Unauthorized' }, 401),
      () => jsonResponse(RUNTIME_EMPTY), // must never be reached
    ]);
    const client = createApiClient(fetchImpl);

    await expect(client.get('/api/v1/runtime')).rejects.toBeInstanceOf(SessionUnavailableError);
    expect(calls).toHaveLength(4);
  });
});

describe('api client error safety', () => {
  it('maps network failures to a display-safe message', async () => {
    const fetchImpl = (async () => {
      throw new TypeError('fetch failed: connect ECONNREFUSED 127.0.0.1:8787');
    }) as unknown as typeof fetch;
    const client = createApiClient(fetchImpl);

    await expect(client.get('/api/v1/runtime')).rejects.toMatchObject({
      name: 'ApiRequestError',
      message: 'API unreachable',
    });
  });

  it('does not propagate raw server error bodies to callers', async () => {
    const { fetchImpl } = mockFetch([
      () => jsonResponse({ token: 'token-a' }),
      () =>
        jsonResponse(
          { detail: 'sqlite3.OperationalError: /secret/dir/state.db is corrupt' },
          500,
        ),
    ]);
    const client = createApiClient(fetchImpl);

    const failure = await client.get('/api/v1/runtime').catch((err: unknown) => err);
    expect(failure).toBeInstanceOf(ApiRequestError);
    expect((failure as ApiRequestError).message).toBe('API request failed (HTTP 500)');
    expect((failure as ApiRequestError).message).not.toContain('state.db');
  });
});
