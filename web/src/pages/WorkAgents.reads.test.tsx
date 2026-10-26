import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import WorkAgents, { useWorkAgents, type WorkAgent } from './WorkAgents';
const agent: WorkAgent = { agent_id: 'agent-codex', detected_identity: 'CODEX', lifecycle: 'RUNNING', role: 'EXECUTION_AGENT',
  confidence: 1, execution_domain_id: 'local', workspace: { status: 'UNKNOWN', binding_ref: null }, reason_code: 'OBSERVED',
  uncertainty: true, observed_at: '2026-10-25T00:00:00Z', evidence_refs: [], activity: 'UNKNOWN', presence: 'CURRENT', source_id: 'local' };
function deferred<T>() { let resolve!: (value: T) => void, reject!: (reason: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function fixture() {
  const get = vi.fn(async (path: string): Promise<unknown> => path === '/api/v1/agents' ? { items: [agent], status: 'AVAILABLE' } :
    path.split('?')[0].endsWith('/agents') ? { items: [] } : path.endsWith('/sources') ? { items: [] } : { revision: 3 });
  const post = vi.fn(async () => ({ revision: 4 }));
  const props = { client: { get, post } as unknown as ApiClient, workId: 'work-a', revision: 3, linked: [] as WorkAgent[],
    onClose: vi.fn(), onLinkedChanged: vi.fn(), onRevision: vi.fn() };
  return { props, get, post };
}
afterEach(() => { cleanup(); vi.useRealTimers(); });

it('starts linked identities and optional sources without waiting for global discovery', async () => {
  const f = fixture(), global = deferred<unknown>(); const original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/agents' ? global.promise : original(path));
  render(<WorkAgents {...f.props} />);
  expect(f.get).toHaveBeenCalledWith('/api/v1/works/work-a/agents?view=links');
  expect(f.get).toHaveBeenCalledWith('/api/v1/works/work-a');
  expect(f.get).toHaveBeenCalledWith('/api/v1/discovery/sources');
  await act(async () => global.resolve({ items: [agent], status: 'AVAILABLE' }));
  expect(await screen.findByRole('button', { name: 'Link to this work' })).toBeTruthy();
});

it('never converts a slow association read into an authoritative empty relationship list', async () => {
  vi.useFakeTimers(); const f = fixture(), linked = deferred<unknown>(); const original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/works/work-a/agents?view=links' ? linked.promise : original(path));
  render(<WorkAgents {...f.props} linked={[{ ...agent, association: 'USER_LINKED' }]} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.getByRole('button', { name: 'Unlink' })).toBeTruthy();
  expect(f.props.onLinkedChanged.mock.calls.some(([items]) => items.length === 0)).toBe(false);
  expect(screen.queryByText('No actual Agents are linked to this work yet.')).toBeNull();
  await act(async () => linked.resolve({ items: [{ ...agent, association: 'USER_LINKED' }] }));
});

it('publishes a real late discovery response instead of discarding it after a fallback timeout', async () => {
  vi.useFakeTimers(); const f = fixture(), global = deferred<unknown>(); const original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/agents' ? global.promise : original(path));
  render(<WorkAgents {...f.props} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.queryByText('No other Agents available. Refresh discovery to check again.')).toBeNull();
  await act(async () => global.resolve({ items: [agent], status: 'AVAILABLE' }));
  expect(screen.getByRole('button', { name: 'Link to this work' })).toBeTruthy();
});

it('coalesces the same in-flight linked-agent read used by the Work and its panel', async () => {
  const f = fixture(), linked = deferred<unknown>(); const original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/works/work-a/agents?view=links' ? linked.promise : original(path));
  function WorkProbe() { const result = useWorkAgents(f.props.client, 'work-a', true); return <p>{result.items.length} linked</p>; }
  render(<><WorkProbe /><WorkAgents {...f.props} /></>);
  await act(async () => {});
  expect(f.get.mock.calls.filter(([path]) => path === '/api/v1/works/work-a/agents?view=links')).toHaveLength(1);
  await act(async () => linked.resolve({ items: [agent] }));
});

it('does not let an old Work association response replace a new Work generation', async () => {
  const f = fixture(), old = deferred<unknown>(); const original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/works/work-a/agents?view=links' ? old.promise :
    path === '/api/v1/works/work-b/agents?view=links' ? Promise.resolve({ items: [{ ...agent, agent_id: 'new-agent', association: 'USER_LINKED' }] }) : original(path));
  const view = render(<WorkAgents {...f.props} />);
  await act(async () => {});
  view.rerender(<WorkAgents {...f.props} workId="work-b" />);
  await act(async () => {});
  await act(async () => old.resolve({ items: [{ ...agent, agent_id: 'old-agent', association: 'USER_LINKED' }] }));
  expect(f.props.onLinkedChanged.mock.calls[f.props.onLinkedChanged.mock.calls.length - 1]?.[0]).toEqual([expect.objectContaining({ agent_id: 'new-agent' })]);
  expect(screen.getAllByRole('button', { name: 'Unlink' })).toHaveLength(1);
});

it('keeps optional source latency from blocking confirmed candidate and Work controls', async () => {
  const f = fixture(), sources = deferred<unknown>(); const original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/discovery/sources' ? sources.promise : original(path));
  render(<WorkAgents {...f.props} />);
  const link = await screen.findByRole('button', { name: 'Link to this work' });
  await waitFor(() => expect((link as HTMLButtonElement).disabled).toBe(false));
  expect(f.post).not.toHaveBeenCalled();
  await act(async () => sources.resolve({ items: [] }));
});

it('retains known discovered identities on read failure while disabling stale association actions', async () => {
  const f = fixture(), original = f.get.getMockImplementation()!;
  render(<WorkAgents {...f.props} />);
  await waitFor(() => expect((screen.getByRole('button', { name: 'Link to this work' }) as HTMLButtonElement).disabled).toBe(false));
  f.get.mockImplementation(path => path === '/api/v1/agents' ? Promise.reject(new Error('private diagnostic')) : original(path));
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh discovery' })); });
  expect((screen.getByRole('button', { name: 'Link to this work' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText('Last known observation · awaiting refresh')).toBeTruthy();
  expect(screen.queryByText('No other Agents available. Refresh discovery to check again.')).toBeNull();
  expect(screen.queryByText('private diagnostic')).toBeNull();
});

it('keeps explicit retry available after an association read fails', async () => {
  const f = fixture(), original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/works/work-a/agents?view=links' ? Promise.reject(new Error()) : original(path));
  render(<WorkAgents {...f.props} linked={[{ ...agent, association: 'USER_LINKED' }]} />);
  const retry = await screen.findByRole('button', { name: 'Reload records' });
  await waitFor(() => expect((retry as HTMLButtonElement).disabled).toBe(false));
  f.get.mockImplementation(path => path === '/api/v1/works/work-a/agents?view=links' ? Promise.resolve({ items: [{ ...agent, association: 'USER_LINKED' }] }) : original(path));
  await act(async () => { fireEvent.click(retry); });
  expect((screen.getByRole('button', { name: 'Unlink' }) as HTMLButtonElement).disabled).toBe(false);
});

it('keeps an observed count during refresh and retains its records if that refresh fails', async () => {
  const f = fixture(), refresh = deferred<unknown>(); let reads = 0;
  f.get.mockImplementation(async () => ++reads === 1 ? { items: [agent] } : refresh.promise);
  function Probe() {
    const result = useWorkAgents(f.props.client, 'work-a', true);
    return <><output aria-label="Agent observation">{result.failed ? 'failed' : result.loading ? 'reading' : 'observed'}:{result.items.length}</output>
      <button onClick={() => void result.reload()}>Read again</button></>;
  }
  render(<Probe />);
  await waitFor(() => expect(screen.getByLabelText('Agent observation').textContent).toBe('observed:1'));
  fireEvent.click(screen.getByRole('button', { name: 'Read again' }));
  expect(screen.getByLabelText('Agent observation').textContent).toBe('observed:1');
  await act(async () => refresh.reject(new Error('private read error')));
  expect(screen.getByLabelText('Agent observation').textContent).toBe('failed:1');
  expect(screen.queryByText('private read error')).toBeNull();
});


it('waits for optional observations to settle before another background batch', async () => {
  vi.useFakeTimers(); const f = fixture(), global = deferred<unknown>(); const original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/agents' ? global.promise : original(path));
  render(<WorkAgents {...f.props} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
  expect(f.get.mock.calls.filter(([path]) => path === '/api/v1/agents')).toHaveLength(1);
  expect(f.get.mock.calls.filter(([path]) => path.endsWith('/sources'))).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Refresh discovery' }).hasAttribute('disabled')).toBe(true);
  await act(async () => global.resolve({ items: [agent], status: 'AVAILABLE' }));
  expect(screen.getByRole('button', { name: 'Link to this work' }).hasAttribute('disabled')).toBe(false);
});

it('backs off failed reads instead of filling the server queue and never enables stale candidates', async () => {
  vi.useFakeTimers(); const f = fixture(); const original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/agents' ? Promise.reject(new Error('read timed out')) : original(path));
  render(<WorkAgents {...f.props} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(9_000); });
  expect(f.get.mock.calls.filter(([path]) => path === '/api/v1/agents').length).toBeLessThanOrEqual(2);
  expect(f.post).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'Link to this work' })).toBeNull();
  expect(screen.queryByText('No other Agents available. Refresh discovery to check again.')).toBeNull();
});
