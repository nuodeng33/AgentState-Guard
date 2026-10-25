import { useEffect, useRef, useState, type ReactNode } from 'react';

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

const SETTINGS_ITEMS = NAV_ITEMS.filter(item => item.key !== 'work');

export type WorkSidebarProps = {
  items?: { id: string; title: string }[];
  activeId?: string | null;
  onOpen?: (id: string) => void;
  onCreate?: () => void;
  onNavigate: (page: PageKey) => void;
  page: PageKey;
  onMore?: () => void;
  hasMore?: boolean;
  navigationLocked?: boolean;
};

/** Work navigation only; auxiliary destinations live inside Settings. */
export function WorkSidebar({ items = [], activeId, onOpen, onCreate, onNavigate, page,
  onMore, hasMore = false, navigationLocked = false }: WorkSidebarProps) {
  const t = useT();
  const [profileOpen, setProfileOpen] = useState(false);
  const [search, setSearch] = useState('');
  const query = search.trim().toLocaleLowerCase();
  const filteredItems = query ? items.filter(item => item.title.toLocaleLowerCase().includes(query)) : items;
  const profile = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { setProfileOpen(false); }, [page]);
  useEffect(() => {
    if (!profileOpen) return;
    const outside = (event: PointerEvent) => {
      if (!profile.current?.contains(event.target as Node)) setProfileOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation(); setProfileOpen(false); trigger.current?.focus();
      }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape, true);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape, true); };
  }, [profileOpen]);
  return <aside className="work-sidebar" aria-label={t('nav.work')}>
    <div className="work-sidebar-brand"><ShieldNodesMark /><span>{t('app.brand.name')}</span></div>
    {onCreate && <button type="button" className="work-sidebar-create" disabled={navigationLocked}
      onClick={onCreate}><span aria-hidden="true">＋</span>{t('sidebar.newWork')}</button>}
    <nav className="work-sidebar-nav" aria-label={t('app.nav.primary')}>
      <button type="button" className="work-sidebar-home" disabled={navigationLocked}
        aria-current={page === 'work' && !activeId ? 'page' : undefined} onClick={() => onNavigate('work')}>
        <NavIcon name="work" />{t('nav.work')}
      </button>
      {onOpen && <input type="search" className="work-sidebar-search" aria-label={t('sidebar.searchWork')}
        placeholder={t('sidebar.searchWork')} value={search} disabled={navigationLocked}
        onChange={event => setSearch(event.target.value)} />}
      <div className="work-sidebar-list">
        {query && !filteredItems.length && <p className="work-sidebar-no-match">{t('sidebar.noMatches')}</p>}
        {filteredItems.map(item => <button type="button" key={item.id} title={item.title}
          disabled={navigationLocked} aria-current={page === 'work' && item.id === activeId ? 'page' : undefined}
          onClick={() => onOpen?.(item.id)}>{item.title}</button>)}
        {hasMore && onMore && <button type="button" disabled={navigationLocked} onClick={onMore}>{t('sidebar.moreWorks')}</button>}
      </div>
    </nav>
    <div className="work-sidebar-profile" ref={profile}>
      {profileOpen && <div className="work-profile-popover">
        <button type="button" disabled={navigationLocked && page !== 'settings'} onClick={() => {
          setProfileOpen(false); onNavigate('settings');
        }}><NavIcon name="settings" />{t('nav.settings')}</button>
      </div>}
      <button type="button" ref={trigger} className="work-profile-trigger" aria-label={t('sidebar.profile')} aria-expanded={profileOpen}
        onClick={() => setProfileOpen(open => !open)}>
        <span className="work-local-avatar" aria-hidden="true">{t('sidebar.localInitial')}</span>
        <span>{t('sidebar.localWorkspace')}</span><span aria-hidden="true">⌄</span>
      </button>
    </div>
  </aside>;
}

/** Auxiliary pages are sections of Settings; the work remains available at left. */
export function AppShell({ page, onNavigate, client, title, navigationLocked = false, children }: {
  page: PageKey; onNavigate: (page: PageKey) => void; client?: ApiClient;
  title: string; navigationLocked?: boolean; children: ReactNode;
}) {
  const t = useT();
  return <div className="app-shell">
    <WorkSidebar page={page} onNavigate={onNavigate} navigationLocked={navigationLocked} />
    <div className="app-main">
      <StatusBanner client={client} />
      <header className="app-header"><h1 className="app-title">{t('nav.settings')}</h1></header>
      <nav className="settings-sections" aria-label={t('sidebar.settingsSections')}>
        {SETTINGS_ITEMS.map(item => <button type="button" key={item.key}
          aria-current={page === item.key ? 'page' : undefined}
          disabled={navigationLocked && item.key !== page} onClick={() => onNavigate(item.key)}>
          {t(item.labelKey)}
        </button>)}
      </nav>
      <main className="app-content">
        {page !== 'settings' && <h2 className="settings-section-title">{title}</h2>}
        {children}
      </main>
    </div>
  </div>;
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
