/**
 * Data-loading hook for the four R4-P8 authoritative read views.
 *
 * Maps transport outcomes to explicit UI phases; error strings are
 * display-safe and never contain raw exception or server detail text.
 */

import { useCallback, useEffect, useState } from 'react';

import {
  apiClient,
  ApiRequestError,
  SessionUnavailableError,
  type ApiClient,
} from './client';
import type { ViewName } from './types';

export type ViewPhase = 'loading' | 'ready' | 'api-error' | 'session-unavailable';

export interface ViewState<T> {
  phase: ViewPhase;
  data: T | null;
  /** Display-safe message; never raw exception text. */
  error: string | null;
  /** True while a refetch runs with previously loaded data still on screen. */
  refreshing: boolean;
  reload: () => void;
}

export function useR4View<T>(
  view: ViewName,
  client: ApiClient = apiClient,
  query = '',
): ViewState<T> {
  const [state, setState] = useState<Omit<ViewState<T>, 'reload' | 'refreshing'>>({
    phase: 'loading',
    data: null,
    error: null,
  });
  const [inFlight, setInFlight] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setInFlight(true);
    // A refetch after an in-place action keeps the previous authoritative data
    // visible; only a cold start or a prior failure shows the loading gate.
    setState((prev) => (prev.data === null ? { phase: 'loading', data: null, error: null } : prev));
    client.get<T>(`/api/v1/${view}${query}`).then(
      (data) => {
        if (cancelled) return;
        setInFlight(false);
        setState({ phase: 'ready', data, error: null });
      },
      (err: unknown) => {
        if (cancelled) return;
        setInFlight(false);
        if (err instanceof SessionUnavailableError) {
          setState({ phase: 'session-unavailable', data: null, error: 'SESSION_UNAVAILABLE' });
        } else {
          const message = err instanceof ApiRequestError ? err.message : 'API request failed';
          setState({ phase: 'api-error', data: null, error: message });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [view, client, query, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, refreshing: inFlight && state.data !== null, reload };
}
