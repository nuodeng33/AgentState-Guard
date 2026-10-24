import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiActionError, type ApiClient } from '../api/client';
import { AgentAppearancePicker } from './AgentAppearancePicker';
import { useAgentAppearances, type AgentAppearance } from './useAgentAppearances';

const agent = { agent_id: 'agent-real-123456', detected_identity: 'CODEX' };
const base = (id: string) => `/api/v1/works/${id}`;
const row = (color: string | null = null, revision = 0): AgentAppearance => ({ agent_id: agent.agent_id, color, revision, updated_at: null });
function harness(client: ApiClient, work = 'work-a', visible = true) {
  function Harness({ id, open }: { id: string; open: boolean }) {
    const state = useAgentAppearances(client, id);
    return <><output data-testid="tint">{state.tints[agent.agent_id] ?? 'default'}</output>
      {open && <AgentAppearancePicker agents={[agent]} state={state} onClose={vi.fn()} />}</>;
  }
  return { Harness, view: <Harness id={work} open={visible} /> };
}
function fixture() {
  const data = new Map<string, AgentAppearance[]>([['work-a', [row()]], ['work-b', [row('#8fb8e8', 3)]]]);
  const get = vi.fn(async (path: string) => {
    const id = path.split('/')[4]; return { work_id: id, items: data.get(id) ?? [] };
  });
  const post = vi.fn(async (path: string, command: { color: string | null; expected_revision: number }) => {
    const id = path.split('/')[4];
    const saved = row(command.color, command.expected_revision + 1); data.set(id, [saved]);
    return { work_id: id, ...saved };
  });
  return { client: { get, post } as unknown as ApiClient, get, post, data };
}
afterEach(cleanup);

it('reads saved colors, provides six named choices and persists a selection/reset', async () => {
  const { client, post, get } = fixture(); const { view } = harness(client); render(view);
  await screen.findByText('Synced with server');
  expect(screen.getAllByRole('radio')).toHaveLength(6);
  fireEvent.click(screen.getByRole('radio', { name: 'Lavender' }));
  await waitFor(() => expect(screen.getByTestId('tint').textContent).toBe('#b9a6f4'));
  expect(post.mock.calls[0][0]).toBe(`${base('work-a')}/agents/${agent.agent_id}/appearance`);
  expect(post.mock.calls[0][1]).toMatchObject({ expected_revision: 0, color: '#b9a6f4' });
  expect(get).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button', { name: 'Use default color' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByTestId('tint').textContent).toBe('default'));
  expect(post.mock.calls[1][1]).toMatchObject({ expected_revision: 1, color: null });
});

it('never claims saved before acknowledgement or applies an optimistic tint', async () => {
  const { client, post, data } = fixture(); let complete!: (value: AgentAppearance & { work_id: string }) => void;
  post.mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }) as ReturnType<typeof post>);
  render(harness(client).view); await screen.findByText('Synced with server');
  fireEvent.click(screen.getByRole('radio', { name: 'Rose' }));
  expect(screen.getByText('Saving color…')).toBeTruthy();
  expect(screen.getByTestId('tint').textContent).toBe('default');
  data.set('work-a', [row('#e3a5bf', 1)]);
  complete({ work_id: 'work-a', ...row('#e3a5bf', 1) });
  await screen.findByText('Synced with server');
  expect(screen.getByTestId('tint').textContent).toBe('#e3a5bf');
});

it('retains an uncertain exact request across panel close and retries it unchanged', async () => {
  const { client, post, data } = fixture(); post.mockRejectedValueOnce(new Error('network outcome unknown'));
  const { Harness, view } = harness(client); const rendered = render(view); await screen.findByText('Synced with server');
  fireEvent.click(screen.getByRole('radio', { name: 'Gold' }));
  await screen.findByRole('alert');
  const original = post.mock.calls[0][1];
  expect(screen.getByRole('radio', { name: 'Blue' }).matches(':disabled')).toBe(true);
  rendered.rerender(<Harness id="work-a" open={false} />);
  rendered.rerender(<Harness id="work-a" open />);
  data.set('work-a', [row('#d9c08a', 1)]);
  fireEvent.click(screen.getByRole('button', { name: 'Retry original color request' }));
  await screen.findByText('Synced with server');
  expect(post.mock.calls[1][1]).toEqual(original);
  expect(screen.getByTestId('tint').textContent).toBe('#d9c08a');
});

it('a conflict requires an authoritative refresh before a new revision can be chosen', async () => {
  const { client, post, data } = fixture(); post.mockRejectedValueOnce(new ApiActionError(409, 'WORK_APPEARANCE_CONFLICT'));
  render(harness(client).view); await screen.findByText('Synced with server');
  fireEvent.click(screen.getByRole('radio', { name: 'Green' }));
  await screen.findByText(/changed in another window/);
  data.set('work-a', [row('#7cc6bc', 4)]);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh saved colors' }));
  await screen.findByText('Synced with server');
  fireEvent.click(screen.getByRole('radio', { name: 'Green' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1][1]).toMatchObject({ expected_revision: 4 });
});

it('late saved replies stay with the original Work and cannot color another Work', async () => {
  const { client, post, data } = fixture(); let complete!: (value: AgentAppearance & { work_id: string }) => void;
  post.mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }) as ReturnType<typeof post>);
  const { Harness, view } = harness(client); const rendered = render(view); await screen.findByText('Synced with server');
  fireEvent.click(screen.getByRole('radio', { name: 'Rose' }));
  rendered.rerender(<Harness id="work-b" open />);
  await waitFor(() => expect(screen.getByTestId('tint').textContent).toBe('#8fb8e8'));
  data.set('work-a', [row('#e3a5bf', 1)]); complete({ work_id: 'work-a', ...row('#e3a5bf', 1) });
  await waitFor(() => expect(post).toHaveBeenCalledOnce());
  expect(screen.getByTestId('tint').textContent).toBe('#8fb8e8');
  rendered.rerender(<Harness id="work-a" open />);
  await waitFor(() => expect(screen.getByTestId('tint').textContent).toBe('#e3a5bf'));
});

it('a late initial read cannot replace the new Work and read failure remains actionable', async () => {
  const { client, get } = fixture(); let complete!: (value: { work_id: string; items: AgentAppearance[] }) => void;
  get.mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }) as ReturnType<typeof get>);
  const { Harness, view } = harness(client); const rendered = render(view);
  rendered.rerender(<Harness id="work-b" open />);
  await waitFor(() => expect(screen.getByTestId('tint').textContent).toBe('#8fb8e8'));
  complete({ work_id: 'work-a', items: [row('#d9c08a', 1)] });
  await screen.findByText('Synced with server'); expect(screen.getByTestId('tint').textContent).toBe('#8fb8e8');
  get.mockRejectedValueOnce(new Error('read failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh saved colors' }));
  await screen.findByText('Could not read or save colors. Refresh and retry.');
});
