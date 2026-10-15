import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { ApiClient } from '../api/client';
import type { DiscoveryApplication, DiscoverySessionStore, DiscoverySource } from '../api/types';
import { DiscoveryCatalog } from './DiscoveryCatalog';

const source: DiscoverySource = {
  source_id: 'local', kind: 'NATIVE', label: 'Local', state: 'OBSERVED',
  reason_code: 'LOCAL_PROCESS_OBSERVATION', agent_count: 1, configurable: false,
  revision: 0, enabled: true, freshness: 'FRESH', session_probe_available: true,
  session_status: 'AVAILABLE',
};
const application: DiscoveryApplication = {
  id: 'app-1', source_id: 'local', path: 'C:/tool.exe', label: 'Desktop tool',
  provider: 'CODEX', status: 'AVAILABLE', reason_code: 'APPLICATION_PRESENT',
  registered: true, running: true, instances: [], observed_at: '2026-09-30T00:00:00Z',
  freshness: 'FRESH', installation_observed_at: '2026-09-29T00:00:00Z',
  installation_freshness: 'STALE',
  control_capabilities: {
    create: false, continue: false, send: false, stream: false, cancel: false,
    reason_code: 'CONTROL_ADAPTER_NOT_IMPLEMENTED', ownership: 'EXTERNAL',
  },
};
const store: DiscoverySessionStore = {
  id: 'store-1', source_id: 'local', provider: 'CODEX', path: 'C:/sessions',
  status: 'AVAILABLE', reason_code: 'SESSION_STORE_PRESENT', registered: true,
  observed_at: '2026-09-30T00:00:00Z', freshness: 'FRESH',
};

describe('DiscoveryCatalog state freshness', () => {
  it('separates installation checks from process observations and hides failed or unavailable session data', async () => {
    let failApplications = false;
    let failStores = false;
    let failSessions = false;
    const client: ApiClient = {
      bootstrap: async () => {},
      post: async <T,>() => ({} as T),
      async get<T>(path: string): Promise<T> {
        if (path.startsWith('/api/v1/discovery/applications')) {
          if (failApplications) throw new Error('unavailable');
          return { items: [application], has_more: false, next_offset: null } as T;
        }
        if (path.includes('/session-stores')) {
          if (failStores) throw new Error('unavailable');
          return { items: [store], has_more: false, next_offset: null } as T;
        }
        if (path.includes('/sessions')) {
          if (failSessions) throw new Error('unavailable');
          return { status: 'AVAILABLE', reason_code: 'SESSION_STORE_READ',
            sessions: [{ session_id: 'session-1', title: 'Previous session', updated_at: null }],
            has_more: false, next_offset: null } as T;
        }
        throw new Error('unexpected path');
      },
    };
    const props = { client, source, visible: true, refreshKey: 0, zh: false, locale: 'en-US' };
    const view = render(<DiscoveryCatalog {...props} />);
    const catalog = view.container.querySelector('details.work-agent-catalog') as HTMLDetailsElement;
    catalog.open = true;
    fireEvent(catalog, new Event('toggle', { bubbles: true }));

    expect(await screen.findByText('Desktop tool')).toBeTruthy();
    expect(screen.getByText(/Installation: STALE/)).toBeTruthy();
    expect(screen.queryByText(/Installation: AVAILABLE/)).toBeNull();

    const sessionDetails = screen.getByText('Provider sessions').closest('details') as HTMLDetailsElement;
    sessionDetails.open = true;
    fireEvent(sessionDetails, new Event('toggle', { bubbles: true }));
    fireEvent.change(screen.getByLabelText('Select session store'), { target: { value: 'store-1' } });
    expect(await screen.findByText('Previous session')).toBeTruthy();

    failSessions = true;
    view.rerender(<DiscoveryCatalog {...props} refreshKey={1} />);
    expect(await screen.findByText('Could not read sessions.')).toBeTruthy();
    expect(screen.queryByText('Previous session')).toBeNull();

    view.rerender(<DiscoveryCatalog {...props} source={{ ...source, state: 'NOT_PROBED' }} refreshKey={1} />);
    expect(screen.getByText(/Installation: Source unavailable/)).toBeTruthy();
    expect(screen.getByText(/Store status: Source unavailable/)).toBeTruthy();
    expect(screen.getByText(/This source is not currently readable/)).toBeTruthy();

    failApplications = true;
    failStores = true;
    view.rerender(<DiscoveryCatalog {...props} source={{ ...source, state: 'NOT_PROBED' }} refreshKey={2} />);
    await waitFor(() => {
      expect(screen.getByText('Could not load programs.')).toBeTruthy();
      expect(screen.getByText('Could not load session stores.')).toBeTruthy();
    });
    expect(screen.queryByText('Desktop tool')).toBeNull();
  });
});
