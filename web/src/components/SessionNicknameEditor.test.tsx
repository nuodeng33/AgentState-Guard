import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiActionError, type ApiClient } from '../api/client';
import SessionNicknameEditor from './SessionNicknameEditor';

function fixture(sessionId = 'session-a') {
  let row = { id: sessionId, work_id: 'work-a', revision: 4, nickname: 'Old session', state: 'READY' };
  const get = vi.fn(async (_path: string) => row);
  const patch = vi.fn(async (_path: string, body: unknown) => {
    const command = body as { expected_revision: number; nickname: string };
    row = { ...row, revision: command.expected_revision + 1, nickname: command.nickname };
    return { session_id: sessionId, revision: row.revision, state: row.state };
  });
  const props = { client: { get, patch } as unknown as ApiClient, workId: 'work-a', sessionId, onChanged: vi.fn(async () => {}) };
  return { get, patch, props };
}
async function open() { fireEvent.click(await screen.findByRole('button', { name: 'Rename session' })); await waitFor(() => expect((screen.getByRole('button', { name: 'Save session name' }) as HTMLButtonElement).disabled).toBe(false)); }
afterEach(cleanup);

it('renames only the selected session using its fresh revision, allowing an empty nickname', async () => {
  const f = fixture(); render(<SessionNicknameEditor {...f.props} />); await open();
  fireEvent.change(screen.getByLabelText('Session nickname'), { target: { value: '' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save session name' })); });
  expect(f.patch.mock.calls).toEqual([['/api/v1/works/work-a/sessions/session-a', { request_id: expect.any(String), expected_revision: 4, nickname: '' }]]);
  expect(f.props.onChanged).toHaveBeenCalledOnce();
});

it('retains an unknown exact command across unmount and does not resend automatically', async () => {
  const f = fixture(); f.patch.mockRejectedValueOnce(new Error('lost reply'));
  const view = render(<SessionNicknameEditor {...f.props} />); await open();
  fireEvent.change(screen.getByLabelText('Session nickname'), { target: { value: 'My nickname' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save session name' })); });
  const original = f.patch.mock.calls[0];
  expect((screen.getByLabelText('Session nickname') as HTMLInputElement).disabled).toBe(true);
  view.unmount(); render(<SessionNicknameEditor {...f.props} />);
  expect(screen.getByDisplayValue('My nickname')).toBeTruthy(); expect(f.patch).toHaveBeenCalledTimes(1);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry same rename' })); });
  expect(f.patch.mock.calls[1]).toEqual(original);
});

it('conflict preserves draft and requires explicit reload before a fresh command', async () => {
  const f = fixture(); f.patch.mockRejectedValueOnce(new ApiActionError(409, 'SESSION_REVISION_CONFLICT'));
  render(<SessionNicknameEditor {...f.props} />); await open();
  fireEvent.change(screen.getByLabelText('Session nickname'), { target: { value: 'Preserved draft' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save session name' })); });
  expect(screen.getByDisplayValue('Preserved draft')).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Save session name' }) as HTMLButtonElement).disabled).toBe(true);
  f.get.mockResolvedValue({ id: 'session-a', work_id: 'work-a', revision: 8, nickname: 'Changed elsewhere', state: 'READY' });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Reload session name (replaces draft)' })); });
  expect(screen.getByDisplayValue('Changed elsewhere')).toBeTruthy();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save session name' })); });
  expect(f.patch.mock.calls[1][1]).toMatchObject({ expected_revision: 8 });
});

it('after confirmed rename only retries failed refresh', async () => {
  const f = fixture(); f.props.onChanged.mockRejectedValueOnce(new Error());
  render(<SessionNicknameEditor {...f.props} />); await open();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save session name' })); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh saved name' })); });
  expect(f.patch).toHaveBeenCalledTimes(1); expect(f.props.onChanged).toHaveBeenCalledTimes(2);
});

it('ignores an old session read after selection changes and does not rename another session', async () => {
  const first = fixture(); let resolve!: (value: unknown) => void;
  first.get.mockImplementation(() => new Promise<unknown>(done => { resolve = done; }) as ReturnType<typeof first.get>);
  const view = render(<SessionNicknameEditor {...first.props} />);
  const second = fixture('session-b'); view.rerender(<SessionNicknameEditor {...second.props} />);
  await act(async () => resolve({ id: 'session-a', work_id: 'work-a', revision: 100, nickname: 'Stale name' }));
  expect(await screen.findByText('Old session')).toBeTruthy(); expect(screen.queryByText('Stale name')).toBeNull();
  await open(); fireEvent.change(screen.getByLabelText('Session nickname'), { target: { value: 'Second name' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save session name' })); });
  expect(second.patch.mock.calls[0][0]).toBe('/api/v1/works/work-a/sessions/session-b');
  expect(first.patch).not.toHaveBeenCalled();
});

it('load failure blocks writes and never leaks an exception or substitutes another API verb', async () => {
  const f = fixture(); f.get.mockRejectedValue(new Error('secret path'));
  render(<SessionNicknameEditor {...f.props} />);
  expect(await screen.findByRole('alert')).toBeTruthy(); expect(screen.queryByText('secret path')).toBeNull();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Rename session' })); });
  expect((screen.getByRole('button', { name: 'Save session name' }) as HTMLButtonElement).disabled).toBe(true);
  expect(f.patch).not.toHaveBeenCalled();
});
