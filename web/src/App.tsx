import { useState } from 'react';

import { AppShell, type PageKey } from './components/AppShell';
import { useT } from './i18n/I18nProvider';
import type { MessageKey } from './i18n/messages';
import ChangesPage from './pages/ChangesPage';
import DevicesPage from './pages/DevicesPage';
import EnvironmentPage from './pages/EnvironmentPage';
import HomePage from './pages/HomePage';
import RecoveryPage from './pages/RecoveryPage';
import SettingsPage from './pages/SettingsPage';
import WorkPage from './pages/WorkPage';
import SupervisionPage from './pages/SupervisionPage';
import './styles/app.css';

const TITLE_KEYS: Record<PageKey, MessageKey> = {
  work: 'nav.work',
  home: 'nav.home',
  environment: 'nav.environment',
  changes: 'nav.changes',
  supervision: 'nav.supervision',
  recovery: 'nav.recovery',
  devices: 'nav.devices',
  settings: 'nav.settings',
};

/**
 * Work owns its immersive shell and stays mounted while visiting Guard,
 * preserving local layout drafts. The existing Guard surfaces retain their
 * navigation and authoritative backend semantics outside that shell.
 */
export default function App() {
  const [page, setPage] = useState<PageKey>(new URLSearchParams(location.search).has('work') || new URLSearchParams(location.search).has('workspace') ? 'work' : 'home');
  const [workVisited, setWorkVisited] = useState(page === 'work');
  const t = useT();
  const navigate = (next: PageKey) => { if (next === 'work') setWorkVisited(true); setPage(next); };
  return (
    <>
    {workVisited && <div hidden={page !== 'work'}><WorkPage onNavigate={navigate} visible={page === 'work'} /></div>}
    {page !== 'work' && <AppShell page={page} onNavigate={navigate} title={t(TITLE_KEYS[page])}>
      {page === 'home' && <HomePage />}
      {page === 'environment' && <EnvironmentPage />}
      {page === 'changes' && <ChangesPage />}
      {page === 'supervision' && <SupervisionPage />}
      {page === 'recovery' && <RecoveryPage />}
      {page === 'devices' && <DevicesPage />}
      {page === 'settings' && <SettingsPage />}
    </AppShell>}
    </>
  );
}
