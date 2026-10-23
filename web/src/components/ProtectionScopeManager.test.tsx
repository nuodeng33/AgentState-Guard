import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiActionError, type ApiClient } from '../api/client';
import type { ProtectionScope } from '../api/execution';
import type { ProtectionScopeCommand } from '../api/protection';
import ProtectionScopeManager from './ProtectionScopeManager';

const protectedScope: ProtectionScope = { id: 'scope-a', source_id: 'local', root: '/projects/one',
  status: 'PROTECTED', enabled: true, revision: 3, retention_days: 30, quota_bytes: 2147483649 };
const source = (source_id = 'local', running = true) => ({ source_id, label: source_id, kind: 'NATIVE',
  running, connected: running, capabilities: { scan: running, apply: running } });
function fixture(options: { scopes?: ProtectionScope[]; sourceId?: string; selected?: string;
  initialPending?: ProtectionScopeCommand; sources?: ReturnType<typeof source>[] } = {}) {
  let records = options.scopes ?? [];
  const get = vi.fn(async (url: string): Promise<unknown> => {
    if (url.includes('/candidates?')) return { sources: options.sources ?? [source(), source('wsl-a', false)], items: [
      { source_id: 'local', path: '/candidate/local', session_id: 'native', current: true },
      { source_id: 'wsl-a', path: '/candidate/remote', session_id: 'remote', current: false },
    ], manual_registration: true, limit: 50, offset: 0, has_more: false };
    if (url === '/api/v1/protection/scopes') return { items: records };
    throw new Error('Unexpected GET');
  });
  const post = vi.fn(async (url: string, body: unknown): Promise<unknown> => {
    if (url.endsWith('/configure')) {
      const command = body as { enabled: boolean; quota_bytes: number; retention_days: number };
      records = [{ ...protectedScope, ...command, revision: 4 }];
      return { scope_id: 'scope-a', revision: 4 };
    }
    records = [{ ...protectedScope, id: 'new-scope', root: '/candidate/local', revision: 1 }];
    return { scope_id: 'new-scope', revision: 1, reused: false };
  });
  const client = { get, post, bootstrap: vi.fn() } as unknown as ApiClient;
  const changed = vi.fn(), pendingChanged = vi.fn();
  function Harness({ shown = true, fixed = options.sourceId }: { shown?: boolean; fixed?: string }) {
    const [scopes, setScopes] = useState(options.scopes ?? []);
    const [selected, setSelected] = useState(options.selected);
    const [pending, setPending] = useState<ProtectionScopeCommand | null>(options.initialPending ?? null);
    return shown ? <ProtectionScopeManager client={client} sourceId={fixed} scopes={scopes}
      selectedScopeId={selected} pending={pending}
      onPendingChange={command => { pendingChanged(command); setPending(command); }}
      onScopesChanged={(items, id) => { changed(items, id); setScopes(items); if (id) setSelected(id); }} /> : null;
  }
  return { client, get, post, changed, pendingChanged, Harness };
}
async function open() {
  const summary = screen.getByText(/Manage protected directories/);
  await act(async () => {
    const details = summary.closest('details')!; details.open = true;
    fireEvent(details, new Event('toggle'));
  });
  await screen.findByRole('button', { name: 'Refresh directory candidates' });
  await waitFor(() => expect(screen.queryByText('Loading directory candidates')).toBeNull());
}
async function manualPath(path = '/candidate/local') {
  fireEvent.change(screen.getByLabelText('Project directory path'), { target: { value: path } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Register and protect directory' })); });
}
afterEach(cleanup);
describe('protected directory onboarding', () => {
  it('opens read-only and fills an exact-source candidate without registering', async () => {
    const f = fixture({ sourceId: 'local' }); render(<f.Harness />);
    expect(f.get).not.toHaveBeenCalled(); expect(f.post).not.toHaveBeenCalled();
    await open();
    expect(screen.queryByRole('option', { name: /candidate\/remote/ })).toBeNull();
    fireEvent.change(screen.getByLabelText('Observed directory candidate'), { target: { value: '0' } });
    expect((screen.getByLabelText('Project directory path') as HTMLInputElement).value).toBe('/candidate/local');
    expect(f.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Register and protect directory' }));
    await waitFor(() => expect(f.changed).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ id: 'new-scope' }),
    ]), 'new-scope'));
    expect(f.post.mock.calls[0]).toEqual(['/api/v1/protection/scopes', {
      request_id: expect.any(String), source_id: 'local', path: '/candidate/local',
    }]);
    expect(f.pendingChanged).toHaveBeenLastCalledWith(null);
    expect(screen.getByText(/Directory registered/)).toBeTruthy();
  });
  it('does not wake a stopped source and allows manual source selection', async () => {
    const f = fixture(); render(<f.Harness />); await open();
    fireEvent.change(screen.getByLabelText('Directory source'), { target: { value: 'wsl-a' } });
    expect((screen.getByLabelText('Project directory path') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Register and protect directory' }) as HTMLButtonElement).disabled).toBe(true);
    expect(f.post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Directory source'), { target: { value: 'local' } });
    await manualPath('/manual/project');
    await waitFor(() => expect(f.post).toHaveBeenCalledTimes(1));
    expect(f.post.mock.calls[0][1]).toMatchObject({ path: '/manual/project', source_id: 'local' });
  });
  it('keeps an unknown immutable request through close/remount and source changes', async () => {
    const f = fixture({ sourceId: 'local' }); f.post.mockRejectedValueOnce(new Error('lost receipt'));
    const view = render(<f.Harness />); await open(); await manualPath();
    await screen.findByRole('button', { name: 'Retry same submission' });
    expect((screen.getByLabelText('Project directory path') as HTMLInputElement).disabled).toBe(true);
    const firstBody = f.post.mock.calls[0][1];
    view.rerender(<f.Harness shown={false} />);
    view.rerender(<f.Harness fixed="wsl-a" />);
    await open();
    expect(screen.getByText(/Pending directory: local/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry same submission' })); });
    await waitFor(() => expect(f.post).toHaveBeenCalledTimes(2));
    expect(f.post.mock.calls[1][1]).toEqual(firstBody);
    await waitFor(() => expect(f.pendingChanged).toHaveBeenLastCalledWith(null));
  });
  it('only refreshes after a confirmed receipt whose scope GET failed', async () => {
    const f = fixture({ sourceId: 'local' });
    const normalGet = f.get.getMockImplementation()!;
    let failed = false;
    f.get.mockImplementation(async url => {
      if (url === '/api/v1/protection/scopes' && !failed) { failed = true; throw new Error(); }
      return normalGet(url);
    });
    render(<f.Harness />); await open(); await manualPath();
    await screen.findByRole('button', { name: 'Refresh confirmed submission' });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh confirmed submission' })); });
    await waitFor(() => expect(f.changed).toHaveBeenCalled());
    expect(f.post).toHaveBeenCalledTimes(1);
  });
  it('retains malformed success as unconfirmed', async () => {
    const f = fixture({ sourceId: 'local' }); f.post.mockResolvedValueOnce({ revision: 1 });
    render(<f.Harness />); await open(); await manualPath();
    await screen.findByRole('button', { name: 'Retry same submission' });
    expect(f.changed).not.toHaveBeenCalled();
    expect(f.pendingChanged).not.toHaveBeenLastCalledWith(null);
  });
  it('keeps definitive rejection editable and shows its safe reason code', async () => {
    const f = fixture({ sourceId: 'local' });
    f.post.mockRejectedValueOnce(new ApiActionError(409, 'PROTECTION_SCOPE_OVERLAP'));
    render(<f.Harness />); await open(); await manualPath();
    await screen.findByText(/PROTECTION_SCOPE_OVERLAP/);
    expect(f.pendingChanged).toHaveBeenLastCalledWith(null);
    expect((screen.getByLabelText('Project directory path') as HTMLInputElement).disabled).toBe(false);
  });
  it('configures the authoritative revision and preserves an exact existing byte quota', async () => {
    const f = fixture({ sourceId: 'local', scopes: [protectedScope], selected: 'scope-a' });
    render(<f.Harness />); await open();
    fireEvent.change(screen.getByLabelText('Retention days'), { target: { value: '45' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save protection settings' }));
    await waitFor(() => expect(f.post).toHaveBeenCalledTimes(1));
    expect(f.post.mock.calls[0]).toEqual(['/api/v1/protection/scopes/scope-a/configure', {
      request_id: expect.any(String), expected_revision: 3, enabled: true, retention_days: 45, quota_bytes: 2147483649,
    }]);
    await waitFor(() => expect(f.pendingChanged).toHaveBeenLastCalledWith(null));
  });
  it('validates integer days and byte quota before configuration', async () => {
    const f = fixture({ sourceId: 'local', scopes: [protectedScope], selected: 'scope-a' });
    render(<f.Harness />); await open();
    fireEvent.change(screen.getByLabelText('Retention days'), { target: { value: '1.5' } });
    expect((screen.getByRole('button', { name: 'Save protection settings' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Retention days'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('Storage quota (MiB)'), { target: { value: '0' } });
    expect((screen.getByRole('button', { name: 'Save protection settings' }) as HTMLButtonElement).disabled).toBe(true);
    expect(f.post).not.toHaveBeenCalled();
  });
  it('refreshes conflict settings and requires another explicit save', async () => {
    const f = fixture({ sourceId: 'local', scopes: [protectedScope], selected: 'scope-a' });
    f.post.mockRejectedValueOnce(new ApiActionError(409, 'PROTECTION_REVISION_CONFLICT'));
    const normalGet = f.get.getMockImplementation()!;
    f.get.mockImplementation(async url => url === '/api/v1/protection/scopes'
      ? { items: [{ ...protectedScope, revision: 9, retention_days: 7 }] } : normalGet(url));
    render(<f.Harness />); await open();
    fireEvent.change(screen.getByLabelText('Retention days'), { target: { value: '45' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save protection settings' }));
    await screen.findByText(/Protection settings changed/);
    expect((screen.getByLabelText('Retention days') as HTMLInputElement).value).toBe('7');
    expect(f.post).toHaveBeenCalledTimes(1);
    expect(f.pendingChanged).toHaveBeenLastCalledWith(null);
  });
  it('suppresses duplicate clicks and exposes no nested form', async () => {
    const f = fixture({ sourceId: 'local' });
    let resolve!: (value: unknown) => void;
    f.post.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    render(<form><f.Harness /></form>); await open();
    fireEvent.change(screen.getByLabelText('Project directory path'), { target: { value: '/candidate/local' } });
    const register = screen.getByRole('button', { name: 'Register and protect directory' });
    fireEvent.click(register); fireEvent.click(register);
    expect(f.post).toHaveBeenCalledTimes(1);
    expect(document.querySelectorAll('form')).toHaveLength(1);
    await act(async () => resolve({ scope_id: 'new-scope', revision: 1 }));
  });
  it('keeps a 5xx receipt unknown and ignores a mismatched refreshed source', async () => {
    const f = fixture({ sourceId: 'local' }); f.post.mockRejectedValueOnce(new ApiActionError(503, 'PROTECTION_UNAVAILABLE'));
    render(<f.Harness />); await open(); await manualPath();
    expect(screen.getByRole('button', { name: 'Retry same submission' })).toBeTruthy();
    const originalGet = f.get.getMockImplementation()!;
    f.get.mockImplementation(async url => url === '/api/v1/protection/scopes'
      ? { items: [{ ...protectedScope, id: 'new-scope', source_id: 'wsl-a', revision: 1 }] } : originalGet(url));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry same submission' })); });
    expect(screen.getByRole('button', { name: 'Refresh confirmed submission' })).toBeTruthy();
    expect(f.changed).not.toHaveBeenCalled();
    expect(f.pendingChanged).not.toHaveBeenLastCalledWith(null);
    expect(f.post.mock.calls[1][1]).toEqual(f.post.mock.calls[0][1]);
  });
  it('pages candidate metadata and still requires an explicit registration', async () => {
    const f = fixture({ sourceId: 'local' });
    const originalGet = f.get.getMockImplementation()!;
    f.get.mockImplementation(async url => url.includes('/candidates?')
      ? { sources: [source()], items: [{ source_id: 'local', path: url.endsWith('offset=0') ? '/first' : '/second',
        session_id: url, current: true }], limit: 50, offset: url.endsWith('offset=0') ? 0 : 50,
        has_more: url.endsWith('offset=0'), manual_registration: true } : originalGet(url));
    render(<f.Harness />); await open();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Load more directory candidates' })); });
    expect(f.get).toHaveBeenCalledWith('/api/v1/protection/scopes/candidates?limit=50&offset=50');
    expect(screen.getByRole('option', { name: /first/ })).toBeTruthy();
    expect(screen.getByRole('option', { name: /second/ })).toBeTruthy();
    expect(f.post).not.toHaveBeenCalled();
  });
  it('does not configure a retired scope or one from a different source', async () => {
    const f = fixture({ sourceId: 'local', scopes: [{ ...protectedScope, status: 'RETIRED' }], selected: 'scope-a' });
    const view = render(<f.Harness />); await open();
    fireEvent.click(screen.getByRole('button', { name: 'Save protection settings' }));
    expect(f.post).not.toHaveBeenCalled();
    view.rerender(<f.Harness fixed="wsl-a" />);
    expect(screen.queryByLabelText('Directory protection settings')).toBeNull();
    expect(f.post).not.toHaveBeenCalled();
  });

  it('opens selected scope policy in global mode and preserves manual source changes', async () => {
    const f = fixture({ scopes: [protectedScope], selected: 'scope-a' });
    const view = render(<f.Harness />); await open();
    expect((screen.getByLabelText('Directory source') as HTMLSelectElement).value).toBe('local');
    expect(screen.getByLabelText('Directory protection settings')).toBeTruthy();
    expect((screen.getByLabelText('Retention days') as HTMLInputElement).value).toBe('30');
    fireEvent.change(screen.getByLabelText('Directory source'), { target: { value: 'wsl-a' } });
    view.rerender(<f.Harness />);
    expect((screen.getByLabelText('Directory source') as HTMLSelectElement).value).toBe('wsl-a');
    expect(screen.queryByLabelText('Directory protection settings')).toBeNull();
    expect(f.post).not.toHaveBeenCalled();
  });
  it('blocks registration during candidate refresh and after a failed source refresh', async () => {
    const f = fixture({ sourceId: 'local' }); render(<f.Harness />); await open();
    fireEvent.change(screen.getByLabelText('Project directory path'), { target: { value: '/candidate/local' } });
    const register = screen.getByRole('button', { name: 'Register and protect directory' }) as HTMLButtonElement;
    expect(register.disabled).toBe(false);
    let reject!: (error: Error) => void;
    f.get.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh directory candidates' }));
    expect(register.disabled).toBe(true);
    await act(async () => { reject(new Error('source observation unavailable')); });
    expect(register.disabled).toBe(true);
    expect((screen.getByLabelText('Project directory path') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText('Directory records could not be refreshed.')).toBeTruthy();
    expect(f.post).not.toHaveBeenCalled();
  });

});
