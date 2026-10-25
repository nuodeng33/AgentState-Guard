import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import WorkPage, { type Board } from './WorkPage';
const id = '11111111-1111-4111-8111-111111111111';
const otherId = '22222222-2222-4222-8222-222222222222';
const work = (workId = id) => ({ id: workId, title: workId === id ? 'First work' : 'Second work', owner: 'Owner',
  revision: 3, requirement_version: 1, requirement: 'Actual requirement', tasks: [{ id: 'task-a', title: 'Actual task' }], artifacts: [] });
const board = (workId = id): Board => ({ schema_version: 1, surface: 'board', viewport: { x: 72, y: 39, zoom: .8 },
  cards: [{ kind: 'requirement', ref_id: workId, version: 1, x: 0, y: 0, width: 400, height: 430, scroll: 0 }] });
function deferred<T>() { let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function fixture(view = Promise.resolve({ revision: 8, state: board() })) {
  const get = vi.fn(async (path: string): Promise<unknown> => {
    if (path.includes('/works?')) return { items: [work(), work(otherId)] };
    if (path.endsWith('/execution-summary')) return { counts: {}, items: [] };
    if (path.endsWith('/agents') || path.endsWith('/agent-appearances')) return { items: [] };
    if (path.endsWith('/protection-links')) return { items: [] };
    if (path.endsWith('/view')) return path.includes(otherId) ? { revision: 4, state: board(otherId) } : view;
    if (path.includes('/requirements/')) return { body: 'Actual requirement' };
    if (path.includes('/tasks/')) return { title: 'Actual task', body: 'Actual task body', assignee: 'Actual assignee', depends_on: ['dependency-id'] };
    return path.includes(otherId) ? work(otherId) : work();
  });
  const post = vi.fn(async (_path: string, _body: unknown): Promise<unknown> => ({ revision: 9 }));
  history.replaceState(null, '', '/?work=' + id);
  return { get, post, client: { get, post } as unknown as ApiClient };
}
afterEach(() => { cleanup(); vi.useRealTimers(); history.replaceState(null, '', '/'); });

it('starts the saved layout read alongside details and never writes a fabricated revision before hydration', async () => {
  const layout = deferred<{ revision: number; state: Board }>(); const detail = deferred<unknown>();
  const f = fixture(layout.promise), original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/works/' + id ? detail.promise : original(path));
  render(<WorkPage client={f.client} />);
  expect(f.get).toHaveBeenCalledWith('/api/v1/works/' + id + '/view');
  await act(async () => detail.resolve(work()));
  const save = screen.getByRole('button', { name: 'Save workspace' }) as HTMLButtonElement;
  expect(save.disabled).toBe(true);
  expect(screen.queryByText('Workspace saved')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Continue workbench' })); fireEvent.click(save);
  expect(f.post).not.toHaveBeenCalled();
  await act(async () => layout.resolve({ revision: 8, state: board() }));
  await screen.findByText('Actual requirement');
  fireEvent.click(screen.getByRole('button', { name: 'Zoom in' })); fireEvent.click(screen.getByRole('button', { name: 'Save workspace' }));
  await waitFor(() => expect(f.post).toHaveBeenCalledWith('/api/v1/works/' + id + '/view', expect.objectContaining({ expected_revision: 8 })));
});

it('keeps failed layout reads read-only until an explicit successful reload', async () => {
  const layout = deferred<{ revision: number; state: Board }>(); const f = fixture(layout.promise);
  render(<WorkPage client={f.client} />);
  await act(async () => layout.reject(new Error('private layout error')));
  expect((screen.getByRole('button', { name: 'Save workspace' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByText('private layout error')).toBeNull();
  const original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path.endsWith('/view') ? Promise.resolve({ revision: 12, state: board() }) : original(path));
  fireEvent.click(screen.getByRole('button', { name: 'Reload workspace' }));
  await screen.findByText('Actual requirement');
  fireEvent.click(screen.getByRole('button', { name: 'Zoom in' })); fireEvent.click(screen.getByRole('button', { name: 'Save workspace' }));
  await waitFor(() => expect(f.post).toHaveBeenCalledWith('/api/v1/works/' + id + '/view', expect.objectContaining({ expected_revision: 12 })));
});

it('uses unique panel keys and opens/closes exactly one protection panel across work switches', async () => {
  const f = fixture(); const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    render(<WorkPage client={f.client} />); await screen.findByText('Actual requirement');
    const open = () => { fireEvent.click(screen.getByRole('button', { name: 'File' })); fireEvent.click(screen.getByRole('button', { name: 'Protected directories' })); };
    open(); expect(screen.getAllByRole('dialog', { name: 'Work directory links' })).toHaveLength(1);
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Work directory links' })).getByRole('button', { name: 'Close directory links' }));
    expect(screen.queryByRole('dialog', { name: 'Work directory links' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Second work' })); await screen.findByText('Actual requirement');
    open(); expect(screen.getAllByRole('dialog', { name: 'Work directory links' })).toHaveLength(1);
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Work directory links' })).getByRole('button', { name: 'Close directory links' }));
    expect(screen.queryByRole('dialog', { name: 'Work directory links' })).toBeNull();
    expect(errors.mock.calls.filter(args => args.some(value => String(value).includes('same key')))).toEqual([]);
  } finally { errors.mockRestore(); }
});

it('deduplicates an unresolved layout when revisiting a Work and ignores its late result while another Work is selected', async () => {
  const layout = deferred<{ revision: number; state: Board }>(); const f = fixture(layout.promise);
  render(<WorkPage client={f.client} />);
  await screen.findByRole('button', { name: 'Continue workbench' });
  fireEvent.click(await screen.findByRole('button', { name: 'Second work' }));
  await screen.findByText('Actual requirement');
  fireEvent.click(screen.getByRole('button', { name: 'First work' }));
  await screen.findByRole('button', { name: 'Continue workbench' });
  expect(f.get.mock.calls.filter(([path]) => path === '/api/v1/works/' + id + '/view')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Second work' }));
  await screen.findByText('Actual requirement');
  const before = document.querySelector('.work-world')!.getAttribute('style');
  await act(async () => layout.resolve({ revision: 8, state: board() }));
  expect(location.search).toContain(otherId);
  expect(document.querySelector('.work-world')!.getAttribute('style')).toBe(before);
  expect(f.post.mock.calls.every(([path]) => !String(path).includes(id))).toBe(true);
});

it('does not reuse a different Core client layout revision for the same Work id', async () => {
  const first = fixture(); const view = render(<WorkPage client={first.client} />);
  await screen.findByText('Actual requirement');
  const pending = deferred<{ revision: number; state: Board }>(); const second = fixture(pending.promise);
  view.rerender(<WorkPage client={second.client} />);
  await screen.findByRole('button', { name: 'Continue workbench' });
  expect((screen.getByRole('button', { name: 'Save workspace' }) as HTMLButtonElement).disabled).toBe(true);
  expect(second.post).not.toHaveBeenCalled();
  await act(async () => pending.resolve({ revision: 21, state: board() }));
  await screen.findByText('Actual requirement');
  fireEvent.click(screen.getByRole('button', { name: 'Zoom in' })); fireEvent.click(screen.getByRole('button', { name: 'Save workspace' }));
  await waitFor(() => expect(second.post).toHaveBeenCalledWith('/api/v1/works/' + id + '/view', expect.objectContaining({ expected_revision: 21 })));
});

it('shares a pending task body between the content card and focused reader and displays real assignment/dependencies', async () => {
  const content = deferred<unknown>(); const f = fixture(Promise.resolve({ revision: 8, state: {
    ...board(), cards: [...board().cards, { kind: 'task', ref_id: 'task-a', version: null, x: 430, y: 0, width: 400, height: 430, scroll: 0 }],
  } }));
  const original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path.endsWith('/tasks/task-a') ? content.promise : original(path));
  render(<WorkPage client={f.client} />);
  fireEvent.click(await screen.findByRole('button', { name: /Focus reading Task/ }));
  await act(async () => {});
  expect(f.get.mock.calls.filter(([path]) => path.endsWith('/tasks/task-a'))).toHaveLength(1);
  await act(async () => content.resolve({ title: 'Actual task', body: 'Actual task body', assignee: 'Actual assignee', depends_on: ['dependency-id'] }));
  expect(screen.getAllByText('Actual task body').length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText('Actual assignee').length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText('dependency-id').length).toBeGreaterThanOrEqual(1);
});

it('retains readable Work facts when a malformed layout response is rejected', async () => {
  const f = fixture(Promise.resolve({ revision: 8, state: { ...board(), space: { nodes: null } } as unknown as Board }));
  render(<WorkPage client={f.client} />);
  await screen.findByRole('button', { name: 'Reload workspace' });
  expect(location.search).toContain(id);
  expect(screen.getByRole('button', { name: 'Continue workbench' })).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Save workspace' }) as HTMLButtonElement).disabled).toBe(true);
  expect(f.post).not.toHaveBeenCalled();
});

it('finishes a confirmed new-Work form after details arrive while its layout still hydrates read-only', async () => {
  const f = fixture(); const original = f.get.getMockImplementation()!;
  const newId = '33333333-3333-4333-8333-333333333333';
  const layout = deferred<{ revision: number; state: Board }>();
  f.get.mockImplementation(path => path === '/api/v1/works/' + newId + '/view' ? layout.promise :
    path === '/api/v1/works/' + newId ? Promise.resolve({ ...work(newId), title: 'Created new work' }) : original(path));
  f.post.mockImplementation(async path => path === '/api/v1/works' ? { work_id: newId } : { revision: 9 });
  render(<WorkPage client={f.client} />); await screen.findByText('Actual requirement');
  fireEvent.click(screen.getByRole('button', { name: 'New work' }));
  const form = screen.getByRole('dialog', { name: 'Create work' });
  fireEvent.change(within(form).getByLabelText('Work title'), { target: { value: 'Created new work' } });
  fireEvent.change(within(form).getByLabelText('Owner'), { target: { value: 'Owner' } });
  fireEvent.change(within(form).getByLabelText('Requirement'), { target: { value: 'Actual requirement' } });
  await act(async () => { fireEvent.click(within(form).getByRole('button', { name: 'Save' })); });
  expect(location.search).toContain(newId);
  expect(screen.queryByRole('dialog', { name: 'Create work' })).toBeNull();
  expect((screen.getByRole('button', { name: 'Save workspace' }) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => layout.resolve({ revision: 0, state: board(newId) }));
});

it('derives relations from existing saved layouts without scheduling a write', async () => {
  vi.useFakeTimers(); const f = fixture();
  render(<WorkPage client={f.client} />);
  await act(async () => {});
  expect(screen.getByText('Actual requirement')).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(600); });
  expect(f.post).not.toHaveBeenCalled();
});

it('does not announce zero linked Agents until a delayed authoritative empty read succeeds', async () => {
  const linked = deferred<unknown>(); const f = fixture(), original = f.get.getMockImplementation()!;
  f.get.mockImplementation(path => path === '/api/v1/works/' + id + '/agents' ? linked.promise : original(path));
  render(<WorkPage client={f.client} />);
  await screen.findByText('Actual requirement');
  const dock = screen.getByRole('button', { name: 'Linked agents' });
  expect(dock.textContent).toBe('…');
  expect(dock.title).toBe('Reading Agent records…');
  await act(async () => linked.resolve({ items: [] }));
  expect(dock.textContent).toBe('0');
  expect(dock.title).toBe('Linked agents');
});
