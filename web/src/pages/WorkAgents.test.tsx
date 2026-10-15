import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import WorkAgents, { type WorkAgent } from './WorkAgents';

afterEach(cleanup);
const observed: WorkAgent = { agent_id: 'agent:codex-process-1', detected_identity: 'CODEX', lifecycle: 'RUNNING',
  role: 'EXECUTION_AGENT', confidence: .9, execution_domain_id: 'host', workspace: { status: 'UNKNOWN', binding_ref: null },
  reason_code: 'AGENT_DETECTED', uncertainty: true, observed_at: '2026-09-29T10:00:00Z', evidence_refs: ['evidence-1'],
  activity: 'UNKNOWN', presence: 'CURRENT', application_id: 'application-1', program_path: 'C:/Tools/codex.exe',
  source_id: 'local', source_label: 'Local processes', pid: 420, cwd: 'C:/Work' };

function fixture(initial: WorkAgent[] = [], detected: WorkAgent[] = [observed]) {
  let linked = initial, revision = 3;
  let sources = [
    { source_id: 'local', kind: 'NATIVE', label: 'Local processes', state: 'OBSERVED', reason_code: 'LOCAL_PROCESS_OBSERVATION',
      agent_count: 1, configurable: false, revision: 0, enabled: true, freshness: 'FRESH', session_probe_available: true,
      session_status: 'PROTOCOL_NOT_CONNECTED' },
    { source_id: 'docker-container', kind: 'CONTAINER', label: 'agent-k3', short_id: 'abcdef123456', state: 'OBSERVED', reason_code: 'DOCKER_PROCESS_OBSERVED',
      agent_count: 1, configurable: true, revision: 0, enabled: true, freshness: 'FRESH', session_probe_available: true,
      session_status: 'PROTOCOL_NOT_CONNECTED' },
  ];
  let applications = [{ id: 'application-1', source_id: 'local', path: 'C:/Tools/codex.exe', provider: 'CODEX',
    classification: 'RECOGNIZED', status: 'AVAILABLE', registered: false, running: true, instances: [{ agent_id: observed.agent_id }],
    version: '1.2.3', installation_freshness: 'FRESH', control_capabilities: { create: false } }];
  let stores = [{ id: 'store-local', source_id: 'local', provider: 'CODEX', path: 'C:/Users/Test/.codex/sessions',
    status: 'AVAILABLE', registered: false }];
  const post = vi.fn(async (path: string, body?: { task_ids?: string[]; enabled?: boolean; expected_revision?: number;
    source_id?: string; path?: string; candidate_id?: string; provider?: string }) => {
    if (path.endsWith('/refresh')) return {};
    if (path === '/api/v1/discovery/applications') {
      applications = applications.map(item => item.id === body?.candidate_id ? { ...item, registered: true } : item);
      return { application_id: body?.candidate_id || 'application-registered' };
    }
    if (path === '/api/v1/discovery/session-stores') {
      stores = [...stores, { id: 'store-registered', source_id: body?.source_id || 'local', provider: body?.provider || 'CODEX',
        path: body?.path || '', status: 'AVAILABLE', registered: true }];
      return { store_id: 'store-registered' };
    }
    if (path.endsWith('/configure')) {
      sources = sources.map(source => source.source_id === 'docker-container' ? { ...source, enabled: Boolean(body?.enabled),
        revision: source.revision + 1, state: body?.enabled ? 'OBSERVED' : 'DISABLED' } : source);
      return { revision: sources[1].revision, enabled: body?.enabled };
    }
    linked = path.endsWith('/tasks') ? linked.map(agent => ({ ...agent, task_ids: body?.task_ids })) :
      path.endsWith('/detach') ? [] : [{ ...observed, association: 'USER_LINKED', presence: 'CURRENT' }];
    return { revision: ++revision };
  });
  const get = vi.fn(async (path: string) => {
    if (path === '/api/v1/discovery/sources') return { items: sources,
      inventory_coverage: [{ source_id: 'local', status: 'AVAILABLE', observed: 1, reason_code: 'BOUNDED_ENTRY_OBSERVATION' }] };
    if (path.startsWith('/api/v1/discovery/applications?')) {
      const query = new URL(path, 'http://local').searchParams;
      const includeUnclassified = query.get('include_unclassified') === 'true';
      const unknown = { ...applications[0], id: 'application-unknown', path: 'C:/Tools/other.exe', provider: 'UNKNOWN',
        classification: 'UNCLASSIFIED', running: false };
      return { items: includeUnclassified ? [...applications, unknown] : applications, has_more: false, next_offset: null,
        unclassified_count: 1 };
    }
    if (path.includes('/session-stores?')) return { items: stores.filter(store => path.includes(encodeURIComponent(store.source_id))),
      has_more: false, next_offset: null };
    if (path.includes('/sessions?')) {
      const query = new URL(path, 'http://local').searchParams;
      return query.get('offset') === '50' ? { status: 'AVAILABLE', reason_code: 'SESSIONS_READ', store_id: 'store-local',
        sessions: [{ session_id: 'session-51', title: 'Later session', updated_at: null }], has_more: false, next_offset: null } :
        { status: 'AVAILABLE', reason_code: 'SESSIONS_READ', store_id: 'store-local', scope: 'PROVIDER_STORE',
          sessions: [{ session_id: 'session-1', title: 'A previous session', updated_at: '2026-09-29T09:00:00Z' },
            { session_id: 'session-2', title: null, updated_at: null }], has_more: true, next_offset: 50 };
    }
    if (path === '/api/v1/agents') return { status: 'DEGRADED', items: detected };
    if (path.endsWith('/agents')) return { items: linked };
    return { revision };
  });
  const props = { client: { get, post, bootstrap: vi.fn() } as unknown as ApiClient, workId: 'work-1', revision: 3,
    linked: initial, onClose: vi.fn(), onLinkedChanged: vi.fn(), onRevision: vi.fn() };
  return { props, get, post };
}

it('links a real discovery identity while leaving work execution explicitly unknown', async () => {
  const { props, post } = fixture(); render(<WorkAgents {...props} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Link to this work' }));
  await screen.findByRole('button', { name: 'Unlink' });
  expect(post).toHaveBeenCalledWith('/api/v1/works/work-1/agents', expect.objectContaining({
    agent_id: observed.agent_id, expected_revision: 3, request_id: expect.any(String),
  }));
  expect(props.onLinkedChanged).toHaveBeenLastCalledWith([expect.objectContaining({ activity: 'UNKNOWN', association: 'USER_LINKED' })]);
  fireEvent.click(screen.getByRole('button', { name: /CODEX.*Process observed/ }));
  expect(await screen.findByText('No verified execution record for this work or its tasks.')).toBeTruthy();
  expect(screen.getByText('User linked')).toBeTruthy();
});

it('retries an uncertain association with the exact same request receipt', async () => {
  const { props, post } = fixture(); post.mockRejectedValueOnce(new Error('lost response'));
  render(<WorkAgents {...props} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Link to this work' }));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Link to this work' }));
  await screen.findByRole('button', { name: 'Unlink' });
  expect(post.mock.calls[1]).toEqual(post.mock.calls[0]);
});

it('retains an old linked identity without claiming its process is still present', async () => {
  const old = { ...observed, presence: 'NOT_CURRENT' as const, association: 'USER_LINKED' as const };
  const { props, post } = fixture([old]); render(<WorkAgents {...props} initialAgentId={old.agent_id} />);
  expect(await screen.findByText('Not in current observation · activity unknown')).toBeTruthy();
  await waitFor(() => expect((screen.getByRole('button', { name: 'Unlink' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Unlink' }));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v1/works/work-1/agents/agent%3Acodex-process-1/detach', expect.objectContaining({ expected_revision: 3 })));
});

it('saves explicit task links without deriving execution from a running process', async () => {
  const { props, post } = fixture([{ ...observed, association: 'USER_LINKED', task_ids: [] }]);
  render(<WorkAgents {...props} initialAgentId={observed.agent_id} tasks={[{ id: 'task-1', title: 'Review the result' }, { id: 'task-2', title: 'Read the requirement' }]} />);
  await waitFor(() => expect((screen.getByRole('button', { name: 'Save task links' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Review the result' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save task links' }));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v1/works/work-1/agents/agent%3Acodex-process-1/tasks', expect.objectContaining({ expected_revision: 3, task_ids: ['task-1'] })));
  await waitFor(() => expect(props.onLinkedChanged).toHaveBeenLastCalledWith([expect.objectContaining({ task_ids: ['task-1'], activity: 'UNKNOWN' })]));
  expect(screen.getByText('No verified execution record for this work or its tasks.')).toBeTruthy();
});

it.each(['STALE', 'UNKNOWN'] as const)('does not link a %s discovery identity', async presence => {
  const candidate = { ...observed, presence, lifecycle: 'UNKNOWN' };
  const { props, post } = fixture([], [candidate]);
  render(<WorkAgents {...props} initialAgentId={candidate.agent_id} />);
  const link = await screen.findByRole('button', { name: 'Link to this work' });
  await waitFor(() => expect(screen.getByText(presence === 'STALE' ? 'Observation expired · activity unknown' :
    'Presence unknown · activity unknown')).toBeTruthy());
  expect((link as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(link);
  expect(post).not.toHaveBeenCalledWith('/api/v1/works/work-1/agents', expect.anything());
});

it('shows source programs and reads only the explicitly selected store, including later pages', async () => {
  const { props, get } = fixture();
  render(<WorkAgents {...props} />);
  const summary = await screen.findByText('Observation sources · 2');
  expect(get.mock.calls.some(([path]) => path.includes('/sessions?'))).toBe(false);
  fireEvent.click(summary);
  const local = screen.getByText('Local processes').closest('.work-agent-source-row')!;
  fireEvent.click(within(local as HTMLElement).getByText('Programs and session stores'));
  expect(await within(local as HTMLElement).findByText('C:/Tools/codex.exe')).toBeTruthy();
  expect(get.mock.calls.some(([path]) => path.includes('/sessions?'))).toBe(false);
  fireEvent.click(within(local as HTMLElement).getByText('Provider sessions'));
  expect(within(local as HTMLElement).getByText('Select a store to read its sessions.')).toBeTruthy();
  fireEvent.change(within(local as HTMLElement).getByLabelText('Select session store'), { target: { value: 'store-local' } });
  expect(await screen.findByText(/A previous session/)).toBeTruthy();
  expect(screen.getByText('Untitled session')).toBeTruthy();
  expect(screen.getByText(/Provider sessions, not linked to this work or process/)).toBeTruthy();
  expect(get).toHaveBeenCalledWith('/api/v1/discovery/sources/local/sessions?store_id=store-local&limit=50&offset=0');
  fireEvent.click(within(local.querySelector('.work-agent-session-result') as HTMLElement).getByRole('button', { name: 'Next' }));
  expect(await screen.findByText('Later session')).toBeTruthy();
  expect(get).toHaveBeenCalledWith('/api/v1/discovery/sources/local/sessions?store_id=store-local&limit=50&offset=50');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh discovery' }));
  await waitFor(() => expect(screen.getByText('Later session')).toBeTruthy());
  expect((within(local as HTMLElement).getByLabelText('Select session store') as HTMLSelectElement).value).toBe('store-local');
});

it('configures observation with its revision and updates the source state', async () => {
  const { props, post } = fixture();
  render(<WorkAgents {...props} />);
  fireEvent.click(await screen.findByText('Observation sources · 2'));
  const source = screen.getByText(/agent-k3/).closest('.work-agent-source-row')!;
  expect(within(source as HTMLElement).getByText(/abcdef123456/)).toBeTruthy();
  fireEvent.click(within(source as HTMLElement).getByRole('checkbox', { name: 'Observe' }));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v1/discovery/sources/docker-container/configure',
    { enabled: false, expected_revision: 0 }));
  await waitFor(() => expect(within(source as HTMLElement).getByText('Disabled · 1 agents')).toBeTruthy());
  expect(screen.queryByText(/A previous session/)).toBeNull();
});

it('keeps installed, running and control states separate and offers unclassified programs only on request', async () => {
  const { props, get, post } = fixture();
  render(<WorkAgents {...props} />);
  fireEvent.click(await screen.findByText('Observation sources · 2'));
  const local = screen.getByText('Local processes').closest('.work-agent-source-row')! as HTMLElement;
  fireEvent.click(within(local).getByText('Programs and session stores'));
  expect(await within(local).findByText('C:/Tools/codex.exe')).toBeTruthy();
  expect(within(local).getByText(/Installation: AVAILABLE · Running: Yes · Interaction: Unavailable/)).toBeTruthy();
  expect(screen.queryByText('C:/Tools/other.exe')).toBeNull();
  fireEvent.click(within(local).getByRole('checkbox', { name: 'Unclassified programs (1)' }));
  expect(await within(local).findByText('C:/Tools/other.exe')).toBeTruthy();
  expect(get).toHaveBeenCalledWith('/api/v1/discovery/applications?source_id=local&limit=50&offset=0&include_unclassified=true');
  const known = within(local).getByText('C:/Tools/codex.exe').closest('.work-agent-catalog-row')! as HTMLElement;
  fireEvent.click(within(known).getByRole('button', { name: 'Register' }));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v1/discovery/applications',
    { source_id: 'local', candidate_id: 'application-1' }));
  fireEvent.click(within(local).getAllByText('Register session store')[0]);
  fireEvent.change(within(local).getByRole('textbox', { name: 'Session store path' }),
    { target: { value: 'C:/Custom/sessions' } });
  fireEvent.click(within(local).getByRole('button', { name: 'Register session store' }));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/api/v1/discovery/session-stores',
    { source_id: 'local', provider: 'CODEX', path: 'C:/Custom/sessions' }));
});

it('uses current discovery identity to complete a sparse work association', async () => {
  const linked = { ...observed, program_path: undefined, source_label: undefined, pid: undefined, cwd: undefined,
    association: 'USER_LINKED' as const };
  const { props } = fixture([linked]);
  render(<WorkAgents {...props} initialAgentId={linked.agent_id} />);
  fireEvent.click(await screen.findByText('Identity and observation details'));
  expect(screen.getByText('C:/Tools/codex.exe')).toBeTruthy();
  expect(screen.getByText('420')).toBeTruthy();
  expect(screen.getByText('C:/Work')).toBeTruthy();
});

it('polls observations while visible and stops when hidden', async () => {
  const { props, get } = fixture();
  vi.useFakeTimers();
  try {
    const view = render(<WorkAgents {...props} />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText('Observation sources · 2')).toBeTruthy();
    const reads = () => get.mock.calls.filter(([path]) => path === '/api/v1/agents').length;
    const initial = reads();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(reads()).toBeGreaterThan(initial);
    view.rerender(<WorkAgents {...props} visible={false} />);
    const hidden = reads();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(reads()).toBe(hidden);
  } finally { vi.useRealTimers(); }
});
