import { useEffect, useState } from 'react';

import {
  apiClient,
  ApiRequestError,
  SessionUnavailableError,
  type ApiClient,
} from '../api/client';

type ReadinessPhase =
  | { kind: 'checking' }
  | { kind: 'ready' }
  | { kind: 'degraded'; detail: string }
  | { kind: 'session-unavailable' };

/**
 * Bounded readiness banner driven by GET /api/readiness only.
 * The legacy wide /api/status endpoint is never used for readiness.
 */
export function StatusBanner({ client = apiClient }: { client?: ApiClient }) {
  const [phase, setPhase] = useState<ReadinessPhase>({ kind: 'checking' });

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
          setPhase({ kind: 'degraded', detail: 'Backend reports degraded readiness (HTTP 503)' });
        } else {
          setPhase({ kind: 'degraded', detail: 'Readiness check failed' });
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
        SESSION_UNAVAILABLE — cannot establish a session with the local backend.
      </div>
    );
  }
  return (
    <div className="status-banner status-banner-bad" role="alert">
      {phase.detail}. Authoritative views may be unavailable or degraded.
    </div>
  );
}
