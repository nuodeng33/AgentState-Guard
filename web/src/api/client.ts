/**
 * Session-scoped API client for the R4-P8 UI.
 *
 * Security rules (frozen contract, docs/phases/08-ui-backend-contract.md):
 * - The session token is fetched from GET /api/session once, held in memory
 *   only, and attached to every protected request as X-Session-Token.
 * - The token is never written to localStorage/sessionStorage/disk and never
 *   logged.
 * - On 401 the client re-bootstraps the session exactly once per request;
 *   a second 401 surfaces SESSION_UNAVAILABLE. There is no retry loop.
 * - A packaged cold start retries only connection failures while the bundled
 *   sidecar becomes ready. HTTP/auth failures are never startup-retried.
 * - Errors thrown by this client carry generic, display-safe messages only;
 *   raw server/exception text is never propagated to the UI.
 */

import { resolveApiUrl, type ApiUrlResolver } from './url';

/** Thrown when a session cannot be established even after one re-bootstrap. */
export class SessionUnavailableError extends Error {
  readonly code = 'SESSION_UNAVAILABLE' as const;
  constructor() {
    super('SESSION_UNAVAILABLE');
    this.name = 'SessionUnavailableError';
  }
}

/** Thrown for network failures and non-401 HTTP errors. Message is display-safe. */
export class ApiRequestError extends Error {
  readonly status: number | null;
  /**
   * Safe reason_code atom captured from the failure body when present
   * (same allowlist as mutations). Never raw server text.
   */
  readonly reasonCode: string | null;
  constructor(message: string, status: number | null = null, reasonCode: string | null = null) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.reasonCode = reasonCode;
  }
}

/** Stable machine-readable failure codes are the only detail a mutation may surface. */
const STABLE_REASON_CODE = /^[A-Z0-9_]{1,64}$/;

/**
 * Thrown for failed mutations (non-401 HTTP status). Carries the HTTP status,
 * the backend's stable reason_code, and optionally a sanitized failure DTO
 * (e.g. the product-ai-advisory-1 body on analyze 503s). Raw response text is
 * never retained.
 */
export class ApiActionError extends Error {
  readonly status: number;
  readonly reasonCode: string | null;
  /** Sanitized machine DTO when the failure body was parseable JSON; else null. */
  readonly dto: unknown | null;
  constructor(status: number, reasonCode: string | null = null, dto: unknown = null) {
    super(`Action failed (HTTP ${status})`);
    this.name = 'ApiActionError';
    this.status = status;
    this.reasonCode = reasonCode;
    this.dto = dto;
  }
}

export interface ApiClient {
  /** Establish (or re-establish) the in-memory session token. */
  bootstrap(): Promise<void>;
  /** GET a protected API path with the session token attached. */
  get<T>(path: string): Promise<T>;
  /**
   * POST a JSON mutation with the session token attached. Sent exactly once
   * per call; the only retry is the shared single 401 re-bootstrap.
   */
  post<T>(path: string, body: unknown): Promise<T>;
}

interface SessionResponse {
  token?: unknown;
}

interface SessionBootstrapRetryPolicy {
  maxAttempts: number;
  wait(): Promise<void>;
}

const DEFAULT_SESSION_BOOTSTRAP_RETRY: SessionBootstrapRetryPolicy = {
  // One immediate attempt plus at most 60 bounded one-second waits in a
  // packaged build. Development fails immediately through the Vite proxy.
  maxAttempts: import.meta.env.DEV ? 1 : 61,
  wait: () => new Promise((resolve) => setTimeout(resolve, 1_000)),
};

export function createApiClient(
  fetchImpl?: typeof fetch,
  urlResolver: ApiUrlResolver = resolveApiUrl,
  bootstrapRetry: SessionBootstrapRetryPolicy = DEFAULT_SESSION_BOOTSTRAP_RETRY,
): ApiClient {
  // In-memory only. Never persisted, never logged.
  let token: string | null = null;
  let bootstrapInFlight: Promise<string> | null = null;
  // Resolve fetch at call time so late-bound environments (tests, Tauri
  // injection) work; production uses the global fetch.
  const callFetch: typeof fetch =
    fetchImpl ?? ((input, init) => fetch(input, init));

  function bootstrapSession(): Promise<string> {
    if (!bootstrapInFlight) {
      bootstrapInFlight = (async () => {
        let response: Response | null = null;
        for (let attempt = 1; attempt <= bootstrapRetry.maxAttempts; attempt += 1) {
          try {
            response = await callFetch(urlResolver('/api/session'), {
              headers: { Accept: 'application/json' },
            });
            break;
          } catch {
            if (attempt === bootstrapRetry.maxAttempts) {
              throw new ApiRequestError('API unreachable');
            }
            await bootstrapRetry.wait();
          }
        }
        if (!response) {
          throw new ApiRequestError('API unreachable');
        }
        if (!response.ok) {
          throw new ApiRequestError(`Session bootstrap failed (HTTP ${response.status})`, response.status);
        }
        const body = (await response.json()) as SessionResponse;
        if (typeof body.token !== 'string' || body.token.length === 0) {
          throw new ApiRequestError('Session bootstrap failed');
        }
        token = body.token;
        return body.token;
      })().finally(() => {
        bootstrapInFlight = null;
      });
    }
    return bootstrapInFlight;
  }

  async function bootstrap(): Promise<void> {
    await bootstrapSession();
  }

  async function request(path: string, sessionToken: string): Promise<Response> {
    try {
      return await callFetch(urlResolver(path), {
        headers: { Accept: 'application/json', 'X-Session-Token': sessionToken },
      });
    } catch {
      throw new ApiRequestError('API unreachable');
    }
  }

  async function get<T>(path: string): Promise<T> {
    const sessionToken = token ?? (await bootstrapSession());
    let response = await request(path, sessionToken);
    if (response.status === 401) {
      // Exactly one re-bootstrap + one retry. No further retries.
      token = null;
      const freshToken = await bootstrapSession();
      response = await request(path, freshToken);
      if (response.status === 401) {
        throw new SessionUnavailableError();
      }
    }
    if (!response.ok) {
      const failure = await readFailureBody(response);
      throw new ApiRequestError(
        `API request failed (HTTP ${response.status})`,
        response.status,
        failure.reasonCode,
      );
    }
    return (await response.json()) as T;
  }

  async function send(path: string, sessionToken: string, body: unknown): Promise<Response> {
    try {
      return await callFetch(urlResolver(path), {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-Session-Token': sessionToken,
        },
        body: JSON.stringify(body),
      });
    } catch {
      throw new ApiRequestError('API unreachable');
    }
  }

  /**
   * Parse a failure body once: keep the JSON as an opaque DTO holder and
   * extract the stable reason_code beside it. Callers that render DTO fields
   * must stay on the same machine-token discipline as success; raw text is
   * never surfaced by the client itself.
   */
  async function readFailureBody(response: Response): Promise<{ reasonCode: string | null; dto: unknown }> {
    try {
      const body = (await response.json()) as unknown;
      return { reasonCode: extractReasonCode(body), dto: body };
    } catch {
      return { reasonCode: null, dto: null };
    }
  }

  /** Extract only the stable reason_code atom; arbitrary text is dropped. */
  function extractReasonCode(body: unknown): string | null {
    const code = (body as { reason_code?: unknown } | null)?.reason_code;
    return typeof code === 'string' && STABLE_REASON_CODE.test(code) ? code : null;
  }

  async function post<T>(path: string, body: unknown): Promise<T> {
    const sessionToken = token ?? (await bootstrapSession());
    let response = await send(path, sessionToken, body);
    if (response.status === 401) {
      // Same bounded policy as reads: one re-bootstrap + one retry, never a loop.
      token = null;
      const freshToken = await bootstrapSession();
      response = await send(path, freshToken, body);
      if (response.status === 401) {
        throw new SessionUnavailableError();
      }
    }
    if (!response.ok) {
      const failure = await readFailureBody(response);
      throw new ApiActionError(response.status, failure.reasonCode, failure.dto);
    }
    return (await response.json()) as T;
  }

  return { bootstrap, get, post };
}

/** Shared singleton for the running app. Tests build isolated clients via createApiClient. */
export const apiClient = createApiClient();
