import type { ReactNode } from 'react';

import type { ApiClient } from '../api/client';
import { StatusBanner } from './StatusBanner';

export type PageKey = 'runtime' | 'agents' | 'supervision' | 'recovery';

const NAV_ITEMS: Array<{ key: PageKey; label: string }> = [
  { key: 'runtime', label: 'Runtime' },
  { key: 'agents', label: 'Agents' },
  { key: 'supervision', label: 'Supervision' },
  { key: 'recovery', label: 'Recovery' },
];

/**
 * Desktop console shell: quiet sidebar navigation, content header, and the
 * bounded readiness banner. P8 authoritative views are the only primary nav.
 */
export function AppShell({
  page,
  onNavigate,
  client,
  title,
  children,
}: {
  page: PageKey;
  onNavigate: (page: PageKey) => void;
  client?: ApiClient;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <ShieldMark />
          <div>
            <div className="app-brand-name">AgentState Guard</div>
            <div className="app-brand-sub">R4 Operations Console</div>
          </div>
        </div>
        <nav className="app-nav" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`app-nav-item${item.key === page ? ' is-active' : ''}`}
              aria-current={item.key === page ? 'page' : undefined}
              onClick={() => onNavigate(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </aside>
      <div className="app-main">
        <StatusBanner client={client} />
        <header className="app-header">
          <h1 className="app-title">{title}</h1>
        </header>
        <main className="app-content">{children}</main>
      </div>
    </div>
  );
}

/** Simple geometric brand mark (no emoji). */
function ShieldMark() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true" className="brand-mark">
      <path
        d="M12 2.5 4.5 5.5v5.2c0 4.6 3.2 8.9 7.5 10.3 4.3-1.4 7.5-5.7 7.5-10.3V5.5L12 2.5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path d="M12 7v5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="12" cy="15.4" r="1.1" fill="currentColor" />
    </svg>
  );
}
