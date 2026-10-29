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
  /** Existing edit endpoints; same token and bounded 401 policy as POST. */
  patch?<T>(path: string, body: unknown): Promise<T>;
  put?<T>(path: string, body: unknown): Promise<T>;
  /** Authenticated event stream. Closing the reader never cancels execution. */
  stream?(path: string, signal?: AbortSignal): Promise<Response>;
}

interface SessionResponse {
  token?: unknown;
}

interface SessionBootstrapRetryPolicy {
  maxAttempts: number;
  wait(): Promise<void>;
}

const DEFAULT_SESSION_BOOTSTRAP_RETRY: SessionBootstrapRetryPolicy = {
  // Packaged startup retries are additionally bounded by a 12-second total
  // wall-clock budget. Development fails immediately through the Vite proxy.
  maxAttempts: import.meta.env.DEV ? 1 : 12,
  wait: () => new Promise((resolve) => setTimeout(resolve, 1_000)),
};

class ConnectionFailure extends ApiRequestError {}
type JsonResponse = { response: Response; body: unknown };
const REQUEST_TIMEOUT_MS = 5000;
const IO_POST_TIMEOUT_MS = 60000;
const IO_POST_PATH = /^\/api\/v1\/(?:recovery\/previews|protection\/scopes(?:\/[^/?#]+\/capture)?|device-link\/(?:enable|disable|network\/refresh))$/;
const BOOTSTRAP_BUDGET_MS = 12000;

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
  // A deadline must settle independently of fetch/reader AbortSignal support.
  // The same timer covers headers and body; cleanup never waits on a hung reader.
  async function deadline<T>(operation: () => Promise<T>, milliseconds: number, abort?: () => void): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([operation(), new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          reject(new ApiRequestError('API request timed out', null, 'API_REQUEST_TIMEOUT'));
          abort?.();
        }, Math.max(0, milliseconds));
      })]);
    } finally { if (timer !== undefined) clearTimeout(timer); }
  }
  async function fetchJson(input: RequestInfo | URL, init: RequestInit, timeoutMs = REQUEST_TIMEOUT_MS): Promise<JsonResponse> {
    const controller = new AbortController();
    let received = false;
    try {
      return await deadline(async () => {
        const response = await callFetch(input, { ...init, signal: controller.signal });
        received = true;
        // Authentication refusal needs no body. Never delay the one allowed 401 retry.
        if (response.status === 401) {
          void response.body?.cancel().catch(() => {});
          return { response, body: null };
        }
        let body: unknown;
        try { body = await response.json(); }
        catch {
          if (response.ok) throw new ApiRequestError('Invalid API response', response.status);
          body = null;
        }
        return { response, body };
      }, timeoutMs, () => controller.abort());
    } catch (error) {
      if (!received) throw new ConnectionFailure(
        error instanceof ApiRequestError ? error.message : 'API unreachable', null,
        error instanceof ApiRequestError ? error.reasonCode : null,
      );
      if (error instanceof ApiRequestError) throw error;
      throw new ApiRequestError('Invalid API response');
    }
  }

  function bootstrapSession(): Promise<string> {
    if (!bootstrapInFlight) {
      bootstrapInFlight = (async () => {
        const expires = Date.now() + BOOTSTRAP_BUDGET_MS;
        let result: JsonResponse | null = null;
        for (let attempt = 1; attempt <= bootstrapRetry.maxAttempts; attempt += 1) {
          const remaining = expires - Date.now();
          if (remaining <= 0) throw new ApiRequestError('API request timed out', null, 'API_REQUEST_TIMEOUT');
          try {
            result = await fetchJson(urlResolver('/api/session'), {
              headers: { Accept: 'application/json' },
            }, Math.min(REQUEST_TIMEOUT_MS, remaining));
            break;
          } catch (error) {
            // A received/malformed/stalled body is not a sidecar connection retry.
            if (!(error instanceof ConnectionFailure) || attempt === bootstrapRetry.maxAttempts) throw error;
            const waitBudget = expires - Date.now();
            if (waitBudget <= 0) throw error;
            await deadline(() => bootstrapRetry.wait(), waitBudget);
          }
        }
        if (!result) throw new ApiRequestError('API unreachable');
        const { response, body: value } = result;
        if (!response.ok) throw new ApiRequestError(`Session bootstrap failed (HTTP ${response.status})`, response.status);
        const body = value as SessionResponse | null;
        if (!body || typeof body.token !== 'string' || body.token.length === 0) throw new ApiRequestError('Session bootstrap failed');
        if (Date.now() >= expires) throw new ApiRequestError('API request timed out', null, 'API_REQUEST_TIMEOUT');
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

  async function request(path: string, sessionToken: string): Promise<JsonResponse> {
    try {
      return await fetchJson(urlResolver(path), {
        headers: { Accept: 'application/json', 'X-Session-Token': sessionToken },
      });
    } catch (error) {
      if (error instanceof ApiRequestError) throw error;
      throw new ApiRequestError('API unreachable');
    }
  }

  async function get<T>(path: string): Promise<T> {
    const sessionToken = token ?? (await bootstrapSession());
    let result = await request(path, sessionToken);
    let response = result.response;
    if (response.status === 401) {
      // Exactly one re-bootstrap + one retry. No further retries.
      token = null;
      const freshToken = await bootstrapSession();
      result = await request(path, freshToken); response = result.response;
      if (response.status === 401) {
        throw new SessionUnavailableError();
      }
    }
    if (!response.ok) {
      const failure = { reasonCode: extractReasonCode(result.body) };
      throw new ApiRequestError(
        `API request failed (HTTP ${response.status})`,
        response.status,
        failure.reasonCode,
      );
    }
    return result.body as T;
  }

  async function send(method: 'POST' | 'PATCH' | 'PUT', path: string, sessionToken: string, body: unknown): Promise<JsonResponse> {
    try {
      return await fetchJson(urlResolver(path), {
        method,
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-Session-Token': sessionToken,
        },
        body: JSON.stringify(body),
      }, method === 'POST' && IO_POST_PATH.test(path) ? IO_POST_TIMEOUT_MS : REQUEST_TIMEOUT_MS);
    } catch (error) {
      if (error instanceof ApiRequestError) throw error;
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

  async function mutate<T>(method: 'POST' | 'PATCH' | 'PUT', path: string, body: unknown): Promise<T> {
    const sessionToken = token ?? (await bootstrapSession());
    let result = await send(method, path, sessionToken, body);
    let response = result.response;
    if (response.status === 401) {
      // Same bounded policy as reads: one re-bootstrap + one retry, never a loop.
      token = null;
      const freshToken = await bootstrapSession();
      result = await send(method, path, freshToken, body); response = result.response;
      if (response.status === 401) {
        throw new SessionUnavailableError();
      }
    }
    if (!response.ok) {
      throw new ApiActionError(response.status, extractReasonCode(result.body), result.body);
    }
    return result.body as T;
  }

  async function stream(path: string, signal?: AbortSignal): Promise<Response> {
    const fetchStream = async (sessionToken: string) => {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      try {
        return await callFetch(urlResolver(path), {
          headers: { Accept: 'text/event-stream', 'X-Session-Token': sessionToken },
          signal,
        });
      } catch {
        if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
        throw new ApiRequestError('API unreachable');
      }
    };
    let response = await fetchStream(token ?? (await bootstrapSession()));
    if (response.status === 401) {
      await response.body?.cancel();
      token = null;
      response = await fetchStream(await bootstrapSession());
      if (response.status === 401) {
        await response.body?.cancel();
        throw new SessionUnavailableError();
      }
    }
    if (!response.ok) {
      const failure = await readFailureBody(response);
      throw new ApiRequestError(`API request failed (HTTP ${response.status})`, response.status, failure.reasonCode);
    }
    return response;
  }

  return { bootstrap, get, stream,
    post: <T>(path: string, body: unknown) => mutate<T>('POST', path, body),
    patch: <T>(path: string, body: unknown) => mutate<T>('PATCH', path, body),
    put: <T>(path: string, body: unknown) => mutate<T>('PUT', path, body),
  };
}

/** Shared singleton for the running app. Tests build isolated clients via createApiClient. */
export const apiClient = createApiClient();
