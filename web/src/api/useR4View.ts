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
  reload: () => void;
}

export function useR4View<T>(view: ViewName, client: ApiClient = apiClient): ViewState<T> {
  const [state, setState] = useState<Omit<ViewState<T>, 'reload'>>({
    phase: 'loading',
    data: null,
    error: null,
  });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ phase: 'loading', data: null, error: null });
    client.get<T>(`/api/v1/${view}`).then(
      (data) => {
        if (!cancelled) setState({ phase: 'ready', data, error: null });
      },
      (err: unknown) => {
        if (cancelled) return;
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
  }, [view, client, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, reload };
}
