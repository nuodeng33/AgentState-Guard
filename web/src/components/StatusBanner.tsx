import { useEffect, useState } from 'react';

import {
  apiClient,
  ApiRequestError,
  SessionUnavailableError,
  type ApiClient,
} from '../api/client';
import { useT } from '../i18n/I18nProvider';

type ReadinessPhase =
  | { kind: 'checking' }
  | { kind: 'ready' }
  | { kind: 'degraded'; reason: 'http-503' | 'failed' }
  | { kind: 'session-unavailable' };

/**
 * Bounded readiness banner driven by GET /api/readiness only.
 * The legacy wide /api/status endpoint is never used for readiness.
 * SESSION_UNAVAILABLE is a stable machine token and is never translated.
 */
export function StatusBanner({ client = apiClient }: { client?: ApiClient }) {
  const [phase, setPhase] = useState<ReadinessPhase>({ kind: 'checking' });
  const t = useT();

  useEffect(() => {
    let cancelled = false;
    client
      .get('/api/readiness')
      .then(() => {
        if (!cancelled) setPhase({ kind: 'ready' });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof SessionUnavailableError) {
          setPhase({ kind: 'session-unavailable' });
        } else if (err instanceof ApiRequestError && err.status === 503) {
          setPhase({ kind: 'degraded', reason: 'http-503' });
        } else {
          setPhase({ kind: 'degraded', reason: 'failed' });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  if (phase.kind === 'checking' || phase.kind === 'ready') {
    return null;
  }
  if (phase.kind === 'session-unavailable') {
    return (
      <div className="status-banner status-banner-bad" role="alert">
        SESSION_UNAVAILABLE — {t('banner.sessionUnavailable')}
      </div>
    );
  }
  const detail =
    phase.reason === 'http-503' ? t('banner.readinessDegraded') : t('banner.readinessFailed');
  return (
    <div className="status-banner status-banner-bad" role="alert">
      {detail}. {t('banner.degradedSuffix')}
    </div>
  );
}
