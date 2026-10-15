import type { ReactNode } from 'react';

import type { ViewState } from '../api/useR4View';
import { useT } from '../i18n/I18nProvider';

/**
 * Uniform LOADING / API-ERROR / SESSION_UNAVAILABLE gate for the four views.
 * Error text comes from the API client and is always display-safe.
 * SESSION_UNAVAILABLE itself is a stable machine token and is never translated.
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
  const t = useT();
  if (state.phase === 'loading') {
    return (
      <div className="view-state" role="status">
        <span className="spinner" aria-hidden="true" />
        <p>{t('view.loading', { label })}</p>
      </div>
    );
  }
  if (state.phase === 'session-unavailable') {
    return (
      <div className="view-state view-state-bad" role="alert">
        <p className="view-state-title">SESSION_UNAVAILABLE</p>
        <p>{t('view.sessionUnavailableBody')}</p>
        <button type="button" className="btn" onClick={state.reload}>
          {t('view.retry')}
        </button>
      </div>
    );
  }
  if (state.phase === 'api-error') {
    return (
      <div className="view-state view-state-bad" role="alert">
        <p className="view-state-title">{t('view.unavailable', { label })}</p>
        <p>{state.error ?? t('view.apiFailed')}</p>
        <button type="button" className="btn" onClick={state.reload}>
          {t('view.retry')}
        </button>
      </div>
    );
  }
  return <>{children(state.data as T)}</>;
}
