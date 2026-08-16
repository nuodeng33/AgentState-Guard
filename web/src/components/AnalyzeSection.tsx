/**
 * Contextual "Analyze Current Environment" surface (Environment / Supervision).
 *
 * POST /api/ai/analyze exactly {} — the backend builds the sanitized context
 * itself from its own projections; the frontend sends no facts. The advisory
 * result (AiAdvisoryPanel) never upgrades deterministic state: it renders
 * severity/summary/checks strictly as-is beside the authority projection.
 */

import { useState } from 'react';

import { ApiActionError, type ApiClient } from '../api/client';
import { failureText } from '../api/errorText';
import { aiAnalyze } from '../api/product';
import type { AiAdvisory } from '../api/types';
import { AiAdvisoryPanel } from './AiAdvisoryPanel';

export function AnalyzeSection({ client }: { client: ApiClient }) {
  const [advisory, setAdvisory] = useState<AiAdvisory | null>(null);
  const [phase, setPhase] = useState<'idle' | 'loading' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  const run = () => {
    setPhase('loading');
    setError(null);
    aiAnalyze(client).then(
      (result) => {
        setAdvisory(result);
        setPhase('idle');
      },
      (err: unknown) => {
        // 503s still carry a sanitized advisory DTO (e.g. AI_PROVIDER_UNAVAILABLE);
        // render that DTO as-is instead of collapsing it into a generic error.
        if (err instanceof ApiActionError && err.dto && isAdvisoryDto(err.dto)) {
          setAdvisory(err.dto);
          setPhase('idle');
          return;
        }
        setPhase('error');
        setError(failureText(err));
      },
    );
  };

  return <AiAdvisoryPanel advisory={advisory} phase={phase} error={error} onAnalyze={run} />;
}

/** Server 503 bodies for analyze are product-ai-advisory-1 DTOs; never raw text. */
function isAdvisoryDto(value: unknown): value is AiAdvisory {
  if (value === null || typeof value !== 'object') return false;
  const dto = value as Partial<AiAdvisory>;
  return (
    dto.schema_version === 'product-ai-advisory-1' &&
    typeof dto.status === 'string' &&
    typeof dto.reason_code === 'string'
  );
}
