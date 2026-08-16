import { describe, expect, it, vi } from 'vitest';

import {
  ApiActionError,
  ApiRequestError,
  createApiClient,
  SessionUnavailableError,
} from './client';
import { resolveApiUrl } from './url';

type FetchCall = { path: string; token: string | null };

const packagedApiUrl = (path: string) =>
  resolveApiUrl(path, false, { protocol: 'http:', hostname: 'tauri.localhost' });

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

  it('routes packaged session bootstrap and an authoritative view to loopback Core', async () => {
    const { calls, fetchImpl } = mockFetch([
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse(RUNTIME_EMPTY),
    ]);
    const client = createApiClient(fetchImpl, packagedApiUrl);

    await client.get('/api/v1/runtime');

    expect(calls).toEqual([
      { path: 'http://127.0.0.1:8787/api/session', token: null },
      { path: 'http://127.0.0.1:8787/api/v1/runtime', token: 'token-a' },
    ]);
  });

  it('retries only connection failures while the packaged sidecar is starting', async () => {
    const { calls, fetchImpl } = mockFetch([
      () => {
        throw new TypeError('connection refused');
      },
      () => {
        throw new TypeError('connection refused');
      },
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse(RUNTIME_EMPTY),
    ]);
    const wait = vi.fn(async () => {});
    const client = createApiClient(fetchImpl, (path) => path, {
      maxAttempts: 3,
      wait,
    });

    const view = await client.get<typeof RUNTIME_EMPTY>('/api/v1/runtime');

    expect(view.status).toBe('EMPTY');
    expect(calls.map((call) => call.path)).toEqual([
      '/api/session',
      '/api/session',
      '/api/session',
      '/api/v1/runtime',
    ]);
    expect(wait).toHaveBeenCalledTimes(2);
  });

  it('does not retry an HTTP failure from the session endpoint', async () => {
    const { calls, fetchImpl } = mockFetch([
      () => jsonResponse({ error: 'unavailable' }, 503),
      () => jsonResponse({ token: 'must-not-be-used' }),
    ]);
    const wait = vi.fn(async () => {});
    const client = createApiClient(fetchImpl, (path) => path, {
      maxAttempts: 3,
      wait,
    });

    await expect(client.bootstrap()).rejects.toMatchObject({
      name: 'ApiRequestError',
      status: 503,
    });
    expect(calls).toEqual([{ path: '/api/session', token: null }]);
    expect(wait).not.toHaveBeenCalled();
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

  it('captures a backend reason_code from a failed GET only when it is a stable machine token', async () => {
    const { fetchImpl } = mockFetch([
      () => jsonResponse({ token: 'token-a' }),
      () =>
        jsonResponse(
          {
            schema_version: 'r4-product-evidence-1',
            status: 'NOT_FOUND',
            reason_code: 'EVIDENCE_EVENT_NOT_FOUND',
            event_id: 'evt-missing',
          },
          404,
        ),
    ]);
    const client = createApiClient(fetchImpl);

    const failure = await client.get('/api/v1/evidence/evt-missing').catch((err: unknown) => err);
    expect(failure).toBeInstanceOf(ApiRequestError);
    expect((failure as ApiRequestError).status).toBe(404);
    expect((failure as ApiRequestError).reasonCode).toBe('EVIDENCE_EVENT_NOT_FOUND');
  });

  it('drops non-machine reason_code values from a failed GET, and never passes the raw body through', async () => {
    const { fetchImpl } = mockFetch([
      () => jsonResponse({ token: 'token-a' }),
      () =>
        jsonResponse(
          {
            detail: 'sqlite3.OperationalError: /secret/dir/state.db token=deadbeef',
            reason_code: '../../etc/passwd',
          },
          503,
        ),
    ]);
    const client = createApiClient(fetchImpl);

    const failure = await client.get('/api/v1/runtime').catch((err: unknown) => err);
    expect(failure).toBeInstanceOf(ApiRequestError);
    expect((failure as ApiRequestError).reasonCode).toBeNull();
    const rendered = `${(failure as ApiRequestError).message} ${(failure as ApiRequestError).reasonCode}`;
    expect(rendered).not.toContain('state.db');
    expect(rendered).not.toContain('sqlite3');
    expect(rendered).not.toContain('deadbeef');
    expect(rendered).not.toContain('/etc/passwd');
  });

  it('keeps the 401 contract for GET even when capturing failure bodies: one re-bootstrap, second 401 is SESSION_UNAVAILABLE', async () => {
    const { calls, fetchImpl } = mockFetch([
      () => jsonResponse({ token: 'token-a' }), // initial session
      () => jsonResponse({}, 401), // first protected request → 401
      () => jsonResponse({ token: 'token-b' }), // re-bootstrap
      () => jsonResponse({}, 401), // retry → 401 again
    ]);
    const client = createApiClient(fetchImpl);

    const failure = await client.get('/api/v1/evidence/evt-x').catch((err: unknown) => err);
    expect(failure).toBeInstanceOf(SessionUnavailableError);
    // session + 401 + re-bootstrap + 401 — never a third request.
    expect(calls).toHaveLength(4);
    expect(calls[3].token).toBe('token-b');
  });
});

describe('api client mutation POST', () => {
  type PostCall = { path: string; method: string; token: string | null; body: unknown; contentType: string | null };

  function mockPostFetch(handlers: Array<() => Response | Promise<Response>>) {
    const calls: PostCall[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      calls.push({
        path: String(input),
        method: init?.method ?? 'GET',
        token: headers.get('X-Session-Token'),
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : null,
        contentType: headers.get('Content-Type'),
      });
      const handler = handlers.length > 1 ? handlers.shift()! : handlers[0];
      if (!handler) throw new Error('unexpected fetch call');
      return handler();
    });
    return { calls, fetchImpl: fetchImpl as unknown as typeof fetch };
  }

  const ACTION_OK = {
    schema_version: 'r4-p8-action-1',
    action: 'APPROVE_ONCE',
    supervision_session_id: 'ssn-1',
    status: 'APPROVED',
    reason_code: 'SUPERVISION_APPROVED_ONCE',
    consumed: true,
    evidence_refs: ['evt-1'],
  };

  it('sends exactly one POST with only the caller body and the session token', async () => {
    const { calls, fetchImpl } = mockPostFetch([
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse(ACTION_OK),
    ]);
    const client = createApiClient(fetchImpl);

    const ref = 'a'.repeat(64);
    const result = await client.post<typeof ACTION_OK>(
      '/api/v1/supervision/ssn-1/approve-once',
      { action_ref: ref },
    );

    expect(result.status).toBe('APPROVED');
    expect(calls).toHaveLength(2);
    const post = calls[1];
    expect(post.method).toBe('POST');
    expect(post.path).toBe('/api/v1/supervision/ssn-1/approve-once');
    expect(post.token).toBe('token-a');
    expect(post.contentType).toBe('application/json');
    expect(post.body).toEqual({ action_ref: ref });
    expect(Object.keys(post.body as object)).toEqual(['action_ref']);
  });

  it('routes a packaged mutation to loopback Core without changing its authority payload', async () => {
    const { calls, fetchImpl } = mockPostFetch([
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse(ACTION_OK),
    ]);
    const client = createApiClient(fetchImpl, packagedApiUrl);
    const ref = 'a'.repeat(64);

    await client.post('/api/v1/supervision/ssn-1/approve-once', { action_ref: ref });

    expect(calls).toEqual([
      {
        path: 'http://127.0.0.1:8787/api/session',
        method: 'GET',
        token: null,
        body: null,
        contentType: null,
      },
      {
        path: 'http://127.0.0.1:8787/api/v1/supervision/ssn-1/approve-once',
        method: 'POST',
        token: 'token-a',
        body: { action_ref: ref },
        contentType: 'application/json',
      },
    ]);
  });

  it('re-bootstraps at most once on 401, then retries the mutation once', async () => {
    const { calls, fetchImpl } = mockPostFetch([
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse({ error: 'Unauthorized' }, 401),
      () => jsonResponse({ token: 'token-b' }),
      () => jsonResponse(ACTION_OK),
    ]);
    const client = createApiClient(fetchImpl);

    await client.post('/api/v1/supervision/ssn-1/reject', { action_ref: 'b'.repeat(64) });

    expect(calls.map((c) => [c.method, c.path, c.token])).toEqual([
      ['GET', '/api/session', null],
      ['POST', '/api/v1/supervision/ssn-1/reject', 'token-a'],
      ['GET', '/api/session', null],
      ['POST', '/api/v1/supervision/ssn-1/reject', 'token-b'],
    ]);
  });

  it('surfaces SESSION_UNAVAILABLE after a second 401 without further retries', async () => {
    const { calls, fetchImpl } = mockPostFetch([
      () => jsonResponse({ token: 'token-a' }),
      () => jsonResponse({ error: 'Unauthorized' }, 401),
      () => jsonResponse({ token: 'token-b' }),
      () => jsonResponse({ error: 'Unauthorized' }, 401),
      () => jsonResponse(ACTION_OK), // must never be reached
    ]);
    const client = createApiClient(fetchImpl);

    await expect(
      client.post('/api/v1/supervision/ssn-1/reject', { action_ref: 'b'.repeat(64) }),
    ).rejects.toBeInstanceOf(SessionUnavailableError);
    expect(calls).toHaveLength(4);
  });

  it('carries only the stable reason_code from a failure body', async () => {
    const { fetchImpl } = mockPostFetch([
      () => jsonResponse({ token: 'token-a' }),
      () =>
        jsonResponse(
          {
            schema_version: 'r4-p8-action-1',
            action: 'APPROVE_ONCE',
            supervision_session_id: 'ssn-1',
            status: 'UNCHANGED',
            reason_code: 'SUPERVISION_ACTION_STALE',
            consumed: false,
            evidence_refs: [],
          },
          409,
        ),
    ]);
    const client = createApiClient(fetchImpl);

    const failure = await client
      .post('/api/v1/supervision/ssn-1/approve-once', { action_ref: 'a'.repeat(64) })
      .catch((err: unknown) => err);
    expect(failure).toBeInstanceOf(ApiActionError);
    expect((failure as ApiActionError).status).toBe(409);
    expect((failure as ApiActionError).reasonCode).toBe('SUPERVISION_ACTION_STALE');
    expect((failure as ApiActionError).message).toBe('Action failed (HTTP 409)');
  });

  it('never leaks raw backend error text, paths, or tokens from a failure body', async () => {
    const { fetchImpl } = mockPostFetch([
      () => jsonResponse({ token: 'token-a' }),
      () =>
        jsonResponse(
          {
            detail: 'sqlite3.OperationalError: /secret/dir/state.db token=deadbeef',
            reason_code: '../../etc/passwd',
          },
          503,
        ),
    ]);
    const client = createApiClient(fetchImpl);

    const failure = await client
      .post('/api/v1/supervision/ssn-1/approve-once', { action_ref: 'a'.repeat(64) })
      .catch((err: unknown) => err);
    expect(failure).toBeInstanceOf(ApiActionError);
    // A non-allowlisted reason_code is dropped, not surfaced.
    expect((failure as ApiActionError).reasonCode).toBeNull();
    const rendered = `${(failure as ApiActionError).message} ${(failure as ApiActionError).reasonCode}`;
    expect(rendered).not.toContain('state.db');
    expect(rendered).not.toContain('sqlite3');
    expect(rendered).not.toContain('deadbeef');
    expect(rendered).not.toContain('/etc/passwd');
  });

  it('maps network failures during mutation to a display-safe message', async () => {
    const fetchImpl = (async () => {
      throw new TypeError('fetch failed: connect ECONNREFUSED 127.0.0.1:8787');
    }) as unknown as typeof fetch;
    const client = createApiClient(fetchImpl);

    await expect(
      client.post('/api/v1/supervision/ssn-1/reject', { action_ref: 'b'.repeat(64) }),
    ).rejects.toMatchObject({ name: 'ApiRequestError', message: 'API unreachable' });
  });
});
