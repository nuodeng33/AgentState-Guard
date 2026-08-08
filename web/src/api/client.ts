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
 * - Errors thrown by this client carry generic, display-safe messages only;
 *   raw server/exception text is never propagated to the UI.
 */

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
  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
  }
}

export interface ApiClient {
  /** Establish (or re-establish) the in-memory session token. */
  bootstrap(): Promise<void>;
  /** GET a protected API path with the session token attached. */
  get<T>(path: string): Promise<T>;
}

interface SessionResponse {
  token?: unknown;
}

export function createApiClient(fetchImpl?: typeof fetch): ApiClient {
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
        let response: Response;
        try {
          response = await callFetch('/api/session', { headers: { Accept: 'application/json' } });
        } catch {
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
      return await callFetch(path, {
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
      throw new ApiRequestError(`API request failed (HTTP ${response.status})`, response.status);
    }
    return (await response.json()) as T;
  }

  return { bootstrap, get };
}

/** Shared singleton for the running app. Tests build isolated clients via createApiClient. */
export const apiClient = createApiClient();
