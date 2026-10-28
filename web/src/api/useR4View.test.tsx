import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ApiClient } from './client';
import { useR4View } from './useR4View';
import type { VerificationState } from './types';
function Probe({ client }: { client: ApiClient }) {
  const result = useR4View<{ verification: VerificationState }>('runtime', client);
  return <output>{result.data?.verification.state ?? result.phase}</output>;
}
afterEach(() => { cleanup(); vi.useRealTimers(); });
it('automatically receives a completed first verification without starting discovery', async () => {
  vi.useFakeTimers(); let state = 'PENDING';
  const get = vi.fn(async () => ({ verification: { state } })), post = vi.fn();
  const view = render(<Probe client={{ get, post } as unknown as ApiClient} />);
  await act(async () => {}); expect(screen.getByText('PENDING')).toBeTruthy();
  state = 'VERIFIED';
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByText('VERIFIED')).toBeTruthy(); expect(post).not.toHaveBeenCalled();
  view.unmount(); const calls = get.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(get.mock.calls.length).toBe(calls);
});
it('never overlaps a slow projection read with a polling read', async () => {
  vi.useFakeTimers(); let resolve!: (value: unknown) => void;
  const get = vi.fn(() => new Promise(value => { resolve = value; }));
  render(<Probe client={{ get } as unknown as ApiClient} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(get).toHaveBeenCalledTimes(1);
  await act(async () => { resolve({ verification: { state: 'VERIFIED' } }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(get).toHaveBeenCalledTimes(2);
});
