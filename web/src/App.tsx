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
import SupervisionPage from './pages/SupervisionPage';
import './styles/app.css';

const TITLE_KEYS: Record<PageKey, MessageKey> = {
  home: 'nav.home',
  environment: 'nav.environment',
  changes: 'nav.changes',
  supervision: 'nav.supervision',
  recovery: 'nav.recovery',
  devices: 'nav.devices',
  settings: 'nav.settings',
};

/**
 * Product shell root. Primary navigation is exactly the V1 IA: Home,
 * Environment, Changes, Supervision, Recovery, Devices, Settings. Runtime
 * and Agents are folded into Environment (their presentation bodies are
 * reused verbatim); AI capability lives under Settings and the bounded
 * advisory components, not as a standalone surface. Every surface keeps
 * exact backend semantics and never fabricates state.
 */
export default function App() {
  const [page, setPage] = useState<PageKey>('home');
  const t = useT();
  return (
    <AppShell page={page} onNavigate={setPage} title={t(TITLE_KEYS[page])}>
      {page === 'home' && <HomePage />}
      {page === 'environment' && <EnvironmentPage />}
      {page === 'changes' && <ChangesPage />}
      {page === 'supervision' && <SupervisionPage />}
      {page === 'recovery' && <RecoveryPage />}
      {page === 'devices' && <DevicesPage />}
      {page === 'settings' && <SettingsPage />}
    </AppShell>
  );
}
