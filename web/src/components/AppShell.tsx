import type { ReactNode } from 'react';

import type { ApiClient } from '../api/client';
import { useT } from '../i18n/I18nProvider';
import type { MessageKey } from '../i18n/messages';
import { StatusBanner } from './StatusBanner';

export type PageKey =
  | 'work'
  | 'home'
  | 'environment'
  | 'changes'
  | 'supervision'
  | 'recovery'
  | 'devices'
  | 'settings';

const NAV_ITEMS: Array<{ key: PageKey; labelKey: MessageKey; icon: IconName }> = [
  { key: 'work', labelKey: 'nav.work', icon: 'work' },
  { key: 'home', labelKey: 'nav.home', icon: 'home' },
  { key: 'environment', labelKey: 'nav.environment', icon: 'environment' },
  { key: 'changes', labelKey: 'nav.changes', icon: 'changes' },
  { key: 'supervision', labelKey: 'nav.supervision', icon: 'supervision' },
  { key: 'recovery', labelKey: 'nav.recovery', icon: 'recovery' },
  { key: 'devices', labelKey: 'nav.devices', icon: 'devices' },
  { key: 'settings', labelKey: 'nav.settings', icon: 'settings' },
];

const NAV_GROUPS: PageKey[][] = [['work'], ['home', 'environment', 'changes', 'supervision', 'recovery', 'devices'], ['settings']];

/**
 * First-level navigation shared by the Guard pages and the work overview.
 * It is hidden once a single work is open, so the work owns the screen.
 */
export function TopBar({ page, onNavigate, navigationLocked = false }: {
  page: PageKey; onNavigate: (page: PageKey) => void; navigationLocked?: boolean;
}) {
  const t = useT();
  return (
    <header className="app-topbar">
      <div className="app-brand">
        <ShieldNodesMark />
        <div className="app-brand-name">{t('app.brand.name')}</div>
      </div>
      <nav className="app-nav" aria-label={t('app.nav.primary')}>
        {NAV_GROUPS.map((group, index) => (
          <div className="app-nav-group" key={group.join()}>
            {index > 0 && <span className="app-nav-rule" aria-hidden="true" />}
            {group.map((key) => {
              const item = NAV_ITEMS.find((entry) => entry.key === key)!;
              return (
                <button
                  key={key}
                  type="button"
                  aria-label={t(item.labelKey)}
                  disabled={navigationLocked && key !== page}
                  className={`app-nav-item${key === page ? ' is-active' : ''}`}
                  aria-current={key === page ? 'page' : undefined}
                  onClick={() => onNavigate(key)}
                >
                  <NavIcon name={item.icon} />
                  <span>{t(item.labelKey)}</span>
                </button>
              );
            })}
          </div>
        ))}
      </nav>
    </header>
  );
}

/**
 * Product shell for Guard pages: top navigation, the bounded readiness
 * banner, and the content header. Machine states inside pages stay verbatim.
 */
export function AppShell({
  page,
  onNavigate,
  client,
  title,
  navigationLocked = false,
  children,
}: {
  page: PageKey;
  onNavigate: (page: PageKey) => void;
  client?: ApiClient;
  title: string;
  navigationLocked?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <TopBar page={page} onNavigate={onNavigate} navigationLocked={navigationLocked} />
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
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" className="brand-mark">
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
  | 'work'
  | 'home'
  | 'environment'
  | 'changes'
  | 'supervision'
  | 'recovery'
  | 'devices'
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
    case 'work':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="7.5" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      );
    case 'home':
      return (
        <svg {...common}>
          <path d="M4 11 12 4l8 7" />
          <path d="M6 10v9h12v-9" />
        </svg>
      );
    case 'environment':
      return (
        <svg {...common}>
          <rect x="5" y="5" width="14" height="14" rx="2" />
          <path d="M9 9h6v6H9z" />
          <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
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
    case 'settings':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3M6 6l2.1 2.1M15.9 15.9 18 18M18 6l-2.1 2.1M8.1 15.9 6 18" />
        </svg>
      );
  }
}
