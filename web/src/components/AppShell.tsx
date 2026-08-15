import type { ReactNode } from 'react';

import type { ApiClient } from '../api/client';
import { useT } from '../i18n/I18nProvider';
import type { MessageKey } from '../i18n/messages';
import { StatusBanner } from './StatusBanner';

export type PageKey =
  | 'home'
  | 'runtime'
  | 'agents'
  | 'supervision'
  | 'changes'
  | 'recovery'
  | 'devices'
  | 'aiMonitor'
  | 'settings';

const NAV_ITEMS: Array<{ key: PageKey; labelKey: MessageKey; icon: IconName }> = [
  { key: 'home', labelKey: 'nav.home', icon: 'home' },
  { key: 'runtime', labelKey: 'nav.runtime', icon: 'runtime' },
  { key: 'agents', labelKey: 'nav.agents', icon: 'agents' },
  { key: 'supervision', labelKey: 'nav.supervision', icon: 'supervision' },
  { key: 'changes', labelKey: 'nav.changes', icon: 'changes' },
  { key: 'recovery', labelKey: 'nav.recovery', icon: 'recovery' },
  { key: 'devices', labelKey: 'nav.devices', icon: 'devices' },
  { key: 'aiMonitor', labelKey: 'nav.aiMonitor', icon: 'aiMonitor' },
  { key: 'settings', labelKey: 'nav.settings', icon: 'settings' },
];

/**
 * Desktop product shell: sidebar navigation over the product IA, the bounded
 * readiness banner, and the content header. Navigation labels come from the
 * active locale; machine states inside pages stay verbatim.
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
  const t = useT();
  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <ShieldNodesMark />
          <div>
            <div className="app-brand-name">{t('app.brand.name')}</div>
            <div className="app-brand-sub">{t('app.brand.sub')}</div>
          </div>
        </div>
        <nav className="app-nav" aria-label={t('app.nav.primary')}>
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`app-nav-item${item.key === page ? ' is-active' : ''}`}
              aria-current={item.key === page ? 'page' : undefined}
              onClick={() => onNavigate(item.key)}
            >
              <NavIcon name={item.icon} />
              <span>{t(item.labelKey)}</span>
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

/**
 * Brand mark: shield = protection / bounded authority; the trace with three
 * state nodes = state + evidence continuity; the filled node is the
 * authoritative current state. Geometric vector only, never an emoji.
 */
function ShieldNodesMark() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true" className="brand-mark">
      <path
        d="M12 2.5 4.5 5.5v5.2c0 4.6 3.2 8.9 7.5 10.3 4.3-1.4 7.5-5.7 7.5-10.3V5.5L12 2.5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M8.5 9.5 12 13.2 15.5 9.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="8.5" cy="9.5" r="1.3" fill="none" stroke="currentColor" strokeWidth="1.2" />
      <circle cx="15.5" cy="9.5" r="1.3" fill="none" stroke="currentColor" strokeWidth="1.2" />
      <circle cx="12" cy="13.8" r="1.6" fill="currentColor" />
    </svg>
  );
}

type IconName =
  | 'home'
  | 'runtime'
  | 'agents'
  | 'supervision'
  | 'changes'
  | 'recovery'
  | 'devices'
  | 'aiMonitor'
  | 'settings';

/** Minimal geometric nav icons; stroke-only, currentColor, no emoji. */
function NavIcon({ name }: { name: IconName }) {
  const common = {
    width: 15,
    height: 15,
    viewBox: '0 0 24 24',
    'aria-hidden': true,
    className: 'app-nav-icon',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
  } as const;
  switch (name) {
    case 'home':
      return (
        <svg {...common}>
          <path d="M4 11 12 4l8 7" />
          <path d="M6 10v9h12v-9" />
        </svg>
      );
    case 'runtime':
      return (
        <svg {...common}>
          <rect x="5" y="5" width="14" height="14" rx="2" />
          <path d="M9 9h6v6H9z" />
        </svg>
      );
    case 'agents':
      return (
        <svg {...common}>
          <circle cx="12" cy="8" r="3.2" />
          <path d="M5.5 19c1.2-3.2 3.6-4.8 6.5-4.8s5.3 1.6 6.5 4.8" />
        </svg>
      );
    case 'supervision':
      return (
        <svg {...common}>
          <path d="M3 12s3.5-5.5 9-5.5S21 12 21 12s-3.5 5.5-9 5.5S3 12 3 12Z" />
          <circle cx="12" cy="12" r="2.4" />
        </svg>
      );
    case 'changes':
      return (
        <svg {...common}>
          <path d="M7 7h10l-3-3" />
          <path d="M17 17H7l3 3" />
        </svg>
      );
    case 'recovery':
      return (
        <svg {...common}>
          <path d="M4 10a8 8 0 1 1 2 6" />
          <path d="M4 16v-6h6" />
        </svg>
      );
    case 'devices':
      return (
        <svg {...common}>
          <rect x="8" y="3.5" width="8" height="17" rx="2" />
          <path d="M11 17.5h2" />
        </svg>
      );
    case 'aiMonitor':
      return (
        <svg {...common}>
          <path d="M3 13h4l2.5-6 3.5 10 2.5-6H21" />
        </svg>
      );
    case 'settings':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3M6 6l2.1 2.1M15.9 15.9 18 18M18 6l-2.1 2.1M8.1 15.9 6 18" />
        </svg>
      );
  }
}
