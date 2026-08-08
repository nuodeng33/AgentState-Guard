import type { ReactNode } from 'react';

import type { ViewState } from '../api/useR4View';

/**
 * Uniform LOADING / API-ERROR / SESSION_UNAVAILABLE gate for the four views.
 * Error text comes from the API client and is always display-safe.
 */
export function ViewGate<T>({
  state,
  label,
  children,
}: {
  state: ViewState<T>;
  label: string;
  children: (data: T) => ReactNode;
}) {
  if (state.phase === 'loading') {
    return (
      <div className="view-state" role="status">
        <span className="spinner" aria-hidden="true" />
        <p>Loading {label}…</p>
      </div>
    );
  }
  if (state.phase === 'session-unavailable') {
    return (
      <div className="view-state view-state-bad" role="alert">
        <p className="view-state-title">SESSION_UNAVAILABLE</p>
        <p>
          A session with the local backend could not be established. No authoritative data
          is shown.
        </p>
        <button type="button" className="btn" onClick={state.reload}>
          Retry
        </button>
      </div>
    );
  }
  if (state.phase === 'api-error') {
    return (
      <div className="view-state view-state-bad" role="alert">
        <p className="view-state-title">{label} unavailable</p>
        <p>{state.error ?? 'API request failed'}</p>
        <button type="button" className="btn" onClick={state.reload}>
          Retry
        </button>
      </div>
    );
  }
  return <>{children(state.data as T)}</>;
}
