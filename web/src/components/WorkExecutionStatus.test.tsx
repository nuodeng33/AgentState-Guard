import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import WorkExecutionStatus from './WorkExecutionStatus';
import { I18nProvider } from '../i18n/I18nProvider';

const summary = { work_id: 'work', requirement_version: 2, work_revision: 7, observed_at: 'now', has_more: false,
  counts: { WAITING_INPUT: 1 }, items: [{ id: 'run-one', session_id: 'session-one', provider: 'CODEX', state: 'WAITING_INPUT',
    requirement_version: 1, updated_at: '2026-10-15T01:00:00Z' }] };
afterEach(() => { cleanup(); vi.useRealTimers(); Object.defineProperty(document, 'hidden', { configurable: true, value: false }); });

describe('work execution attention', () => {
  it.each([
    ['en-US', 1, '1 execution needs attention'], ['en-US', 2, '2 executions need attention'],
    ['zh-CN', 1, '1 项执行需要处理'], ['zh-CN', 2, '2 项执行需要处理'],
  ] as const)('localizes %s attention count %i', async (locale, n, name) => {
    const get = vi.fn().mockResolvedValue({ ...summary, counts: { WAITING_INPUT: n } });
    render(<I18nProvider systemLanguage={locale}><WorkExecutionStatus client={{ get } as unknown as ApiClient}
      workId="work" visible onOpen={vi.fn()} onRequirement={vi.fn()} /></I18nProvider>);
    expect(await screen.findByRole('button', { name })).toBeTruthy();
  });

  it('shows a localized fallback for unknown activity time', async () => {
    const get = vi.fn().mockResolvedValue({ ...summary, items: [{ ...summary.items[0], updated_at: 'not-a-time' }] });
    render(<WorkExecutionStatus client={{ get } as unknown as ApiClient} workId="work" visible onOpen={vi.fn()} onRequirement={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: /need.*attention/ }));
    expect(screen.getByText('Time unknown').closest('small')?.textContent).toContain('Last activity Time unknown');
    expect(screen.queryByText(/not-a-time/)).toBeNull();
  });

  it('refreshes without the session panel and opens the exact question, preserving stale results on failure', async () => {
    vi.useFakeTimers();
    const get = vi.fn().mockResolvedValue(summary); const onOpen = vi.fn(); const onRequirement = vi.fn();
    render(<WorkExecutionStatus client={{ get } as unknown as ApiClient} workId="work" visible onOpen={onOpen} onRequirement={onRequirement} />);
    await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: '1 execution needs attention' }));
    fireEvent.click(screen.getByRole('button', { name: /CODEX.*Needs input/ }));
    expect(onOpen).toHaveBeenCalledWith({ sessionId: 'session-one', runId: 'run-one' });
    expect(onRequirement).toHaveBeenCalledWith(2);
    get.mockRejectedValueOnce(new Error('offline'));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.getByText(/Status update failed/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '1 execution needs attention' }));
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
