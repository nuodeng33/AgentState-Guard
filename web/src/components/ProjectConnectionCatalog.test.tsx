import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import ProjectConnectionCatalog from './ProjectConnectionCatalog';
afterEach(cleanup);
it('reads sources only after opening, scans only on explicit click, then refreshes session choices', async () => {
  const get = vi.fn(async () => ({ items: [] })); const post = vi.fn(async () => ({})); const onRegistered = vi.fn();
  render(<ProjectConnectionCatalog client={{ get, post } as unknown as ApiClient} disabled={false} onRegistered={onRegistered} />);
  expect(get).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('Connect programs and session stores'));
  await waitFor(() => expect(get).toHaveBeenCalledWith('/api/v1/discovery/sources'));
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Scan programs and environments' }));
  await waitFor(() => expect(onRegistered).toHaveBeenCalledTimes(1));
  expect(post).toHaveBeenCalledTimes(1); expect(post).toHaveBeenCalledWith('/api/v1/discovery/refresh', {});
});
it('does not scan when a conflicting command is pending', async () => {
  const post = vi.fn();
  render(<ProjectConnectionCatalog client={{ get: vi.fn(async () => ({ items: [] })), post } as unknown as ApiClient} disabled onRegistered={vi.fn()} />);
  fireEvent.click(screen.getByText('Connect programs and session stores'));
  fireEvent.click(screen.getByRole('button', { name: 'Scan programs and environments' }));
  expect(post).not.toHaveBeenCalled();
});
