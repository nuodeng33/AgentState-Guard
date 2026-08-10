import { useState } from 'react';

import { AppShell, type PageKey } from './components/AppShell';
import { useT } from './i18n/I18nProvider';
import type { MessageKey } from './i18n/messages';
import AgentsPage from './pages/AgentsPage';
import AiMonitorPage from './pages/AiMonitorPage';
import ChangesPage from './pages/ChangesPage';
import HomePage from './pages/HomePage';
import RecoveryPage from './pages/RecoveryPage';
import RuntimePage from './pages/RuntimePage';
import SettingsPage from './pages/SettingsPage';
import SupervisionPage from './pages/SupervisionPage';
import './styles/app.css';

const TITLE_KEYS: Record<PageKey, MessageKey> = {
  home: 'nav.home',
  runtime: 'nav.runtime',
  agents: 'nav.agents',
  supervision: 'nav.supervision',
  changes: 'nav.changes',
  recovery: 'nav.recovery',
  devices: 'nav.devices',
  aiMonitor: 'nav.aiMonitor',
  settings: 'nav.settings',
};

/**
 * Product shell root. Primary navigation follows the product IA; the four
 * authoritative R4 views keep their exact semantics, and the new surfaces
 * (Home/Changes/Devices/AI Monitor/Settings) are honest shells that never
 * fabricate backend state. Legacy pre-P8 code stays under src/legacy,
 * unreachable from this navigation.
 */
export default function App() {
  const [page, setPage] = useState<PageKey>('home');
  const t = useT();
  return (
    <AppShell page={page} onNavigate={setPage} title={t(TITLE_KEYS[page])}>
      {page === 'home' && <HomePage />}
      {page === 'runtime' && <RuntimePage />}
      {page === 'agents' && <AgentsPage />}
      {page === 'supervision' && <SupervisionPage />}
      {page === 'changes' && <ChangesPage />}
      {page === 'recovery' && <RecoveryPage />}
      {page === 'devices' && <ChangesPage />}
      {page === 'aiMonitor' && <AiMonitorPage />}
      {page === 'settings' && <SettingsPage />}
    </AppShell>
  );
}
