import { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiActionError, ApiRequestError, type ApiClient } from '../api/client';
import { I18nProvider, useI18n } from '../i18n/I18nProvider';
import WorkRecovery from './WorkRecovery';

const file = (id: string, category = 'restorable') => ({ file_id: id, relative_path: `${id}.txt`, category });
const version = { id: 'version-1', available: 1, metadata_available: true, details_included: true,
  created_at: '2026-10-23T00:00:00Z', files: [file('A'), file('B'), file('secret', 'audit_only')] };
const operation = { id: 'op-1', kind: 'RESTORE', scope_id: 'scope-a', state: 'SUCCEEDED', created_at: '2026-10-23T00:00:00Z',
  steps: [{ relative_path: 'A.txt', before: file('A'), after: file('A') }] };
function fixture() {
  const get = vi.fn(async (path: string): Promise<unknown> => {
    if (path === '/api/v1/protection/scopes') return { items: [
      { id: 'scope-a', source_id: 'local', root: '/project/a', status: 'PROTECTED', enabled: true },
      { id: 'scope-b', source_id: 'other', root: '/unrelated/b', status: 'PROTECTED', enabled: true }] };
    if (path.includes('/versions?')) return { items: [version] };
    if (path.includes('/recovery/operations?')) return { items: [operation] };
    if (path.endsWith('/operations/op-1')) return operation;
    if (path.includes('/diff')) return { text_diff: '-changed-A\n+original-A\n', binary: false, truncated: true };
    throw new Error('Unexpected read: ' + path);
  });
  const post = vi.fn(async (path: string, body: unknown): Promise<unknown> => {
    if (path.endsWith('/previews')) return { preview_id: 'preview-1', digest: 'a'.repeat(64), status: 'READY', scope_id: 'scope-a',
      expires_at: new Date(Date.now() + 300000).toISOString(), selection: (body as { file_ids: string[] }).file_ids.map(id => ({ ...file(id), before: file(id), after: file(id) })), affected_work_ids: ['another-work'] };
    if (path.endsWith('/operations')) return operation;
    throw new Error('Unexpected write');
  });
  const props = { client: { get, post, bootstrap: vi.fn() } as unknown as ApiClient, visible: true, onClose: vi.fn() };
  return { props, get, post };
}
async function selectFiles() {
  await screen.findByRole('option', { name: /\/project\/a/ });
  fireEvent.change(screen.getByLabelText('Protected directory'), { target: { value: 'scope-a' } });
  await screen.findByRole('option', { name: /version-1/ });
  fireEvent.change(screen.getByLabelText('Directory version'), { target: { value: 'version-1' } });
  fireEvent.click(await screen.findByLabelText('A.txt'));
}
async function preview() {
  await selectFiles();
  fireEvent.click(screen.getByRole('button', { name: 'Preview restore' }));
  await screen.findByRole('button', { name: 'Confirm restore' });
}
afterEach(() => { cleanup(); localStorage.clear(); vi.useRealTimers(); Object.defineProperty(document, 'hidden', { configurable: true, value: false }); });

describe('native file recovery', () => {
  it.each(['success', 'failure'])('shows preview preparation until %s', async outcome => {
    const { props, post } = fixture();
    let finish!: (value: unknown) => void; let fail!: (error: Error) => void;
    post.mockImplementationOnce(() => new Promise((resolve, reject) => { finish = resolve; fail = reject; }));
    render(<WorkRecovery {...props} />); await selectFiles();
    fireEvent.click(screen.getByRole('button', { name: 'Preview restore' }));
    expect(screen.getByText('Preparing recovery preview...')).toBeTruthy();
    await act(async () => {
      if (outcome === 'failure') fail(new Error('failed'));
      else finish({ preview_id: 'p', digest: 'a'.repeat(64), status: 'READY', expires_at: new Date(Date.now() + 300000).toISOString(), selection: [{ ...file('A'), before: file('A'), after: file('A') }], affected_work_ids: [] });
    });
    expect(screen.queryByText('Preparing recovery preview...')).toBeNull();
    if (outcome === 'success') expect(screen.getByRole('button', { name: 'Confirm restore' })).toBeTruthy();
    else expect(screen.getByRole('alert')).toBeTruthy();
  });
  it('blocks recovery while a directory policy result is unconfirmed', async () => {
    const { props, post } = fixture(); const command = { kind: 'configure' as const, sourceId: 'local', path: '/project/a', scopeId: 'scope-a',
      payload: { request_id: 'same-policy-request', expected_revision: 1, enabled: true, retention_days: 30, quota_bytes: 2048 * 1048576 } };
    render(<WorkRecovery {...props} protectionCommand={command} onProtectionCommandChange={vi.fn()} />);
    await screen.findByRole('option', { name: /project\/a/ });
    expect((screen.getByLabelText('Protected directory') as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Preview restore' }) as HTMLButtonElement).disabled).toBe(true);
    expect(post).not.toHaveBeenCalled();
  });

  it('offers all directories without Work links and leaves files unselected', async () => {
    const { props, get } = fixture(); render(<WorkRecovery {...props} />);
    await selectFiles();
    expect(screen.getByRole('option', { name: /\/unrelated\/b/ })).toBeTruthy();
    expect((screen.getByLabelText('B.txt') as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText(/secret.txt/) as HTMLInputElement).disabled).toBe(true);
    expect(get.mock.calls.some(([path]) => path.includes('/works/'))).toBe(false);
  });
  it('previews without applying, shows plain diff and affected works, then restores selected files only', async () => {
    const { props, post } = fixture(); render(<WorkRecovery {...props} />); await preview();
    expect(post.mock.calls.filter(([path]) => path.endsWith('/operations'))).toHaveLength(0);
    expect(screen.getByText('another-work')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'View diff A.txt' }));
    expect(await screen.findByText(/-changed-A/)).toBeTruthy();
    expect(screen.getByText('Diff truncated')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    expect(await screen.findByText('Restore succeeded')).toBeTruthy();
    expect(post.mock.calls[0][1]).toMatchObject({ scope_id: 'scope-a', version_id: 'version-1', file_ids: ['A'], mode: 'restore_version' });
  });
  it('invalidates a preview when the file selection changes', async () => {
    const { props } = fixture(); render(<WorkRecovery {...props} />); await preview();
    fireEvent.click(screen.getByLabelText('B.txt'));
    expect(screen.queryByRole('button', { name: 'Confirm restore' })).toBeNull();
  });
  it('retries an unconfirmed submission using the identical command', async () => {
    const { props, post } = fixture(); render(<WorkRecovery {...props} />); await preview();
    post.mockRejectedValueOnce(new ApiRequestError('network'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByText(/Result not confirmed/);
    fireEvent.click(screen.getByRole('button', { name: 'Retry same submission' }));
    await screen.findByText('Restore succeeded');
    const commands = post.mock.calls.filter(([path]) => path.endsWith('/operations'));
    expect(commands).toHaveLength(2); expect(commands[0][1]).toEqual(commands[1][1]);
  });
  it('invalidates a rejected expired preview rather than silently replacing it', async () => {
    const { props, post } = fixture(); render(<WorkRecovery {...props} />); await preview();
    post.mockRejectedValueOnce(new ApiActionError(410, 'PROTECTION_PREVIEW_EXPIRED'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByText(/Preview expired/);
    expect(screen.queryByRole('button', { name: 'Confirm restore' })).toBeNull();
  });
  it('loads successful operation files and previews undo before executing', async () => {
    const { props, post } = fixture(); render(<WorkRecovery {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: /Undo op-1/ }));
    fireEvent.click(await screen.findByLabelText('A.txt'));
    fireEvent.click(screen.getByRole('button', { name: 'Preview undo' }));
    await screen.findByRole('button', { name: 'Confirm undo' });
    expect(post.mock.calls[0][1]).toMatchObject({ mode: 'undo_operation', operation_id: 'op-1', file_ids: ['A'] });
    expect(post.mock.calls.some(([path]) => path.endsWith('/operations'))).toBe(false);
  });
  it.each(['REJECTED', 'COMPENSATED', 'NEEDS_ATTENTION', 'UNRECOGNIZED'])('does not label %s as success', async state => {
    const { props, post } = fixture(); render(<WorkRecovery {...props} />); await preview();
    post.mockResolvedValueOnce({ ...operation, state });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await waitFor(() => expect(screen.getByTestId('recovery-result').textContent).not.toContain('Restore succeeded'));
    expect(screen.queryByText('Restore succeeded')).toBeNull();
  });
  it('does not load while hidden and resumes operation polling only when visible', async () => {
    const { props, get, post } = fixture(); const view = render(<WorkRecovery {...props} visible={false} />);
    expect(get).not.toHaveBeenCalled(); view.rerender(<WorkRecovery {...props} />); await preview();
    post.mockResolvedValueOnce({ ...operation, state: 'QUEUED' });
    get.mockImplementation(async path => path.endsWith('/op-1') ? { ...operation, state: 'RUNNING' } : { items: [] });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByTestId('recovery-result');
    view.rerender(<WorkRecovery {...props} visible={false} />);
    const before = get.mock.calls.length;
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
    expect(get.mock.calls.length).toBe(before);
    view.rerender(<WorkRecovery {...props} />);
    await waitFor(() => expect(get.mock.calls.length).toBeGreaterThan(before));
  });
  it('changes UI language while preserving original paths and file selection', async () => {
    const { props } = fixture();
    function Switch() { const { setPreference } = useI18n(); return <button onClick={() => setPreference('zh-CN')}>中文</button>; }
    render(<I18nProvider><Switch /><WorkRecovery {...props} /></I18nProvider>); await selectFiles();
    fireEvent.click(screen.getByText('中文'));
    expect(screen.getByRole('button', { name: '预览恢复' })).toBeTruthy();
    expect((screen.getByLabelText('A.txt') as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole('option', { name: /\/unrelated\/b/ })).toBeTruthy();
  });
  it('pages versions and files while preserving selected file ids', async () => {
    const { props, get } = fixture(); const original = get.getMockImplementation()!;
    get.mockImplementation(async path => {
      if (path.includes('/versions?')) return { items: path.includes('offset=50') ? [{ ...version, id: 'older-version' }] :
        [{ ...version, files: Array.from({ length: 51 }, (_, i) => file(`F${i}`)) }, ...Array.from({ length: 49 }, (_, i) => ({ ...version, id: `v${i}` }))] };
      return original(path);
    });
    render(<WorkRecovery {...props} />);
    await screen.findByRole('option', { name: /project\/a/ });
    fireEvent.change(screen.getByLabelText('Protected directory'), { target: { value: 'scope-a' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Load more versions' }));
    await screen.findByRole('option', { name: /older-version/ });
    fireEvent.change(screen.getByLabelText('Directory version'), { target: { value: 'version-1' } });
    fireEvent.click(await screen.findByLabelText('F0.txt'));
    fireEvent.click(screen.getByRole('button', { name: 'Next files' }));
    fireEvent.click(screen.getByLabelText('F50.txt'));
    fireEvent.click(screen.getByRole('button', { name: 'Previous files' }));
    expect((screen.getByLabelText('F0.txt') as HTMLInputElement).checked).toBe(true);
  });
  it('can load another global history page containing restore operations after a source-only page', async () => {
    const { props, get } = fixture(); const original = get.getMockImplementation()!;
    get.mockImplementation(async path => path.includes('/operations?') ? { items: path.includes('offset=50') ? [operation] :
      Array.from({ length: 50 }, (_, i) => ({ ...operation, id: `source-${i}`, kind: 'SOURCE' })) } : original(path));
    render(<WorkRecovery {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Load more records' }));
    await screen.findByRole('button', { name: 'Undo op-1' });
  });
  it('blocks duplicate clicks while confirmation is in flight', async () => {
    const { props, post } = fixture(); render(<WorkRecovery {...props} />); await preview();
    let complete!: (value: unknown) => void;
    post.mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
    const confirm = screen.getByRole('button', { name: 'Confirm restore' });
    fireEvent.click(confirm); fireEvent.click(confirm);
    expect(post.mock.calls.filter(([path]) => path.endsWith('/operations'))).toHaveLength(1);
    await act(async () => complete(operation));
    await screen.findByText('Restore succeeded');
  });
  it('blocks an expired new confirmation but still allows retry of an ambiguous original command', async () => {
    const { props, post } = fixture(); render(<WorkRecovery {...props} />); await preview();
    post.mockRejectedValueOnce(new ApiRequestError('network'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByText(/Result not confirmed/);
    vi.useFakeTimers(); vi.setSystemTime(Date.now() + 360000);
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect((screen.getByRole('button', { name: 'Confirm restore' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Retry same submission' }) as HTMLButtonElement).disabled).toBe(false);
    vi.useRealTimers();
  });
  it('invalidates confirmation when the selected directory changes', async () => {
    const { props } = fixture(); render(<WorkRecovery {...props} />); await preview();
    fireEvent.change(screen.getByLabelText('Protected directory'), { target: { value: 'scope-b' } });
    expect(screen.queryByRole('button', { name: 'Confirm restore' })).toBeNull();
    await waitFor(() => expect(screen.getByLabelText('Directory version')).toBeTruthy());
  });
  it('shows an empty directory catalog without making writes', async () => {
    const { props, get, post } = fixture(); get.mockResolvedValue({ items: [] });
    render(<WorkRecovery {...props} />);
    await screen.findByText('No protected directories available.');
    expect(post).not.toHaveBeenCalled();
  });
  it('marks binary differences without trying to render them as text', async () => {
    const { props, get } = fixture(); render(<WorkRecovery {...props} />); await preview();
    get.mockResolvedValueOnce({ text_diff: null, binary: true });
    fireEvent.click(screen.getByRole('button', { name: 'View diff A.txt' }));
    await screen.findByText('Binary file; text diff unavailable.');
  });

  it('loads versions and previews under StrictMode effect remounts', async () => {
    const { props } = fixture(); render(<StrictMode><WorkRecovery {...props} /></StrictMode>);
    await preview();
    expect(screen.getByRole('button', { name: 'Confirm restore' })).toBeTruthy();
  });

  it('keeps keyboard focus on refresh rather than moving it to the close button', async () => {
    const { props } = fixture(); render(<WorkRecovery {...props} />);
    await screen.findByRole('option', { name: /project\/a/ });
    const refresh = screen.getByRole('button', { name: 'Refresh records' });
    await act(async () => { refresh.focus(); fireEvent.click(refresh); });
    expect(document.activeElement).toBe(refresh);
  });

  it('shows successful verification without a contradictory failure message', async () => {
    const { props, post } = fixture(); render(<WorkRecovery {...props} />); await preview();
    post.mockResolvedValueOnce({ ...operation, reason_code: 'PROTECTION_RESTORED_AND_VERIFIED' });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByText('Restore succeeded');
    expect(screen.queryByText(/Unable to read or prepare recovery/)).toBeNull();
  });
  it('finds an ambiguous submission receipt by exact request_id when records refresh', async () => {
    const { props, get, post } = fixture(); const original = get.getMockImplementation()!;
    render(<WorkRecovery {...props} />); await preview();
    post.mockRejectedValueOnce(new ApiRequestError('network'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByText(/Result not confirmed/);
    const command = post.mock.calls.find(([path]) => path.endsWith('/operations'))![1] as { request_id: string };
    get.mockImplementation(async path => path.includes('/operations?') ? { items: [{ ...operation, request_id: command.request_id }] } : original(path));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh records' }));
    await screen.findByText('Restore succeeded');
    expect(screen.queryByRole('button', { name: 'Retry same submission' })).toBeNull();
    expect(post.mock.calls.filter(([path]) => path.endsWith('/operations'))).toHaveLength(1);
  });
  it('stops polling in a background tab and checks the result again on return', async () => {
    const { props, get, post } = fixture(); render(<WorkRecovery {...props} />); await preview();
    const original = get.getMockImplementation()!;
    get.mockImplementation(async path => path.endsWith('/op-1') ? { ...operation, state: 'RUNNING' } : original(path));
    post.mockResolvedValueOnce({ ...operation, state: 'QUEUED' });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByTestId('recovery-result');
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    fireEvent(document, new Event('visibilitychange'));
    const before = get.mock.calls.filter(([path]) => path.endsWith('/op-1')).length;
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
    expect(get.mock.calls.filter(([path]) => path.endsWith('/op-1'))).toHaveLength(before);
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    fireEvent(document, new Event('visibilitychange'));
    await waitFor(() => expect(get.mock.calls.filter(([path]) => path.endsWith('/op-1')).length).toBeGreaterThan(before));
  });
  it('blocks another recovery when the result needs attention', async () => {
    const { props, post } = fixture(); render(<WorkRecovery {...props} />); await preview();
    post.mockResolvedValueOnce({ ...operation, state: 'NEEDS_ATTENTION' });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByTestId('recovery-result');
    expect((screen.getByRole('button', { name: 'Preview restore' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('does not present a previous success as the result of a new unconfirmed submission', async () => {
    const { props, post } = fixture(); render(<WorkRecovery {...props} />); await preview();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByText('Restore succeeded');
    fireEvent.click(screen.getByRole('button', { name: 'Preview restore' }));
    await screen.findByRole('button', { name: 'Confirm restore' });
    post.mockRejectedValueOnce(new ApiRequestError('network'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByText(/Result not confirmed/);
    expect(screen.queryByText('Restore succeeded')).toBeNull();
  });

  it('preserves the same ambiguous command across closing and reopening without replaying it', async () => {
    const { props, post } = fixture(); const view = render(<WorkRecovery {...props} />); await preview();
    post.mockRejectedValueOnce(new ApiRequestError('network'));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm restore' }));
    await screen.findByText(/Result not confirmed/);
    view.rerender(<WorkRecovery {...props} visible={false} />);
    view.rerender(<WorkRecovery {...props} />);
    await screen.findByRole('button', { name: 'Retry same submission' });
    expect(post.mock.calls.filter(([path]) => path.endsWith('/operations'))).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Retry same submission' }));
    await screen.findByText('Restore succeeded');
    const attempts = post.mock.calls.filter(([path]) => path.endsWith('/operations'));
    expect(attempts).toHaveLength(2); expect(attempts[0][1]).toEqual(attempts[1][1]);
  });

});
