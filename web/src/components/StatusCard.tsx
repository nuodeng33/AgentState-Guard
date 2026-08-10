import type { ReactNode } from 'react';

import { StateBadge, type BadgeTone } from './StateBadge';

/**
 * Compact status tile for overview surfaces. The badge label is always a
 * verbatim machine state; the tile never invents a state of its own.
 */
export function StatusCard({
  label,
  badge,
  children,
}: {
  label: string;
  badge?: { label: string; tone: BadgeTone };
  children?: ReactNode;
}) {
  return (
    <section className="status-card">
      <p className="status-card-label">{label}</p>
      {badge && (
        <p className="status-card-badge">
          <StateBadge label={badge.label} tone={badge.tone} />
        </p>
      )}
      {children}
    </section>
  );
}
