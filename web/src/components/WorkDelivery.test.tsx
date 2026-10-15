import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiActionError, type ApiClient } from '../api/client';
import type { ExecutionRun, RunEvent } from '../api/execution';
import WorkPage from '../pages/WorkPage';
import WorkDelivery from './WorkDelivery';
import { createWorkSessionMemory, type WorkRunOutput } from './WorkSessions';

const run: ExecutionRun = { id: 'run-a', session_id: 'session-a', requirement_version: 3, status: 'COMPLETED',
  input_summary: 'Fix issue', last_seq: 3, coverage: null, change_refs: [], pending_responses: [], created_at: 'now',
  file_result: { status: 'AVAILABLE', scope_id: 'scope', version_id: 'directory-result', baseline_id: 'baseline',
    reason_code: null, attribution: 'UNATTRIBUTED' } };
const events: RunEvent[] = [
  { seq: 1, kind: 'TEXT', payload: { text: 'Original result.' }, created_at: 'now' },
  { seq: 2, kind: 'MESSAGE', payload: { text: 'Message is not original output' }, created_at: 'now' },
  { seq: 3, kind: 'REASONING', payload: { text: 'Private reasoning' }, created_at: 'now' },
];
const output: WorkRunOutput = { sessionId: 'session-a', run, events, omitted: 0 };
const artifacts = [{ id: 'artifact-a', title: 'Implementation', version: 1, requirement_version: 3 },
  { id: 'artifact-a', title: 'Implementation', version: 2, requirement_version: 3 },
  { id: 'old-artifact', title: 'Earlier artifact', version: 1, requirement_version: 2 }];
const submission = (id = 'submission-a', version = 3) => ({ id, requirement_version: version, run_id: 'run-a',
  responsibility: 'Author', refs: { artifacts: [{ artifact_id: 'artifact-a', version: 1 }],
    files: [{ scope_id: 'scope', version_id: 'directory-result', digest: 'immutable-digest' }] },
  current_requirement: version === 3, reviews: [], acceptances: [], created_at: '2026-09-30' });
function fixture(records = [submission()]) {
  const get = vi.fn(async (path: string): Promise<unknown> => {
    if (path.includes('/submissions?')) return { items: records, has_more: false };
    if (path === '/api/v1/protection/scopes') return { items: [{ id: 'scope', source_id: 'local', root: '/real/work', status: 'PROTECTED', enabled: true },
      { id: 'unrelated', source_id: 'local', root: '/unrelated', status: 'PROTECTED', enabled: true }] };
    if (path.endsWith('/protection-links')) return { items: [{ kind: 'scope', object_id: 'scope' }] };
    if (path.includes('/runs/run-a/events?')) return { items: events.map(event => ({ seq: event.seq, type: event.kind, payload: event.payload, observed_at: event.created_at })), has_more: false };
    if (path.includes('/versions?')) return { items: [
      { id: 'directory-fixed', scope_id: 'scope', created_at: 'today', available: 1, metadata_available: true, history_status: 'FULL' },
      { id: 'expired', scope_id: 'scope', created_at: 'yesterday', available: 0, metadata_available: false, history_status: 'DETAILS_EXPIRED' },
    ] };
    throw new Error(`Unexpected path ${path}`);
  });
  const post = vi.fn(async (path: string, _body: unknown): Promise<Record<string, unknown>> => {
    if (path.endsWith('/artifacts')) return { artifact_id: 'saved-artifact', version: 1, revision: 6 };
    if (path.endsWith('/submissions')) return { submission_id: 'submission-a', revision: 6 };
    return { revision: 7 };
  });
  const client = { bootstrap: vi.fn(), get, post } as unknown as ApiClient;
  const props = { client, workId: 'work-a', revision: 5, requirementVersion: 3, owner: 'Owner', artifacts,
    output, visible: true, memory: createWorkSessionMemory(), onClose: vi.fn(), onRevision: vi.fn(),
    onChanged: vi.fn(async () => {}), onOpenRequirement: vi.fn(), onShowArtifact: vi.fn() };
  return { props, get, post };
}
async function openRecord(name = /Author.*Requirement v3/) {
  fireEvent.click(screen.getByRole('tab', { name: /Records/ }));
  fireEvent.click(await screen.findByRole('button', { name }));
}
afterEach(() => { cleanup(); history.replaceState(null, '', '/'); });

describe('local Work delivery', () => {
  it('offers linked directory versions without requiring a managed session or run', async () => {
    const { props, get, post } = fixture([]); render(<WorkDelivery {...props} output={null} artifacts={[]} />);
    await screen.findByRole('option', { name: '/real/work' });
    expect(screen.queryByRole('option', { name: '/unrelated' })).toBeNull();
    expect(get.mock.calls.some(([path]) => path.includes('/sessions?'))).toBe(false);
    fireEvent.change(screen.getByLabelText('Protected directory'), { target: { value: 'scope' } });
    fireEvent.click(await screen.findByLabelText(/today · directory-fixed/));
    fireEvent.change(screen.getByLabelText('Responsible person'), { target: { value: 'Human' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit delivery' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toMatchObject({ artifact_refs: [],
      file_refs: [{ scope_id: 'scope', version_id: 'directory-fixed' }], responsibility: 'Human' });
    expect(post.mock.calls[0][1]).not.toHaveProperty('run_id');
  });

  it('promotes only selected original TEXT events and never submits automatically', async () => {
    const { props, post } = fixture(); render(<WorkDelivery {...props} />);
    await screen.findByLabelText('Output event 1');
    expect(screen.queryByLabelText('Output event 2')).toBeNull();
    expect(screen.queryByText('Private reasoning')).toBeNull();
    expect((screen.getByRole('button', { name: 'Save artifact' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Artifact title'), { target: { value: 'Saved output' } });
    fireEvent.click(screen.getByLabelText('Output event 1'));
    fireEvent.click(screen.getByRole('button', { name: 'Save artifact' }));
    await screen.findByText('Artifact saved');
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]).toEqual(['/api/v1/works/work-a/sessions/session-a/runs/run-a/artifacts',
      { request_id: expect.any(String), expected_revision: 5, title: 'Saved output', event_seqs: [1] }]);
    expect(props.onRevision).toHaveBeenCalledWith(6);
    fireEvent.click(screen.getByRole('button', { name: 'Open saved artifact · v1' }));
    expect(props.onShowArtifact).toHaveBeenCalledWith('saved-artifact', 1);
  });

  it('sends edited content explicitly without claiming original provenance', async () => {
    const { props, post } = fixture(); render(<WorkDelivery {...props} />);
    await screen.findByLabelText('Output event 1');
    fireEvent.click(screen.getByLabelText('Edited output'));
    expect((screen.getByLabelText('Edited content') as HTMLTextAreaElement).value).toBe('Original result.');
    fireEvent.change(screen.getByLabelText('Edited content'), { target: { value: 'My revised result' } });
    fireEvent.change(screen.getByLabelText('Artifact title'), { target: { value: 'Revised output' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save artifact' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toEqual({ request_id: expect.any(String), expected_revision: 5, title: 'Revised output', body: 'My revised result' });
  });

  it('preserves output drafts and frozen UUID payloads after an uncertain mutation', async () => {
    const { props, post } = fixture(); post.mockRejectedValueOnce(new Error('Lost reply'));
    const { rerender } = render(<WorkDelivery {...props} />); await screen.findByLabelText('Output event 1');
    fireEvent.click(screen.getByLabelText('Edited output'));
    fireEvent.change(screen.getByLabelText('Artifact title'), { target: { value: 'Draft title' } });
    fireEvent.change(screen.getByLabelText('Edited content'), { target: { value: 'Draft body' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save artifact' })); await screen.findByRole('alert');
    rerender(<WorkDelivery {...props} visible={false} revision={9} />);
    rerender(<WorkDelivery {...props} revision={9} />);
    expect((screen.getByLabelText('Edited content') as HTMLTextAreaElement).value).toBe('Draft body');
    fireEvent.click(screen.getByRole('button', { name: 'Save artifact' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1][1]).toEqual(post.mock.calls[0][1]);
  });

  it('submits explicit immutable artifact and real directory versions', async () => {
    const { props, post } = fixture(); render(<WorkDelivery {...props} />);
    await screen.findByRole('option', { name: '/real/work' });
    expect(screen.queryByRole('option', { name: '/unrelated' })).toBeNull();
    expect((screen.getByLabelText(/Earlier artifact/, { selector: 'input' }) as HTMLInputElement).disabled).toBe(true);
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText(/Implementation · v1/));
    fireEvent.click(screen.getByLabelText(/Reference execution/));
    fireEvent.change(screen.getByLabelText('Protected directory'), { target: { value: 'scope' } });
    fireEvent.click(await screen.findByLabelText(/today · directory-fixed/));
    expect((screen.getByLabelText(/yesterday · expired/) as HTMLInputElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Responsible person'), { target: { value: 'Author' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit delivery' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]).toEqual(['/api/v1/works/work-a/submissions', {
      request_id: expect.any(String), expected_revision: 5, requirement_version: 3, run_id: 'run-a',
      artifact_refs: [{ artifact_id: 'artifact-a', version: 1 }], file_refs: [{ scope_id: 'scope', version_id: 'directory-fixed' }], responsibility: 'Author',
    }]);
    expect(post.mock.calls.some(([path]) => path.includes('/acceptances'))).toBe(false);
  });

  it('keeps review approval separate from an owner acceptance decision', async () => {
    const { props, post } = fixture(); render(<WorkDelivery {...props} />); await openRecord();
    fireEvent.change(screen.getByLabelText('Reviewer'), { target: { value: 'Reviewer' } });
    fireEvent.change(screen.getByLabelText('Review decision'), { target: { value: 'APPROVED' } });
    fireEvent.change(screen.getByLabelText('Feedback'), { target: { value: 'Checked against requirement v3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Record review' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe('/api/v1/works/work-a/submissions/submission-a/reviews');
    expect(post.mock.calls[0][1]).toMatchObject({ decision: 'APPROVED', reviewer: 'Reviewer', body: 'Checked against requirement v3' });
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText('Acceptance decision'), { target: { value: 'ACCEPTED' } });
    fireEvent.change(screen.getByLabelText('Acceptance basis'), { target: { value: 'All required behavior verified' } });
    fireEvent.change(screen.getByLabelText('Acceptance owner'), { target: { value: 'Wrong owner' } });
    expect((screen.getByRole('button', { name: 'Record acceptance' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Acceptance owner'), { target: { value: 'Owner' } });
    fireEvent.click(screen.getByRole('button', { name: 'Record acceptance' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1][0]).toBe('/api/v1/works/work-a/submissions/submission-a/acceptances');
    expect(post.mock.calls[1][1]).toMatchObject({ decision: 'ACCEPTED', owner: 'Owner', basis: 'All required behavior verified', expected_revision: 7 });
  });

  it('shows old acceptance as history and prevents accepting an earlier requirement', async () => {
    const old = { ...submission('old-submission', 2), acceptances: [{ id: 'acceptance', decision: 'ACCEPTED', basis: 'Earlier basis', owner: 'Owner', created_at: 'then' }] };
    const { props, get } = fixture(); const originalGet = get.getMockImplementation()!;
    get.mockImplementation(async path => path.includes('/submissions?') ? { items: [old], has_more: false } : originalGet(path));
    render(<WorkDelivery {...props} />); await openRecord(/Author.*Requirement v2/);
    expect(screen.getByText('Historical · Accepted · Owner')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Acceptance owner'), { target: { value: 'Owner' } });
    fireEvent.change(screen.getByLabelText('Acceptance basis'), { target: { value: 'New basis' } });
    expect((screen.getByRole('button', { name: 'Record acceptance' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Historical submission/)).toBeTruthy();
  });

  it('retains separate feedback drafts and safe reasons when review fails', async () => {
    const { props, post } = fixture(); post.mockRejectedValueOnce(new ApiActionError(409, 'WORK_REVISION_CONFLICT'));
    render(<WorkDelivery {...props} />); await openRecord();
    fireEvent.change(screen.getByLabelText('Reviewer'), { target: { value: 'Reviewer' } });
    fireEvent.change(screen.getByLabelText('Feedback'), { target: { value: 'Preserved review' } });
    fireEvent.change(screen.getByLabelText('Acceptance basis'), { target: { value: 'Separate acceptance draft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Record review' }));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('WORK_REVISION_CONFLICT'));
    expect((screen.getByLabelText('Feedback') as HTMLTextAreaElement).value).toBe('Preserved review');
    expect((screen.getByLabelText('Acceptance basis') as HTMLTextAreaElement).value).toBe('Separate acceptance draft');
    expect(props.memory.commands.size).toBe(0);
  });

  it('ignores late output promotion on a switched Work and retains its replay receipt', async () => {
    const { props, post } = fixture(); let resolve!: (value: Record<string, unknown>) => void;
    post.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const { rerender } = render(<WorkDelivery key="work-a" {...props} />); await screen.findByLabelText('Output event 1');
    fireEvent.change(screen.getByLabelText('Artifact title'), { target: { value: 'Late result' } });
    fireEvent.click(screen.getByLabelText('Output event 1')); fireEvent.click(screen.getByRole('button', { name: 'Save artifact' }));
    rerender(<WorkDelivery key="work-b" {...props} workId="work-b" output={null} />);
    await act(async () => resolve({ artifact_id: 'late-artifact', version: 1, revision: 8 }));
    expect(props.onRevision).not.toHaveBeenCalled(); expect(props.onChanged).not.toHaveBeenCalled();
    expect(screen.queryByText('Artifact saved')).toBeNull();
    expect(props.memory.commands.has('work-a:delivery:run-a:save-output')).toBe(true);
  });

  it('loads older real TEXT events by sequence without losing current output', async () => {
    const { props, get } = fixture(); const originalGet = get.getMockImplementation()!;
    get.mockImplementation(async path => {
      if (path.includes('/runs/run-a/events?after=0')) return { items: [{ seq: 1, type: 'TEXT', payload: { text: 'Original result.' }, observed_at: 'now' }], has_more: true };
      if (path.includes('/runs/run-a/events?after=1')) return { items: [{ seq: 4, type: 'TEXT', payload: { text: 'More output' }, observed_at: 'now' }], has_more: false };
      return originalGet(path);
    });
    render(<WorkDelivery {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Load more output' }));
    expect(await screen.findByLabelText('Output event 4')).toBeTruthy();
    expect(screen.getByLabelText('Output event 1')).toBeTruthy();
    expect(get).toHaveBeenCalledWith('/api/v1/works/work-a/runs/run-a/events?after=1&limit=100');
  });

  it('opens locally from Work actions and preserves the workbench layout on reload', async () => {
    const { props, get, post } = fixture(); const originalGet = get.getMockImplementation()!;
    get.mockImplementation(async path => {
      if (path === '/api/v1/works/work-a') return { id: 'work-a', title: 'Real work', owner: 'Owner', revision: 5,
        requirement_version: 3, requirement: 'Requirement', tasks: [], artifacts };
      if (path.endsWith('/view')) return { revision: 1, state: { schema_version: 1, surface: 'board',
        viewport: { x: 71, y: 27, zoom: 1 }, cards: [{ kind: 'requirement', ref_id: 'work-a', version: 3, x: 19, y: 23, width: 400, height: 430, scroll: 8 }] } };
      if (path.endsWith('/agents')) return { items: [] };
      if (path.endsWith('/requirements/3')) return { title: 'Requirement', body: 'Requirement body' };
      if (path.includes('/artifacts/')) return { title: 'Implementation', body: 'Artifact body', requirement_version: 3, source: 'AGENT_RUN', source_run_id: 'run-a' };
      return originalGet(path);
    });
    history.replaceState(null, '', '/?work=work-a'); render(<WorkPage client={props.client} />);
    await screen.findByText('Requirement body'); const card = document.querySelector('[data-card="requirement:work-a:3"]') as HTMLElement;
    const left = card.style.left, top = card.style.top;
    fireEvent.click(screen.getByRole('button', { name: 'More actions' })); fireEvent.click(screen.getByRole('button', { name: 'Delivery' }));
    await screen.findByRole('region', { name: 'Delivery' });
    expect(screen.getByText('Requirement body')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reload delivery' }));
    await waitFor(() => expect(get.mock.calls.filter(([path]) => path === '/api/v1/works/work-a').length).toBe(2));
    expect(card.style.left).toBe(left); expect(card.style.top).toBe(top);
    fireEvent.click(screen.getByRole('button', { name: 'Open artifact Implementation v1' }));
    expect(await screen.findByText('Artifact body')).toBeTruthy();
    expect(screen.getByText('Original execution output · Requirement v3')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('region', { name: 'Delivery' })).toBeNull();
    expect(post.mock.calls.every(([path]) => path.endsWith('/view'))).toBe(true);
  });
});
