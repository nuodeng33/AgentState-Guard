import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiActionError, type ApiClient } from '../api/client';
import WorkEditor from './WorkEditor';

const work = (id = 'work-a') => ({ id, title: 'Original work', owner: 'Owner', revision: 7,
  requirement: 'Original requirement', requirement_version: 3,
  tasks: [{ id: 'task-a', title: 'First task' }, { id: 'task-b', title: 'Second task' }] });
function fixture(id = 'work-a') {
  const current = work(id);
  const get = vi.fn(async (path: string): Promise<unknown> => path.endsWith('/tasks/task-a') ? {
    id: 'task-a', work_id: id, title: 'First task', body: 'Original task body', assignee: 'Person', depends_on: ['task-b'],
  } : current);
  const patch = vi.fn(async (_path: string, _body: unknown) => ({ work_id: id, revision: 8 }));
  const put = vi.fn(async (_path: string, _body: unknown) => ({ work_id: id, revision: 8, task_id: 'task-a' }));
  const post = vi.fn(async (_path: string, _body: unknown) => ({ work_id: id, revision: 8, requirement_version: 4 }));
  const props = { work: current, client: { get, post, patch, put } as unknown as ApiClient,
    onClose: vi.fn(), onChanged: vi.fn(async () => {}) };
  return { props, get, patch, put, post };
}
afterEach(cleanup);

describe('existing Work editing commands', () => {
  it('PATCHes only work information with its original CAS revision', async () => {
    const f = fixture(); render(<WorkEditor {...f.props} mode="work" />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('Work title'), { target: { value: 'Updated work' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(f.props.onClose).toHaveBeenCalledOnce());
    expect(f.patch.mock.calls[0]).toEqual(['/api/v1/works/work-a', {
      request_id: expect.any(String), expected_revision: 7, title: 'Updated work', owner: 'Owner',
    }]);
    expect(f.post).not.toHaveBeenCalled(); expect(f.put).not.toHaveBeenCalled();
  });

  it('adds a new requirement version without changing the old version', async () => {
    const f = fixture(); render(<WorkEditor {...f.props} mode="requirement" />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save new version' }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByText(/current v3/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Requirement'), { target: { value: 'Revised requirement' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save new version' }));
    await waitFor(() => expect(f.props.onChanged).toHaveBeenCalledOnce());
    expect(f.post.mock.calls[0]).toEqual(['/api/v1/works/work-a/requirements', {
      request_id: expect.any(String), expected_revision: 7, body: 'Revised requirement',
    }]);
    expect(f.props.work.requirement).toBe('Original requirement');
    expect(f.patch).not.toHaveBeenCalled();
  });

  it('loads full task content and retains dependencies absent from the supplied summary', async () => {
    const f = fixture(); f.get.mockImplementation(async path => path.endsWith('/tasks/task-a') ? {
      id: 'task-a', work_id: 'work-a', title: 'First task', body: 'Original task body', assignee: 'Person', depends_on: ['task-b', 'task-not-loaded'],
    } : { ...work(), revision: 9 });
    f.put.mockResolvedValue({ work_id: 'work-a', revision: 10, task_id: 'task-a' });
    render(<WorkEditor {...f.props} mode="task" taskId="task-a" />);
    await screen.findByDisplayValue('Original task body');
    expect(screen.queryByRole('checkbox', { name: 'First task' })).toBeNull();
    expect((screen.getByRole('checkbox', { name: 'Second task' }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/task-not-loaded/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Content'), { target: { value: 'Updated body' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(f.props.onChanged).toHaveBeenCalledOnce());
    expect(f.put.mock.calls[0]).toEqual(['/api/v1/works/work-a/tasks/task-a', {
      request_id: expect.any(String), expected_revision: 9, title: 'First task', body: 'Updated body', assignee: 'Person',
      depends_on: ['task-b', 'task-not-loaded'],
    }]);
  });

  it('blocks task mutation on load failure and supports explicit reread', async () => {
    const f = fixture(); f.get.mockRejectedValueOnce(new Error('private path'));
    render(<WorkEditor {...f.props} mode="task" taskId="task-a" />);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByText('private path')).toBeNull();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Reload current content (replaces draft)' }));
    await screen.findByDisplayValue('Original task body');
    expect(f.put).not.toHaveBeenCalled();
  });

  it('preserves an unknown immutable command across close and retries exactly the same UUID/body', async () => {
    const f = fixture('unknown-work'); f.patch.mockRejectedValueOnce(new Error('response lost'));
    const view = render(<WorkEditor {...f.props} mode="work" />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('Work title'), { target: { value: 'Unknown write' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
    const command = f.patch.mock.calls[0];
    expect(screen.getByLabelText('Work title').matches(':disabled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Close editor' })); view.unmount();
    render(<WorkEditor {...f.props} mode="work" />);
    expect(screen.getByDisplayValue('Unknown write')).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry same submission' })); });
    expect(f.patch.mock.calls[1]).toEqual(command);
    expect(f.patch).toHaveBeenCalledTimes(2);
  });

  it('keeps the rejected draft on conflict and reloads explicitly before a fresh CAS command', async () => {
    const f = fixture(); f.patch.mockRejectedValueOnce(new ApiActionError(409, 'WORK_REVISION_CONFLICT'));
    f.get.mockResolvedValue({ ...work(), title: 'Other update', revision: 11 });
    f.patch.mockResolvedValueOnce({ work_id: 'work-a', revision: 12 });
    render(<WorkEditor {...f.props} mode="work" />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('Work title'), { target: { value: 'My draft' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
    expect(screen.getByDisplayValue('My draft')).toBeTruthy();
    expect(screen.getByText(/WORK_REVISION_CONFLICT/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reload current content (replaces draft)' })); });
    fireEvent.change(screen.getByLabelText('Work title'), { target: { value: 'Reviewed draft' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
    expect(f.patch.mock.calls[1][1]).toMatchObject({ expected_revision: 11 });
    expect(f.patch.mock.calls[0][1]).not.toEqual(f.patch.mock.calls[1][1]);
  });

  it('after a confirmed mutation retries only parent refresh if refresh fails', async () => {
    const f = fixture(); f.props.onChanged.mockRejectedValueOnce(new Error('refresh lost'));
    render(<WorkEditor {...f.props} mode="work" />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
    expect(await screen.findByRole('button', { name: 'Refresh saved result' })).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh saved result' })); });
    expect(f.patch).toHaveBeenCalledTimes(1); expect(f.props.onChanged).toHaveBeenCalledTimes(2);
  });

  it('fails visibly if PATCH is unsupported and does not emulate it through POST', async () => {
    const f = fixture(); const client = { get: f.get, post: f.post } as unknown as ApiClient;
    render(<WorkEditor {...f.props} client={client} mode="work" />);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    expect(f.post).not.toHaveBeenCalled();
  });

  it('shows safe sensitive-content rejection while preserving editable form', async () => {
    const f = fixture(); f.patch.mockRejectedValueOnce(new ApiActionError(422, 'WORK_CONTENT_SENSITIVE', { message: 'private secret' }));
    render(<WorkEditor {...f.props} mode="work" />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
    expect(screen.getByText(/WORK_CONTENT_SENSITIVE/)).toBeTruthy();
    expect(screen.queryByText('private secret')).toBeNull();
    expect((screen.getByLabelText('Work title') as HTMLInputElement).disabled).toBe(false);
  });
  it('selects an existing task and loads its detail without creating a task', async () => {
    const f = fixture(); const original = f.get.getMockImplementation()!;
    f.get.mockImplementation(async path => path.endsWith('/tasks/task-b') ? {
      id: 'task-b', work_id: 'work-a', title: 'Second task', body: 'Second full body', assignee: null, depends_on: [],
    } : original(path));
    f.put.mockResolvedValue({ work_id: 'work-a', revision: 8, task_id: 'task-b' });
    render(<WorkEditor {...f.props} mode="task" />);
    await screen.findByDisplayValue('Original task body');
    fireEvent.change(screen.getByLabelText('Task to edit'), { target: { value: 'task-b' } });
    await screen.findByDisplayValue('Second full body');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
    expect(f.put.mock.calls[0][0]).toBe('/api/v1/works/work-a/tasks/task-b');
    expect(f.post).not.toHaveBeenCalled();
  });

  it('switching Work discards stale async task reads and shows only the new target', async () => {
    const first = fixture(); let resolve!: (value: unknown) => void;
    const original = first.get.getMockImplementation()!;
    first.get.mockImplementation(path => path.endsWith('/tasks/task-a') ? new Promise(done => { resolve = done; }) : original(path));
    const view = render(<WorkEditor {...first.props} mode="task" taskId="task-a" />);
    const second = fixture('work-b'); second.get.mockResolvedValue({ ...second.props.work, title: 'New Work' });
    view.rerender(<WorkEditor {...second.props} mode="work" work={{ ...second.props.work, title: 'New Work' }} />);
    await act(async () => resolve({ id: 'task-a', work_id: 'work-a', title: 'Stale task', body: 'Private old content', assignee: null, depends_on: [] }));
    expect(screen.getByDisplayValue('New Work')).toBeTruthy();
    expect(screen.queryByDisplayValue('Private old content')).toBeNull();
    expect(first.put).not.toHaveBeenCalled();
  });

  it('traps keyboard focus and restores its opener on explicit Escape close', async () => {
    const f = fixture(); const opener = document.createElement('button');
    document.body.append(opener); opener.focus();
    const view = render(<WorkEditor {...f.props} mode="work" />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false));
    const close = screen.getByRole('button', { name: 'Close editor' });
    const save = screen.getByRole('button', { name: 'Save' });
    // Inputs appear before footer controls in the tab loop; Shift+Tab wraps
    // from the first Close button to the final Save action.
    close.focus(); fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(save);
    fireEvent.keyDown(document, { key: 'Tab' }); expect(document.activeElement).toBe(close);
    fireEvent.keyDown(document, { key: 'Escape' }); expect(f.props.onClose).toHaveBeenCalledOnce();
    view.unmount(); expect(document.activeElement).toBe(opener); opener.remove();
  });

  it('synchronously blocks duplicate writes and Escape closure while a mutation is in flight', async () => {
    const f = fixture(); let resolve!: (value: { work_id: string; revision: number }) => void;
    f.patch.mockImplementation(() => new Promise(done => { resolve = done; }));
    render(<WorkEditor {...f.props} mode="work" />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false));
    const form = screen.getByRole('button', { name: 'Save' }).closest('form')!;
    fireEvent.submit(form); fireEvent.submit(form); fireEvent.keyDown(document, { key: 'Escape' });
    expect(f.patch).toHaveBeenCalledTimes(1); expect(f.props.onClose).not.toHaveBeenCalled();
    await act(async () => resolve({ work_id: 'work-a', revision: 8 }));
    expect(f.props.onClose).toHaveBeenCalledOnce();
  });

  it('never transfers an unresolved command to a different API client with the same Work id', async () => {
    const first = fixture('shared-work'); first.patch.mockRejectedValueOnce(new Error());
    const view = render(<WorkEditor {...first.props} mode="work" />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText('Work title'), { target: { value: 'Private pending draft' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
    view.unmount();
    const second = fixture('shared-work');
    render(<WorkEditor {...second.props} mode="work" />);
    await waitFor(() => expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByDisplayValue('Original work')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Retry same submission' })).toBeNull();
    expect(second.patch).not.toHaveBeenCalled();
  });

});
