import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiActionError, type ApiClient } from '../api/client';
import WorkPendingInstructionPanel, { type PendingInstruction, type PendingInstructionMemory } from './WorkPendingInstructionPanel';

const draft = { input: 'Exact next instruction\nkeep whitespace', requirement_version: 3,
  context_refs: [{ kind: 'artifact', ref_id: 'artifact-a', version: 2 }] };
const record = (extra: Partial<PendingInstruction> = {}): PendingInstruction => ({
  id: 'pending-a', work_id: 'work-a', session_id: 'session-a', request_id: 'enqueue-a',
  predecessor_run_id: 'run-a', state: 'PENDING', input: draft.input, requirement_version: 3,
  context_refs: draft.context_refs, reason_code: null, revision: 1, created_at: '', updated_at: '',
  promoted_run_id: null, ...extra,
});
function fixture(initial: PendingInstruction | null = null) {
  let item = initial;
  const memory: PendingInstructionMemory = new Map();
  const get = vi.fn(async (_path: string): Promise<unknown> => ({ item }));
  const post = vi.fn(async (path: string, body: unknown): Promise<unknown> => {
    const command = body as { request_id: string; expected_revision: number };
    if (path.endsWith('/cancel')) {
      item = { ...item!, state: 'CANCELLED', revision: command.expected_revision + 1 };
      return { pending_id: item.id, session_id: 'session-a', request_id: command.request_id,
        state: 'CANCELLED', revision: item.revision, predecessor_run_id: 'run-a' };
    }
    item = record({ request_id: command.request_id });
    return { pending_id: item.id, session_id: 'session-a', state: 'PENDING', revision: 1,
      predecessor_run_id: 'run-a', request_id: command.request_id };
  });
  const props = { client: { get, post } as unknown as ApiClient, workId: 'work-a', sessionId: 'session-a',
    sessionRevision: 8, predecessorRunId: 'run-a', canEnqueue: true, draft, visible: true, memory,
    onBlockChange: vi.fn(), onEnqueued: vi.fn(), onPromoted: vi.fn() };
  return { props, get, post, memory, setItem: (next: PendingInstruction | null) => { item = next; } };
}
async function ready() {
  await waitFor(() => expect((screen.getByRole('button', { name: 'Queue next instruction' }) as HTMLButtonElement).disabled).toBe(false));
}
afterEach(() => cleanup());
describe('one pending instruction per session', () => {
  it('reads only on open and explicitly snapshots the existing input', async () => {
    const f = fixture(); render(<WorkPendingInstructionPanel {...f.props} />); await ready();
    expect(f.post).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Queue next instruction' })); });
    expect(f.post).toHaveBeenCalledTimes(1);
    expect(f.post.mock.calls[0]).toEqual(['/api/v1/works/work-a/sessions/session-a/pending-instruction', {
      ...draft, request_id: expect.any(String), expected_revision: 8, predecessor_run_id: 'run-a',
    }]);
    expect(f.props.onEnqueued).toHaveBeenCalledWith(expect.objectContaining(draft));
    expect(f.memory.size).toBe(0);
    expect(screen.getByText((_content, node) => node?.tagName === 'P' && node.textContent === draft.input)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Queue next instruction' }) as HTMLButtonElement).disabled).toBe(true);
  });
  it('retains unknown body and UUID across unmount; retry sends exactly the original command', async () => {
    const f = fixture(); f.post.mockRejectedValueOnce(new Error());
    const view = render(<WorkPendingInstructionPanel {...f.props} />); await ready();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Queue next instruction' })); });
    const first = f.post.mock.calls[0][1];
    expect(f.memory.size).toBe(1); view.unmount();
    render(<WorkPendingInstructionPanel {...f.props} draft={{ ...draft, input: 'different later draft' }} />);
    await screen.findByRole('button', { name: 'Retry same submission' });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry same submission' })); });
    expect(f.post.mock.calls[1][1]).toEqual(first);
    expect(f.memory.size).toBe(0);
  });
  it('reconciles unknown enqueue by exact request ID without automatically posting', async () => {
    const f = fixture(); f.post.mockRejectedValueOnce(new Error());
    render(<WorkPendingInstructionPanel {...f.props} />); await ready();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Queue next instruction' })); });
    const sent = f.post.mock.calls[0][1] as { request_id: string };
    f.setItem(record({ request_id: sent.request_id, state: 'PROMOTED', promoted_run_id: 'run-next' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh next instruction' })); });
    expect(f.get).toHaveBeenLastCalledWith(expect.stringContaining('?request_id=' + sent.request_id));
    expect(f.post).toHaveBeenCalledTimes(1);
    expect(f.props.onPromoted).toHaveBeenCalledWith('run-next');
    expect(f.props.onEnqueued).toHaveBeenCalledTimes(1);
    expect(f.memory.size).toBe(0);
  });
  it('does not treat another record as an unknown enqueue receipt', async () => {
    const f = fixture(); f.post.mockRejectedValueOnce(new Error());
    render(<WorkPendingInstructionPanel {...f.props} />); await ready();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Queue next instruction' })); });
    f.setItem(record({ request_id: 'another-request' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh next instruction' })); });
    expect(f.memory.size).toBe(1);
    expect(f.props.onEnqueued).not.toHaveBeenCalled();
  });
  it('after compact receipt only refreshes authoritative state when GET failed', async () => {
    const f = fixture(); const original = f.get.getMockImplementation()!;
    render(<WorkPendingInstructionPanel {...f.props} />); await ready();
    f.get.mockRejectedValueOnce(new Error());
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Queue next instruction' })); });
    expect(screen.getByRole('button', { name: 'Refresh confirmed submission' })).toBeTruthy();
    f.get.mockImplementation(original);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh confirmed submission' })); });
    expect(f.post).toHaveBeenCalledTimes(1);
    expect(f.memory.size).toBe(0);
  });
  it('shows held instructions and requires cancellation rather than resuming', async () => {
    const f = fixture(record({ state: 'HELD', reason_code: 'EXECUTION_PENDING_CONTEXT_CHANGED' }));
    render(<WorkPendingInstructionPanel {...f.props} />);
    await screen.findByText('Held for manual review');
    expect(screen.getByText(/will not start automatically/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /resume/i })).toBeNull();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Cancel next instruction' })); });
    expect(f.post.mock.calls[0][1]).toEqual({ request_id: expect.any(String), expected_revision: 1 });
    expect(f.post.mock.calls[0][0]).toMatch(/pending-a\/cancel$/);
    expect(f.memory.size).toBe(0);
  });
  it('unknown cancellation retries the same CAS and UUID', async () => {
    const f = fixture(record()); f.post.mockRejectedValueOnce(new Error());
    render(<WorkPendingInstructionPanel {...f.props} />);
    await screen.findByRole('button', { name: 'Cancel next instruction' });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Cancel next instruction' })); });
    const first = f.post.mock.calls[0][1];
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry same submission' })); });
    expect(f.post.mock.calls[1][1]).toEqual(first);
  });
  it('definitive revision conflict refreshes before allowing a new submission', async () => {
    const f = fixture(); f.post.mockRejectedValueOnce(new ApiActionError(409, 'EXECUTION_REVISION_CONFLICT'));
    render(<WorkPendingInstructionPanel {...f.props} />); await ready();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Queue next instruction' })); });
    expect(f.memory.size).toBe(0);
    expect((screen.getByRole('button', { name: 'Queue next instruction' }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh next instruction' })); });
    await ready(); expect(f.post).toHaveBeenCalledTimes(1);
  });
  it('rejects malformed receipt and cross-session GET without claiming success', async () => {
    const f = fixture(); f.post.mockResolvedValueOnce({ pending_id: 'pending-a' });
    render(<WorkPendingInstructionPanel {...f.props} />); await ready();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Queue next instruction' })); });
    expect(f.props.onEnqueued).not.toHaveBeenCalled(); expect(f.memory.size).toBe(1);
    f.setItem(record({ session_id: 'another-session' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh next instruction' })); });
    expect(f.props.onEnqueued).not.toHaveBeenCalled(); expect(f.memory.size).toBe(1);
  });
  it('suppresses duplicate click and does no read or automatic submit when hidden', async () => {
    const f = fixture();
    const view = render(<WorkPendingInstructionPanel {...f.props} visible={false} />);
    expect(f.get).not.toHaveBeenCalled(); expect(f.post).not.toHaveBeenCalled();
    view.rerender(<WorkPendingInstructionPanel {...f.props} />); await ready();
    let done!: (value: unknown) => void;
    f.post.mockImplementationOnce(() => new Promise(resolve => { done = resolve; }));
    const button = screen.getByRole('button', { name: 'Queue next instruction' });
    fireEvent.click(button); fireEvent.click(button);
    expect(f.post).toHaveBeenCalledTimes(1);
    await act(async () => { done({}); });
  });
  it('accepts known optional context fields omitted or null without losing exact version identity', async () => {
    const f = fixture(); const originalPost = f.post.getMockImplementation()!;
    f.post.mockImplementation(async (path, body) => {
      const receipt = await originalPost(path, body);
      const command = body as { request_id: string };
      f.setItem(record({ request_id: command.request_id,
        context_refs: [{ kind: 'artifact', ref_id: 'artifact-a', version: 2, version_id: null, file_id: null }] }));
      return receipt;
    });
    render(<WorkPendingInstructionPanel {...f.props} />); await ready();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Queue next instruction' })); });
    expect(f.memory.size).toBe(0);
    expect(f.props.onEnqueued).toHaveBeenCalledTimes(1);
  });
  it('does not reconcile a different non-null context version', async () => {
    const f = fixture(); const originalPost = f.post.getMockImplementation()!;
    f.post.mockImplementation(async (path, body) => {
      const receipt = await originalPost(path, body);
      const command = body as { request_id: string };
      f.setItem(record({ request_id: command.request_id,
        context_refs: [{ kind: 'artifact', ref_id: 'artifact-a', version: 3 }] }));
      return receipt;
    });
    render(<WorkPendingInstructionPanel {...f.props} />); await ready();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Queue next instruction' })); });
    expect(f.memory.size).toBe(1);
    expect(f.props.onEnqueued).not.toHaveBeenCalled();
  });

});
