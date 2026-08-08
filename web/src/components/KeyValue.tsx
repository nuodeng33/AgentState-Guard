import type { ReactNode } from 'react';

/** Two-column key/value grid used across all four views. */
export function KeyValueGrid({ children }: { children: ReactNode }) {
  return <dl className="kv-grid">{children}</dl>;
}

export function KeyValue({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="kv-row">
      <dt className="kv-key">{k}</dt>
      <dd className="kv-value">{v}</dd>
    </div>
  );
}

/** Render a nullable string honestly: em dash when the backend provides nothing. */
export function orDash(value: string | null | undefined): ReactNode {
  if (value === null || value === undefined || value === '') {
    return <span className="muted">—</span>;
  }
  return value;
}
