/**
 * Mutation/read failure → display-safe text.
 *
 * Only the backend's stable reason code (and the HTTP status) is ever
 * rendered; raw error text never crosses into the UI. Everything falls back
 * to plain "HTTP <status>" when no whitelisted code was captured.
 */

import { ApiActionError, ApiRequestError, SessionUnavailableError } from './client';

export function failureText(err: unknown): string {
  if (err instanceof SessionUnavailableError) return 'SESSION_UNAVAILABLE';
  if (err instanceof ApiActionError) {
    return err.reasonCode ?? `HTTP ${err.status}`;
  }
  if (err instanceof ApiRequestError) {
    return err.reasonCode ?? (err.status !== null ? `HTTP ${err.status}` : 'API unreachable');
  }
  return 'API request failed';
}
