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

export function useR4View<T>(view: ViewName, client: ApiClient = apiClient): ViewState<T> {
  const [state, setState] = useState<Omit<ViewState<T>, 'reload' | 'refreshing'>>({
    phase: 'loading',
    data: null,
    error: null,
  });
  const [inFlight, setInFlight] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false, failures = 0;
    let timer: ReturnType<typeof window.setTimeout> | undefined;
    const schedule = () => {
      if (!cancelled) timer = window.setTimeout(() => {
        if (document.visibilityState === 'visible') void read();
        else schedule();
      }, failures ? Math.min(30_000, 5_000 * 2 ** failures) : 5_000);
    };
    const read = async () => {
      setInFlight(true);
      setState(prev => prev.data === null ? { phase: 'loading', data: null, error: null } : prev);
      try {
        const data = await client.get<T>(`/api/v1/${view}`);
        if (cancelled) return;
        failures = 0;
        setState({ phase: 'ready', data, error: null });
      } catch (err: unknown) {
        if (cancelled) return;
        failures = Math.min(failures + 1, 3);
        if (err instanceof SessionUnavailableError) {
          setState({ phase: 'session-unavailable', data: null, error: 'SESSION_UNAVAILABLE' });
        } else {
          const message = err instanceof ApiRequestError ? err.message : 'API request failed';
          setState({ phase: 'api-error', data: null, error: message });
        }
      } finally {
        if (!cancelled) { setInFlight(false); schedule(); }
      }
    };
    void read();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [view, client, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, refreshing: inFlight && state.data !== null, reload };
}
