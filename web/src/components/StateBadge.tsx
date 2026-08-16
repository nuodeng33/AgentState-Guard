import type { ReactNode } from 'react';

import type { PolicyDecision, TrustedBaselineStatus, ViewStatus } from '../api/types';

/**
 * Status badge with an explicit, conservative tone vocabulary.
 *
 * Color is a secondary channel only; the label always carries the meaning.
 * `unknown` is visually distinct from every affirmative state and is never
 * rendered as safe/ok.
 */
export type BadgeTone = 'ok' | 'warn' | 'bad' | 'neutral' | 'info' | 'unknown';

export function StateBadge({ label, tone }: { label: ReactNode; tone: BadgeTone }) {
  return <span className={`badge badge-${tone}`}>{label}</span>;
}

export function viewStatusTone(status: ViewStatus): BadgeTone {
  switch (status) {
    case 'AVAILABLE':
      return 'ok';
    case 'EMPTY':
      return 'neutral';
    case 'UNKNOWN':
      return 'unknown';
    case 'UNREACHABLE':
    case 'DEGRADED':
      return 'bad';
  }
}

export function availabilityTone(availability: string): BadgeTone {
  switch (availability) {
    case 'AVAILABLE':
      return 'ok';
    case 'UNREACHABLE':
      return 'bad';
    default:
      return 'unknown';
  }
}

export function policyTone(decision: string | null): BadgeTone {
  switch (decision as PolicyDecision | null) {
    case 'ALLOW':
      return 'ok';
    case 'REVIEW':
      return 'warn';
    case 'BLOCK':
      return 'bad';
    case 'UNKNOWN':
      return 'unknown';
    default:
      return 'neutral';
  }
}

export function recoveryLevelTone(level: string): BadgeTone {
  switch (level) {
    case 'R3':
      return 'ok';
    case 'R2':
    case 'R1':
      return 'warn';
    default:
      return 'bad';
  }
}

export function baselineTone(status: string): BadgeTone {
  switch (status as TrustedBaselineStatus) {
    case 'TRUSTED':
      return 'ok';
    case 'RETIRED':
      return 'warn';
    case 'REVOKED':
      return 'bad';
    default:
      return 'neutral';
  }
}

export function recoveryCoverageTone(status: string): BadgeTone {
  switch (status) {
    case 'COMPLETE':
      return 'ok';
    case 'INSUFFICIENT':
    case 'MISSING':
      return 'warn';
    default:
      return 'bad';
  }
}
