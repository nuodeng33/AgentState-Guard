import { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiActionError, type ApiClient } from '../api/client';
import WorkProtectionLinks from './WorkProtectionLinks';
const scope = { id: 'scope-a', source_id: 'docker-a', root: '/project/actual', status: 'PROTECTED' };
const base = '/api/v1/works/work-a/protection-links';
function fixture(initial: Record<string, unknown>[] = []) {
  let revision = 4, links = initial;
  const get = vi.fn(async (path: string): Promise<unknown> => {
    if (path === '/api/v1/protection/scopes') return { items: [scope, { ...scope, id: 'retired', status: 'RETIRED' }] };
    if (path.endsWith('/protection-links')) return { items: links };
    return { revision };
  });
  const post = vi.fn(async (path: string, body: unknown): Promise<unknown> => {
    const command = body as { kind: string; object_id: string; task_id: string | null };
    links = path.endsWith('/unlink') ? [] : [{ ...command, task_id: command.task_id ?? '', basis: 'USER_LINKED', object: scope }];
    revision++;
    return { work_id: 'work-a', revision, scope_id: command.kind === 'scope' ? command.object_id : null };
  });
  const client = { get, post, bootstrap: vi.fn() } as unknown as ApiClient;
  const onClose = vi.fn(), onChanged = vi.fn();
  return { get, post, client, onClose, onChanged, props: { client, workId: 'work-a', onClose, onChanged } };
}
async function ready() { await screen.findByRole('option', { name: /docker-a.*actual/ }); }
async function prepareLink() {
  await ready(); fireEvent.change(screen.getByLabelText('Protected directory to link'), { target: { value: 'scope-a' } });
  fireEvent.click(screen.getByRole('button', { name: 'Link directory' }));
}
async function confirm() { await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Confirm directory link change' })); }); }
afterEach(cleanup);
describe('Work protected directory links', () => {
  it('reads only while visible and links an actual directory only after confirmation', async () => {
    const f = fixture(); const view = render(<WorkProtectionLinks {...f.props} visible={false} />);
    expect(f.get).not.toHaveBeenCalled(); expect(f.post).not.toHaveBeenCalled();
    view.rerender(<WorkProtectionLinks {...f.props} />); await prepareLink();
    expect(screen.queryByRole('option', { name: /RETIRED/ })).toBeNull();
    expect(f.post).not.toHaveBeenCalled(); await confirm();
    expect(f.post.mock.calls[0]).toEqual([base, { request_id: expect.any(String), expected_revision: 4,
      kind: 'scope', object_id: 'scope-a', task_id: null }]);
    expect(f.onChanged).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Directory links refreshed after saving.')).toBeTruthy();
    expect(f.get.mock.calls.every(([path]) => !path.includes('/connect') && !path.includes('/capture'))).toBe(true);
  });
  it('removes the exact existing task-specific session association with a separate confirmation', async () => {
    const f = fixture([{ kind: 'session', object_id: 'observed-session', task_id: 'actual-task', basis: 'USER_LINKED' }]);
    render(<WorkProtectionLinks {...f.props} />); await ready();
    fireEvent.click(screen.getByRole('button', { name: 'Remove link' })); expect(f.post).not.toHaveBeenCalled();
    await confirm(); expect(f.post.mock.calls[0]).toEqual([base + '/unlink', {
      request_id: expect.any(String), expected_revision: 4, kind: 'session', object_id: 'observed-session', task_id: 'actual-task',
    }]);
    expect(f.get).not.toHaveBeenCalledWith(expect.stringContaining('/sessions?'));
  });
  it('preserves the exact unknown command through close and remount and does not automatically replay it', async () => {
    const f = fixture(); f.post.mockRejectedValueOnce(new Error('transport lost'));
    const view = render(<WorkProtectionLinks {...f.props} />); await prepareLink(); await confirm();
    const body = f.post.mock.calls[0][1];
    fireEvent.click(screen.getByRole('button', { name: 'Close directory links' })); expect(f.onClose).toHaveBeenCalled();
    view.unmount(); render(<WorkProtectionLinks {...f.props} />); await ready();
    expect(f.post).toHaveBeenCalledTimes(1);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry same request' })); });
    expect(f.post.mock.calls[1][1]).toEqual(body); expect(f.onChanged).toHaveBeenCalledTimes(1);
  });
  it('only refreshes a confirmed receipt after the authoritative GET fails', async () => {
    const f = fixture(); const normal = f.get.getMockImplementation()!; let reads = 0;
    f.get.mockImplementation(async path => {
      if (path.endsWith('/protection-links') && ++reads === 2) throw new Error();
      return normal(path);
    });
    render(<WorkProtectionLinks {...f.props} />); await prepareLink(); await confirm();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh confirmed request' })); });
    expect(f.post).toHaveBeenCalledTimes(1); expect(f.onChanged).toHaveBeenCalledTimes(1);
  });
  it('refreshes a revision conflict and requires a new explicit confirmation without losing selection', async () => {
    const f = fixture(); f.post.mockRejectedValueOnce(new ApiActionError(409, 'WORK_REVISION_CONFLICT'));
    render(<WorkProtectionLinks {...f.props} />); await prepareLink(); await confirm();
    expect(screen.getByRole('alert').textContent).toContain('WORK_REVISION_CONFLICT');
    expect((screen.getByLabelText('Protected directory to link') as HTMLSelectElement).value).toBe('scope-a');
    expect(screen.queryByRole('button', { name: 'Confirm directory link change' })).toBeNull();
    expect(f.post).toHaveBeenCalledTimes(1); expect(f.onChanged).not.toHaveBeenCalled();
  });
  it('keeps malformed success and a 5xx response pending without inventing confirmation', async () => {
    const f = fixture(); f.post.mockResolvedValueOnce({ work_id: 'wrong-work', revision: 5 });
    f.post.mockRejectedValueOnce(new ApiActionError(503, 'PROTECTION_UNAVAILABLE'));
    render(<WorkProtectionLinks {...f.props} />); await prepareLink(); await confirm();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry same request' })); });
    expect(f.post.mock.calls[0][1]).toEqual(f.post.mock.calls[1][1]);
    expect(screen.getByRole('button', { name: 'Retry same request' })).toBeTruthy();
    expect(f.onChanged).not.toHaveBeenCalled();
  });
  it('suppresses duplicate submission and does not notify a different work after an in-flight result', async () => {
    const f = fixture(); const original = f.post.getMockImplementation()!;
    let release!: () => void;
    f.post.mockImplementationOnce(async (path, body) => { await new Promise<void>(done => { release = done; }); return original(path, body); });
    const view = render(<WorkProtectionLinks {...f.props} />); await prepareLink();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm directory link change' }));
    const retry = screen.getByRole('button', { name: 'Retry same request' }); fireEvent.click(retry); expect(f.post).toHaveBeenCalledTimes(1);
    view.rerender(<WorkProtectionLinks {...f.props} workId="work-b" />);
    await act(async () => { release(); });
    expect(f.onChanged).not.toHaveBeenCalled();
    await waitFor(() => expect((screen.getByRole('button', { name: 'Refresh directory links' }) as HTMLButtonElement).disabled).toBe(false));
  });
  it('ignores an old read generation after switching work', async () => {
    const f = fixture(); const normal = f.get.getMockImplementation()!;
    let release!: (value: unknown) => void;
    f.get.mockImplementation(path => path === base ? new Promise(done => { release = done; }) : normal(path));
    const view = render(<WorkProtectionLinks {...f.props} />);
    view.rerender(<WorkProtectionLinks {...f.props} workId="work-b" />); await ready();
    await act(async () => { release({ items: [{ kind: 'batch', object_id: 'old-work-record', task_id: '' }] }); });
    expect(screen.queryByText(/old-work-record/)).toBeNull();
  });
  it('does not resend an in-flight request after remount or notify a newly opened generation', async () => {
    const f = fixture(); const original = f.post.getMockImplementation()!;
    let release!: () => void;
    f.post.mockImplementationOnce(async (path, body) => { await new Promise<void>(done => { release = done; }); return original(path, body); });
    const view = render(<WorkProtectionLinks {...f.props} />); await prepareLink();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm directory link change' }));
    view.unmount(); const second = render(<WorkProtectionLinks {...f.props} />); await ready();
    fireEvent.click(screen.getByRole('button', { name: 'Retry same request' })); expect(f.post).toHaveBeenCalledTimes(1);
    await act(async () => { release(); }); expect(f.onChanged).not.toHaveBeenCalled();
    // Remount reconciles the already refreshed server records without sending again.
    second.unmount(); render(<WorkProtectionLinks {...f.props} />);
    await screen.findByText(/scope.*actual/); expect(f.post).toHaveBeenCalledTimes(1);
  });
  it('ignores a late result after hiding and reopening the same work', async () => {
    const f = fixture(); let reject!: (error: unknown) => void;
    f.post.mockImplementationOnce(() => new Promise((_done, fail) => { reject = fail; }));
    const view = render(<WorkProtectionLinks {...f.props} />); await prepareLink();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm directory link change' }));
    view.rerender(<WorkProtectionLinks {...f.props} visible={false} />);
    view.rerender(<WorkProtectionLinks {...f.props} />); await ready();
    await act(async () => { reject(new Error('late private diagnostic')); });
    expect(screen.queryByRole('alert')).toBeNull(); expect(f.onChanged).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Retry same request' })).toBeTruthy();
  });
  it('loads under StrictMode effect cleanup and replay', async () => {
    const f = fixture(); render(<StrictMode><WorkProtectionLinks {...f.props} /></StrictMode>);
    await ready(); expect(f.post).not.toHaveBeenCalled();
  });
  it('rejects malformed catalog entries rather than offering invented directory IDs', async () => {
    const f = fixture(); const normal = f.get.getMockImplementation()!;
    f.get.mockImplementation(path => path === '/api/v1/protection/scopes' ? Promise.resolve({ items: [{ root: '/bad' }] }) : normal(path));
    render(<WorkProtectionLinks {...f.props} />); await screen.findByRole('alert');
    expect(screen.queryByRole('option', { name: /bad/ })).toBeNull(); expect(f.post).not.toHaveBeenCalled();
  });
  it('contains keyboard focus, prioritizes Escape and restores the opener when hidden', async () => {
    const f = fixture([{ kind: 'batch', object_id: 'batch-a', task_id: '' }]);
    const view = render(<><button>Panel opener</button><WorkProtectionLinks {...f.props} visible={false} /></>);
    const opener = screen.getByRole('button', { name: 'Panel opener' }); act(() => opener.focus());
    view.rerender(<><button>Panel opener</button><WorkProtectionLinks {...f.props} /></>); await ready();
    const close = screen.getByRole('button', { name: 'Close directory links' });
    expect(document.activeElement).toBe(close);
    expect(screen.getByRole('dialog').getAttribute('aria-modal')).toBe('true');
    expect(screen.getByRole('region', { name: 'Work directory links' })).toBeTruthy();
    const last = screen.getByRole('button', { name: 'Remove link' });
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true }); expect(document.activeElement).toBe(last);
    fireEvent.keyDown(last, { key: 'Tab' }); expect(document.activeElement).toBe(close);
    const navigate = vi.fn(); document.addEventListener('keydown', navigate);
    try {
      fireEvent.keyDown(close, { key: 'Escape' });
      expect(f.onClose).toHaveBeenCalledTimes(1); expect(navigate).not.toHaveBeenCalled();
    } finally { document.removeEventListener('keydown', navigate); }
    view.rerender(<><button>Panel opener</button><WorkProtectionLinks {...f.props} visible={false} /></>);
    expect(document.activeElement).toBe(opener);
  });
  it('Escape closure during an uncertain request keeps its immutable intent for a later panel', async () => {
    const f = fixture(); f.post.mockRejectedValueOnce(new Error('lost reply'));
    const view = render(<WorkProtectionLinks {...f.props} />); await prepareLink(); await confirm();
    const body = f.post.mock.calls[0][1];
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' }); expect(f.onClose).toHaveBeenCalledTimes(1);
    view.unmount(); render(<WorkProtectionLinks {...f.props} />); await ready();
    expect(f.post).toHaveBeenCalledTimes(1);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry same request' })); });
    expect(f.post.mock.calls[1][1]).toEqual(body);
  });
  it('shows read errors without exposing unverified links or allowing writes', async () => {
    const f = fixture(); f.get.mockRejectedValue(new Error('private diagnostic'));
    render(<WorkProtectionLinks {...f.props} />); await screen.findByRole('alert');
    expect(screen.queryByText('private diagnostic')).toBeNull(); expect(f.post).not.toHaveBeenCalled();
    expect((screen.getByRole('button', { name: 'Link directory' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
