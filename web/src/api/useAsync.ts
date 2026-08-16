/**
 * Generic async data hook：与 useR4View 同一状态语义（loading/ready/api-error/
 * session-unavailable + 保留旧数据的 refreshing），用于非 /api/v1/{view} 的产品
 * endpoint（status / doctor / changes / devices / evidence …）。
 */

import { useCallback, useEffect, useState } from 'react';

import { ApiRequestError, SessionUnavailableError } from './client';
import type { ViewState } from './useR4View';

export function useAsync<T>(loader: () => Promise<T>, deps: readonly unknown[] = []): ViewState<T> {
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
    setState((prev) => (prev.data === null ? { phase: 'loading', data: null, error: null } : prev));
    loader().then(
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt, ...deps]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, refreshing: inFlight && state.data !== null, reload };
}
