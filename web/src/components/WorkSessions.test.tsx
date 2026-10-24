import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import type { ExecutionRun, WorkSession } from '../api/execution';
import WorkSessions, { createWorkSessionMemory } from './WorkSessions';
import WorkPage from '../pages/WorkPage';

const session = (id: string): WorkSession => ({ id, work_id: 'work-a', provider: 'CODEX', source_id: 'local',
  application_id: 'app', store_id: 'store', scope_id: 'scope', task_id: null,
  status: 'READY', connection_state: 'CONNECTED', native_session_id: `native-${id}`, read_only: true, revision: 2,
  capabilities: { create: true, continue: true, send: true, stream: true, cancel: true, fork: false, write: false } });
const execution = (status: ExecutionRun['status'] = 'COMPLETED'): ExecutionRun => ({ id: 'run-a', session_id: 'a',
  requirement_version: 3, status, input_summary: 'Fix the issue', last_seq: 0, coverage: 'PARTIAL', change_refs: [],
  pending_responses: [], created_at: '2026-09-30T00:00:00Z' });
function eventResponse(text = '') {
  return new Response(text, { headers: { 'Content-Type': 'text/event-stream' } });
}
function fixture(initialRun?: ExecutionRun) {
  let executions = initialRun ? [initialRun] : [];
  const post = vi.fn(async (path: string, _body: unknown): Promise<Record<string, unknown>> => {
    if (path.endsWith('/sessions')) return { session_id: 'a', revision: 8 };
    if (path.endsWith('/runs')) { executions = [execution()]; return { run_id: 'run-a', session_id: 'a', revision: 3 }; }
    return { revision: 3 };
  });
  const get = vi.fn(async (path: string): Promise<unknown> => {
    if (path.endsWith('/execution-summary')) return { work_id: 'work-a', requirement_version: 3, work_revision: 5, counts: {}, items: [], has_more: false };
    if (path.startsWith('/api/v1/discovery/applications')) return { items: [{ id: 'app', provider: 'CODEX', source_id: 'local',
      path: '/tools/codex', status: 'AVAILABLE', control_capabilities: { create: false, continue: false }, reason_code: 'APPLICATION_OBSERVED' }], has_more: false };
    if (path === '/api/v1/protection/scopes') return { items: [{ id: 'scope', source_id: 'local', root: '/work/repo', status: 'PROTECTED', enabled: true }] };
    if (path.includes('/session-stores')) return { items: [{ id: 'store', source_id: 'local', provider: 'CODEX', path: '/stores/one', status: 'AVAILABLE' }], has_more: false };
    if (path.startsWith('/api/v1/discovery/sources/local/sessions?')) return { sessions: [{ session_id: 'external-native', binding_id: 'exact-binding', store_id: 'store', provider: 'CODEX', native_session_id: 'native-external', title: 'Existing', cwd: '/work/repo' }], has_more: false };
    if (/\/sessions\?/.test(path)) return { items: [session('a'), session('b')], has_more: false };
    if (/\/sessions\/[ab]\/runs/.test(path)) return { items: path.includes('/a/') ? executions : [], has_more: false };
    if (path.includes('/pending-instruction')) return { item: null };
    if (path.endsWith('/sessions/a')) return session('a');
    if (path.endsWith('/sessions/b')) return session('b');
    if (path.endsWith('/runs/run-a')) return executions[0];
    throw new Error(`Unexpected path ${path}`);
  });
  const stream = vi.fn(async (_path: string, _signal?: AbortSignal) => eventResponse());
  const client = { bootstrap: vi.fn(), get, post, stream } as unknown as ApiClient;
  const props = { client, workId: 'work-a', revision: 5, requirementVersion: 3, tasks: [{ id: 'task', title: 'Implementation' }],
    memory: createWorkSessionMemory(), onClose: vi.fn(), onRevision: vi.fn(), onOpenRequirement: vi.fn() };
  return { props, get, post, stream, setRun: (run: ExecutionRun) => { executions = [run]; } };
}
function promotionFixture() {
  const f = fixture(execution('RUNNING'));
  const original = f.get.getMockImplementation()!;
  let promotedRunId: string | null = null, reads = 0, allowedReads = 2;
  const promotedRuns = new Map<string, ExecutionRun>();
  f.get.mockImplementation(async path => {
    if (path.endsWith('/sessions/a')) {
      reads++;
      // Bound an erroneous refresh loop so the causal regression fails rather
      // than hanging the test process in an unbounded microtask chain.
      if (reads > allowedReads) return new Promise(() => {});
    }
    if (path.includes('/pending-instruction')) return { item: promotedRunId ? {
      id: `pending-${promotedRunId}`, work_id: 'work-a', session_id: 'a', request_id: `enqueue-${promotedRunId}`,
      predecessor_run_id: 'run-a', state: 'PROMOTED', input: `Queued ${promotedRunId}`,
      requirement_version: 3, context_refs: [], reason_code: null, revision: 2,
      promoted_run_id: promotedRunId, created_at: '', updated_at: '',
    } : null };
    if (promotedRunId && /\/sessions\/a\/runs\?/.test(path)) return {
      items: [...promotedRuns.values()].reverse().concat(execution('COMPLETED')), has_more: false,
    };
    if (promotedRunId && path.endsWith('/runs/run-a')) return execution('COMPLETED');
    for (const [id, run] of promotedRuns) if (path.endsWith('/runs/' + id)) return run;
    return original(path);
  });
  return { ...f, sessionReads: () => reads, allowReads: (count: number) => { allowedReads = count; },
    promote: (id: string) => {
      promotedRunId = id;
      promotedRuns.set(id, { ...execution('COMPLETED'), id, input_summary: `Queued ${id}` });
    } };
}

async function openSession(id = 'a') { fireEvent.click(await screen.findByRole('button', { name: new RegExp(`CODEX.*native-${id}`) })); await screen.findByLabelText('Execution input'); }
afterEach(() => { cleanup(); history.replaceState(null, '', '/'); });

describe('work session management', () => {
  it.each(['scope-source', 'store-source', 'disabled', 'retired'])('blocks creating a session with an invalid directory/store association: %s', async kind => {
    const { props, get, post } = fixture(); const original = get.getMockImplementation()!;
    get.mockImplementation(async path => {
      const result = await original(path) as { items?: Record<string, unknown>[] };
      if (path === '/api/v1/protection/scopes') return { items: [{ id: 'scope', source_id: kind === 'scope-source' ? 'other' : 'local',
        root: '/work/repo', status: kind === 'retired' ? 'RETIRED' : 'PROTECTED', enabled: kind !== 'disabled' }] };
      if (kind === 'store-source' && path.includes('/session-stores')) return { ...result, items: result.items?.map(item => ({ ...item, source_id: 'other' })) };
      return result;
    });
    render(<WorkSessions {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Create session' }));
    await screen.findByRole('option', { name: /tools\/codex/ });
    fireEvent.change(screen.getByLabelText('Application'), { target: { value: 'app' } });
    await waitFor(() => expect(get.mock.calls.some(([path]) => path.includes('/session-stores'))).toBe(true));
    fireEvent.change(screen.getByLabelText('Session store'), { target: { value: 'store' } });
    fireEvent.change(screen.getByLabelText('Protected directory'), { target: { value: 'scope' } });
    fireEvent.click(screen.getByLabelText('Allow ASG to manage this session'));
    expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(screen.getByRole('button', { name: 'Create' }).closest('form')!);
    expect(post).not.toHaveBeenCalled();
  });

  it.each(['AVAILABLE', 'EMPTY', 'DEGRADED'])('allows an explicit NEW session using a backend-supported %s store', async status => {
    const { props, get, post } = fixture(); const original = get.getMockImplementation()!;
    get.mockImplementation(async path => {
      const result = await original(path) as { items?: Record<string, unknown>[] };
      return path.includes('/session-stores') ? { ...result, items: result.items?.map(item => ({ ...item, status })) } : result;
    });
    render(<WorkSessions {...props} />); fireEvent.click(screen.getByRole('button', { name: 'Create session' }));
    await screen.findByRole('option', { name: /tools\/codex/ });
    fireEvent.change(screen.getByLabelText('Application'), { target: { value: 'app' } });
    await screen.findByRole('option', { name: new RegExp('/stores/one.*' + status) });
    fireEvent.change(screen.getByLabelText('Session store'), { target: { value: 'store' } });
    fireEvent.change(screen.getByLabelText('Protected directory'), { target: { value: 'scope' } });
    fireEvent.click(screen.getByLabelText('Allow ASG to manage this session'));
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(post.mock.calls.some(([path]) => path.endsWith('/sessions'))).toBe(true));
  });

  it('preserves a shared uncertain directory command when the session surface closes', async () => {
    const { props, post } = fixture(); const onChange = vi.fn();
    const command = { kind: 'register' as const, sourceId: 'local', path: '/new-project',
      payload: { source_id: 'local', path: '/new-project', request_id: 'same-request' } };
    const view = render(<WorkSessions {...props} protectionCommand={command} onProtectionCommandChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Create session' }));
    await screen.findByRole('option', { name: /tools\/codex/ });
    expect((screen.getByLabelText('Application') as HTMLSelectElement).disabled).toBe(true);
    view.rerender(<WorkSessions {...props} visible={false} protectionCommand={command} onProtectionCommandChange={onChange} />);
    view.rerender(<WorkSessions {...props} protectionCommand={command} onProtectionCommandChange={onChange} />);
    await screen.findByRole('option', { name: /tools\/codex/ });
    expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(true);
    expect(post).not.toHaveBeenCalled(); expect(onChange).not.toHaveBeenCalled();
  });

  it('sends selected versioned context and requires confirmation after requirements change', async () => {
    const { props, post } = fixture();
    const artifacts = [{ id: 'artifact-a', version: 2, title: 'Reviewed result' }];
    const view = render(<WorkSessions {...props} artifacts={artifacts} />); await openSession();
    fireEvent.click(screen.getByText('Context to send', { exact: false }));
    fireEvent.click(screen.getByLabelText('Reviewed result · v2'));
    fireEvent.change(screen.getByLabelText('Execution input'), { target: { value: 'Apply this feedback' } });
    view.rerender(<WorkSessions {...props} artifacts={artifacts} requirementVersion={4} />);
    expect(screen.getByText(/Requirement changed: this draft uses v3/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Start execution' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText('Execution input') as HTMLTextAreaElement).value).toBe('Apply this feedback');
    fireEvent.click(screen.getByRole('button', { name: 'Use current requirement for next execution' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start execution' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toMatchObject({ requirement_version: 4, input: 'Apply this feedback',
      context_refs: [{ kind: 'artifact', ref_id: 'artifact-a', version: 2 }] });
  });

  it('opens the exact running execution from work attention and keeps new instructions unsent', async () => {
    const { props, post } = fixture(execution('WAITING_INPUT'));
    render(<WorkSessions {...props} target={{ sessionId: 'a', runId: 'run-a' }} />);
    await screen.findByLabelText('Execution input');
    await screen.findByText(/This draft has not been sent/);
    fireEvent.change(screen.getByLabelText('Execution input'), { target: { value: 'Changed requirements' } });
    expect((screen.getByRole('button', { name: 'Start execution' }) as HTMLButtonElement).disabled).toBe(true);
    expect(post).not.toHaveBeenCalled();
  });

  it('does not reopen an old attention target after switching sessions and returning from reading', async () => {
    const { props } = fixture(execution()); const target = { sessionId: 'a', runId: 'run-a' };
    const view = render(<WorkSessions {...props} target={target} />);
    await screen.findByLabelText('Execution input');
    fireEvent.click(screen.getByRole('button', { name: 'Session list' }));
    await openSession('b');
    view.rerender(<WorkSessions {...props} target={target} visible={false} />);
    view.rerender(<WorkSessions {...props} target={target} visible />);
    await waitFor(() => expect(screen.getByText('native-b')).toBeTruthy());
  });

  it('waits for file finalization before offering delivery or new input', async () => {
    const { props, setRun } = fixture(execution('FINALIZING')); const onDelivery = vi.fn();
    render(<WorkSessions {...props} onDelivery={onDelivery} />); await openSession();
    await screen.findAllByText(/Collecting file results/);
    fireEvent.change(screen.getByLabelText('Execution input'), { target: { value: 'Next execution' } });
    expect((screen.getByRole('button', { name: 'Start execution' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Prepare delivery' })).toBeNull();
    expect((screen.getByRole('button', { name: 'Stop execution' }) as HTMLButtonElement).disabled).toBe(false);
    setRun(execution('COMPLETED'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Prepare delivery' })).toBeTruthy(), { timeout: 3500 });
  });

  it('connects a disconnected session with explicit management consent and its revision', async () => {
    const { props, get, post } = fixture(); const originalGet = get.getMockImplementation()!;
    get.mockImplementation(async path => path.endsWith('/sessions/a') ? { ...session('a'), state: 'DISCONNECTED', status: 'DISCONNECTED' } : originalGet(path));
    render(<WorkSessions {...props} />); await openSession();
    fireEvent.click(await screen.findByRole('button', { name: 'Connect session' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]).toEqual(['/api/v1/works/work-a/sessions/a/connect',
      { request_id: expect.any(String), expected_revision: 2, control_consent: true }]);
  });

  it('reconciles an uncertain execution without allowing blind input or cancellation', async () => {
    const { props, post } = fixture(execution('UNCERTAIN'));
    render(<WorkSessions {...props} />); await openSession();
    fireEvent.change(screen.getByLabelText('Execution input'), { target: { value: 'Do not resend' } });
    expect((screen.getByRole('button', { name: 'Start execution' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Stop execution' })).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Reconcile execution' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe('/api/v1/works/work-a/runs/run-a/reconcile');
  });

  it('archives using the current session revision', async () => {
    const { props, post } = fixture(); render(<WorkSessions {...props} />); await openSession();
    fireEvent.click(screen.getByRole('button', { name: 'Archive session' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toMatchObject({ expected_revision: 2 });
  });

  it('creates a managed continuation from an exact binding and retries the same UUID', async () => {
    const { props, post } = fixture(); post.mockRejectedValueOnce(new Error('connection lost'));
    render(<WorkSessions {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Create session' }));
    await screen.findByRole('option', { name: /tools\/codex/ });
    fireEvent.change(screen.getByLabelText('Application'), { target: { value: 'app' } });
    await screen.findByRole('option', { name: /stores\/one/ });
    fireEvent.change(screen.getByLabelText('Session store'), { target: { value: 'store' } });
    fireEvent.change(screen.getByLabelText('Protected directory'), { target: { value: 'scope' } });
    fireEvent.click(screen.getByLabelText('Continue existing'));
    await screen.findByRole('option', { name: /native-external/ });
    fireEvent.change(screen.getByLabelText('Existing session'), { target: { value: 'exact-binding' } });
    fireEvent.click(screen.getByLabelText('Allow ASG to manage this session'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[0][0]).toBe('/api/v1/works/work-a/sessions');
    expect(post.mock.calls[0][1]).toMatchObject({ application_id: 'app', store_id: 'store', scope_id: 'scope',
      mode: 'CONTINUE', binding_id: 'exact-binding', read_only: true, control_consent: true, expected_revision: 5 });
    expect(post.mock.calls[1][1]).toEqual(post.mock.calls[0][1]);
  });

  it('keeps drafts separate and preserves an uncertain run command across session switches', async () => {
    const { props, post } = fixture(); post.mockRejectedValueOnce(new Error('lost reply'));
    render(<WorkSessions {...props} />); await openSession();
    fireEvent.change(screen.getByLabelText('Execution input'), { target: { value: 'Draft A' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start execution' }));
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: 'Session list' })); await openSession('b');
    expect((screen.getByLabelText('Execution input') as HTMLTextAreaElement).value).toBe('');
    fireEvent.change(screen.getByLabelText('Execution input'), { target: { value: 'Draft B' } });
    fireEvent.click(screen.getByRole('button', { name: 'Session list' })); await openSession();
    expect((screen.getByLabelText('Execution input') as HTMLTextAreaElement).value).toBe('Draft A');
    fireEvent.click(screen.getByRole('button', { name: 'Start execution' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1][1]).toEqual(post.mock.calls[0][1]);
    expect(post.mock.calls[0][1]).toMatchObject({ requirement_version: 3, input: 'Draft A', context_refs: [] });
    expect(props.memory.drafts.get('work-a:b')).toBe('Draft B');
  });

  it('binds approval to its execution event and leaves the prompt on an uncertain response', async () => {
    const run = { ...execution('WAITING_APPROVAL'), pending_responses: [{ event_seq: 4, action_ref: 'tool-exact', kind: 'APPROVAL', payload: { text: 'Write selected file', allowed_decisions: ['ALLOW_ONCE', 'DENY', 'CANCEL'] } }] };
    const { props, post } = fixture(run); post.mockRejectedValueOnce(new Error('lost reply'));
    render(<WorkSessions {...props} />); await openSession();
    fireEvent.click(await screen.findByRole('button', { name: 'Allow once' }));
    await screen.findByRole('alert');
    expect(screen.getByText('Write selected file')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[0][0]).toBe('/api/v1/works/work-a/runs/run-a/responses');
    expect(post.mock.calls[0][1]).toMatchObject({ event_seq: 4, action_ref: 'tool-exact', response: { decision: 'ALLOW_ONCE' } });
    expect(post.mock.calls[1][1]).toEqual(post.mock.calls[0][1]);
  });

  it('detaches output on closing the panel without stopping execution', async () => {
    const { props, post, stream } = fixture(execution('RUNNING'));
    stream.mockImplementation(async () => new Response(new ReadableStream(), { headers: { 'Content-Type': 'text/event-stream' } }));
    const { rerender } = render(<WorkSessions {...props} />); await openSession();
    await waitFor(() => expect(stream).toHaveBeenCalled());
    const signal = (stream.mock.calls as unknown as [string, AbortSignal][])[0][1];
    rerender(<WorkSessions {...props} visible={false} />);
    await waitFor(() => expect(signal.aborted).toBe(true));
    expect(post).not.toHaveBeenCalled();
    expect(screen.queryByRole('region', { name: 'Session management' })).toBeNull();
  });

  it('shows an approval immediately while the event stream remains open', async () => {
    const { props, stream, setRun } = fixture(execution('RUNNING'));
    let push!: (event: string) => void;
    stream.mockImplementation(async () => new Response(new ReadableStream<Uint8Array>({ start(controller) {
      push = event => controller.enqueue(new TextEncoder().encode(event));
    } }), { headers: { 'Content-Type': 'text/event-stream' } }));
    render(<WorkSessions {...props} />); await openSession();
    await waitFor(() => expect(stream).toHaveBeenCalled());
    setRun({ ...execution('WAITING_APPROVAL'), pending_responses: [{ event_seq: 1, action_ref: 'live-request',
      kind: 'APPROVAL', payload: { title: 'Pending operation', allowed_decisions: ['DENY', 'CANCEL'] } }] });
    await act(async () => push('id: 1\ndata: {"seq":1,"kind":"APPROVAL","payload":{"title":"Pending operation"},"created_at":"now"}\n\n'));
    expect(await screen.findByRole('button', { name: 'Deny' })).toBeTruthy();
  });

  it('honors allowed decisions and submits multiple questions with masked private input', async () => {
    const { props, post } = fixture({ ...execution('WAITING_INPUT'), pending_responses: [
      { event_seq: 3, action_ref: 'limited-operation', kind: 'APPROVAL', payload: { allowed_decisions: ['DENY', 'CANCEL'], title: 'Unavailable write' } },
      { event_seq: 4, action_ref: 'question-set', kind: 'QUESTION', payload: { questions: [
        { id: 'choice', header: 'Target', question: 'Which target?', secret: false, options: [{ label: 'Preview', description: 'Preview environment' }] },
        { id: 'secret', header: 'Credential', question: 'Private value?', secret: true, options: [] },
      ] } },
    ] });
    render(<WorkSessions {...props} />); await openSession();
    expect((await screen.findByRole('button', { name: 'Allow once' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Target'), { target: { value: 'Preview' } });
    const privateInput = screen.getByLabelText('Private value?') as HTMLInputElement;
    expect(privateInput.type).toBe('password');
    fireEvent.change(privateInput, { target: { value: 'private-answer' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit answer' }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).toMatchObject({ event_seq: 4, action_ref: 'question-set', response: {
      answers: { choice: { answers: ['Preview'] }, secret: { answers: ['private-answer'] } },
    } });
    expect(screen.queryByText('private-answer')).toBeNull();
  });

  it('loads a real diff from the exact protected baseline and result versions', async () => {
    const { props, get } = fixture({ ...execution(), file_result: { status: 'AVAILABLE', scope_id: 'scope',
      baseline_id: 'before', version_id: 'after', reason_code: null, attribution: 'UNATTRIBUTED' } });
    const existing = get.getMockImplementation()!;
    get.mockImplementation(async path => {
      if (path.includes('/versions?')) return { items: [
        { id: 'before', details_included: true, files: [{ file_id: 'file', relative_path: 'src/main.ts', content_digest: 'old', category: 'restorable' }] },
        { id: 'after', details_included: true, files: [{ file_id: 'file', relative_path: 'src/main.ts', content_digest: 'new', category: 'restorable' }] },
      ] };
      if (path.includes('/files/file/diff?')) return { text_diff: '- before\n+ after' };
      return existing(path);
    });
    render(<WorkSessions {...props} />); await openSession();
    fireEvent.click(await screen.findByRole('button', { name: 'View recorded changes' }));
    fireEvent.click(await screen.findByRole('button', { name: 'src/main.ts' }));
    expect(await screen.findByText(/before.*after/)).toBeTruthy();
    expect(get).toHaveBeenCalledWith('/api/v1/protection/scopes/scope/files/file/diff?before=before&after=after');
    expect(screen.getByText(/Attribution unconfirmed/)).toBeTruthy();
  });

  it('keeps the work core and old return path when session management opens from more actions', async () => {
    const { props, get } = fixture(); const existing = get.getMockImplementation()!;
    get.mockImplementation(async path => {
      if (path === '/api/v1/works/work-a') return { id: 'work-a', title: 'Real work', owner: 'Owner', revision: 5,
        requirement_version: 3, requirement: 'Real requirement', tasks: [], artifacts: [] };
      if (path.endsWith('/view')) return { revision: 1, state: { schema_version: 1, surface: 'space', cards: [],
        viewport: { x: 0, y: 0, zoom: 1 }, space: { viewport: { x: 10, y: 15, zoom: 1 }, nodes: [{ kind: 'requirement', ref_id: 'work-a', version: 3, x: 0, y: 0 }] } } };
      if (path.endsWith('/agents')) return { items: [] };
      return existing(path);
    });
    history.replaceState(null, '', '/?work=work-a');
    render(<WorkPage client={props.client} />);
    await screen.findByRole('button', { name: 'Open Real work' });
    expect(screen.queryByRole('region', { name: 'Session management' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(screen.getByRole('button', { name: 'Session management' }));
    await screen.findByRole('region', { name: 'Session management' });
    expect(screen.getByRole('button', { name: 'Open Real work' })).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('region', { name: 'Session management' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Open Real work' })).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'More actions' })));
  });

  it('shows safe summaries, keeps execution completion separate, and opens the bound requirement', async () => {
    const { props, stream } = fixture({ ...execution(), last_seq: 2 });
    stream.mockResolvedValueOnce(eventResponse('id: 1\ndata: {"seq":1,"kind":"MESSAGE","payload":{"text":"Safe output","raw_reasoning":"private details"},"created_at":"now"}\n\nid: 2\ndata: {"seq":2,"kind":"REASONING","payload":{"text":"private reasoning"},"created_at":"now"}\n\n'));
    render(<WorkSessions {...props} />); await openSession();
    expect(await screen.findByText('Safe output')).toBeTruthy();
    expect(screen.queryByText('private details')).toBeNull(); expect(screen.queryByText('private reasoning')).toBeNull();
    expect(screen.queryByText('Accepted')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: 'Requirement v3' })[1]);
    expect(props.onOpenRequirement).toHaveBeenCalledWith(3);
  });

  it('ignores a delayed response from a component unmounted during a work switch', async () => {
    const { props, get } = fixture(); let finish!: (value: unknown) => void;
    get.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const { rerender } = render(<WorkSessions key="work-a" {...props} />);
    rerender(<WorkSessions key="work-b" {...props} workId="work-b" />);
    await act(async () => finish({ items: [session('a')], has_more: false }));
    expect(screen.queryByRole('button', { name: /CODEX.*native-a/ })).toBeNull();
  });
  it('queues the existing draft during an active run and preserves Stop behavior', async () => {
    const { props, get, post } = fixture(execution('RUNNING'));
    let pendingItem: Record<string, unknown> | null = null;
    const originalGet = get.getMockImplementation()!, originalPost = post.getMockImplementation()!;
    get.mockImplementation(async path => path.includes('/pending-instruction') ? { item: pendingItem } : originalGet(path));
    post.mockImplementation(async (path, body) => {
      if (path.endsWith('/pending-instruction')) {
        const command = body as Record<string, unknown>;
        pendingItem = { id: 'next-a', work_id: 'work-a', session_id: 'a', ...command,
          state: 'PENDING', reason_code: null, revision: 1, promoted_run_id: null, created_at: '', updated_at: '' };
        return { pending_id: 'next-a', session_id: 'a', request_id: command.request_id,
          predecessor_run_id: command.predecessor_run_id, state: 'PENDING', revision: 1 };
      }
      return originalPost(path, body);
    });
    render(<WorkSessions {...props} />); await openSession();
    fireEvent.change(screen.getByLabelText('Execution input'), { target: { value: 'Next exact instruction' } });
    const queue = await screen.findByRole('button', { name: 'Queue next instruction' });
    await waitFor(() => expect((queue as HTMLButtonElement).disabled).toBe(false));
    await act(async () => { fireEvent.click(queue); });
    expect(post.mock.calls[0][0]).toBe('/api/v1/works/work-a/sessions/a/pending-instruction');
    expect(post.mock.calls[0][1]).toMatchObject({ predecessor_run_id: 'run-a', input: 'Next exact instruction', requirement_version: 3, expected_revision: 2 });
    expect((screen.getByLabelText('Execution input') as HTMLTextAreaElement).value).toBe('');
    expect(screen.getByText('Queued for next execution')).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Stop execution' })); });
    expect(post.mock.calls[1][0]).toBe('/api/v1/works/work-a/runs/run-a/cancel');
    expect(post.mock.calls[1][1]).toMatchObject({ request_id: expect.any(String) });
  });
  it('freezes an unknown queued draft while keeping explicit Stop available', async () => {
    const { props, post } = fixture(execution('RUNNING'));
    post.mockRejectedValueOnce(new Error('lost receipt'));
    render(<WorkSessions {...props} />); await openSession();
    fireEvent.change(screen.getByLabelText('Execution input'), { target: { value: 'Keep this exact draft' } });
    const queue = await screen.findByRole('button', { name: 'Queue next instruction' });
    await waitFor(() => expect((queue as HTMLButtonElement).disabled).toBe(false));
    await act(async () => { fireEvent.click(queue); });
    expect((screen.getByLabelText('Execution input') as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Stop execution' }) as HTMLButtonElement).disabled).toBe(false);
    expect(props.memory.pendingInstructions.size).toBe(1);
    expect(props.memory.drafts.get('work-a:a')).toBe('Keep this exact draft');
  });

  it('Stop remains available during file finalization and cancels through the existing run route', async () => {
    const { props, get, post } = fixture(execution('FINALIZING'));
    const originalGet = get.getMockImplementation()!;
    get.mockImplementation(async path => path.includes('/pending-instruction') ? { item: {
      id: 'next-a', work_id: 'work-a', session_id: 'a', request_id: 'next-request',
      predecessor_run_id: 'run-a', input: 'Pending instruction', requirement_version: 3,
      context_refs: [], state: 'PENDING', revision: 1, reason_code: null, promoted_run_id: null,
    } } : originalGet(path));
    render(<WorkSessions {...props} />); await openSession();
    await screen.findByText('Queued for next execution');
    const stop = screen.getByRole('button', { name: 'Stop execution' }) as HTMLButtonElement;
    expect(stop.disabled).toBe(false);
    await act(async () => { fireEvent.click(stop); });
    expect(post.mock.calls[0][0]).toBe('/api/v1/works/work-a/runs/run-a/cancel');
    expect(post.mock.calls[0][1]).toEqual({ request_id: expect.any(String) });
    expect(post).toHaveBeenCalledTimes(1);
  });
  it('file-finalization Stop preserves the server cancellation capability boundary', async () => {
    const { props, get, post } = fixture(execution('FINALIZING'));
    const originalGet = get.getMockImplementation()!;
    get.mockImplementation(async path => path.endsWith('/sessions/a')
      ? { ...session('a'), capabilities: { ...session('a').capabilities, cancel: false } } : originalGet(path));
    render(<WorkSessions {...props} />); await openSession();
    const stop = screen.getByRole('button', { name: 'Stop execution' }) as HTMLButtonElement;
    expect(stop.disabled).toBe(true);
    fireEvent.click(stop);
    expect(post).not.toHaveBeenCalled();
  });

  it('keeps a promoted execution visible without reloading the same promotion again', async () => {
    const f = promotionFixture();
    render(<WorkSessions {...f.props} />); await openSession();
    await screen.findByRole('button', { name: 'Refresh next instruction' });
    f.promote('run-next');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh next instruction' })); });
    expect(f.sessionReads()).toBe(2);
    expect(screen.getByLabelText('Execution input')).toBeTruthy();
    expect(screen.getByText('Started as an execution')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Execution ended.*Queued run-next/ }).getAttribute('aria-pressed')).toBe('true');
    expect(f.post).not.toHaveBeenCalled();
  });

  it('does not steal manual history selection when the same promotion is read after refresh', async () => {
    const f = promotionFixture();
    render(<WorkSessions {...f.props} />); await openSession();
    await screen.findByRole('button', { name: 'Refresh next instruction' });
    f.promote('run-next');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh next instruction' })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Execution ended.*Fix the issue/ })); });
    f.allowReads(3);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reload sessions' })); });
    expect(f.sessionReads()).toBe(3);
    expect(screen.getByRole('button', { name: /Execution ended.*Fix the issue/ }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: /Execution ended.*Queued run-next/ }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByLabelText('Execution input')).toBeTruthy();
    expect(f.post).not.toHaveBeenCalled();
  });

  it('follows a different promoted execution once after handling the previous promotion', async () => {
    const f = promotionFixture();
    render(<WorkSessions {...f.props} />); await openSession();
    await screen.findByRole('button', { name: 'Refresh next instruction' });
    f.promote('run-next');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh next instruction' })); });
    f.allowReads(3); f.promote('run-later');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh next instruction' })); });
    expect(f.sessionReads()).toBe(3);
    expect(screen.getByRole('button', { name: /Execution ended.*Queued run-later/ }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: /Execution ended.*Queued run-next/ }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByLabelText('Execution input')).toBeTruthy();
    expect(f.post).not.toHaveBeenCalled();
  });

});
