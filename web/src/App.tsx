import { useState } from 'react';

import { AppShell, type PageKey } from './components/AppShell';
import AgentsPage from './pages/AgentsPage';
import RecoveryPage from './pages/RecoveryPage';
import RuntimePage from './pages/RuntimePage';
import SupervisionPage from './pages/SupervisionPage';
import './styles/app.css';

const TITLES: Record<PageKey, string> = {
  runtime: 'Runtime',
  agents: 'Agents',
  supervision: 'Supervision',
  recovery: 'Recovery',
};

/**
 * R4-P8 root. The primary navigation is exactly the four authoritative
 * read-only views; legacy dashboard/settings/devices code is preserved under
 * src/legacy but is not a P8 entry point.
 */
export default function App() {
  const [page, setPage] = useState<PageKey>('runtime');
  return (
    <AppShell page={page} onNavigate={setPage} title={TITLES[page]}>
      {page === 'runtime' && <RuntimePage />}
      {page === 'agents' && <AgentsPage />}
      {page === 'supervision' && <SupervisionPage />}
      {page === 'recovery' && <RecoveryPage />}
    </AppShell>
  );
}
