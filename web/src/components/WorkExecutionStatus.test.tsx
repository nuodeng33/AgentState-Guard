import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import WorkExecutionStatus from './WorkExecutionStatus';

const summary = { work_id: 'work', requirement_version: 2, work_revision: 7, observed_at: 'now', has_more: false,
  counts: { WAITING_INPUT: 1 }, items: [{ id: 'run-one', session_id: 'session-one', provider: 'CODEX', state: 'WAITING_INPUT',
    requirement_version: 1, updated_at: '2026-10-15T01:00:00Z' }] };
afterEach(() => { cleanup(); vi.useRealTimers(); Object.defineProperty(document, 'hidden', { configurable: true, value: false }); });

describe('work execution attention', () => {
  it('refreshes without the session panel and opens the exact question, preserving stale results on failure', async () => {
    vi.useFakeTimers();
    const get = vi.fn().mockResolvedValue(summary); const onOpen = vi.fn(); const onRequirement = vi.fn();
    render(<WorkExecutionStatus client={{ get } as unknown as ApiClient} workId="work" visible onOpen={onOpen} onRequirement={onRequirement} />);
    await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: '1 executions need attention' }));
    fireEvent.click(screen.getByRole('button', { name: /CODEX.*Needs input/ }));
    expect(onOpen).toHaveBeenCalledWith({ sessionId: 'session-one', runId: 'run-one' });
    expect(onRequirement).toHaveBeenCalledWith(2);
    get.mockRejectedValueOnce(new Error('offline'));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.getByText(/Status update failed/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '1 executions need attention' }));
    expect(screen.getByRole('button', { name: /CODEX.*Needs input/ })).toBeTruthy();
  });

  it('pauses polling while the document is hidden and resumes when visible', async () => {
    vi.useFakeTimers(); const get = vi.fn().mockResolvedValue({ ...summary, counts: {}, items: [] });
    render(<WorkExecutionStatus client={{ get } as unknown as ApiClient} workId="work" visible onOpen={vi.fn()} onRequirement={vi.fn()} />);
    await act(async () => {}); expect(get).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(get).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    await act(async () => document.dispatchEvent(new Event('visibilitychange')));
    expect(get).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('region')).toBeNull();
  });
});
