import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiRequestError, type ApiClient } from '../api/client';
import WorkPage, { type Board } from './WorkPage';

const workId = '11111111-1111-4111-8111-111111111111';
const work = { id: workId, title: 'Visible work', owner: 'Owner', revision: 1,
  requirement_version: 1, requirement: 'Requirement', tasks: [], artifacts: [] };
const board: Board = { schema_version: 1, surface: 'board', viewport: { x: 24, y: 24, zoom: 1 },
  cards: [{ kind: 'requirement', ref_id: workId, version: 1, x: 0, y: 0, width: 400, height: 430, scroll: 0 }] };

function clientWith(overrides: { detail?: () => Promise<unknown>; content?: () => Promise<unknown>;
  save?: () => Promise<unknown> } = {}) {
  const get = vi.fn(async (path: string) => {
    if (path.endsWith('/execution-summary')) return { work_id: workId, requirement_version: 1, counts: {}, items: [] };
    if (path.includes('?limit=')) return { items: [work] };
    if (path.endsWith('/view')) return { revision: 1, state: board };
    if (path.includes('/requirements/')) return overrides.content ? overrides.content() : { body: 'Readable requirement' };
    return overrides.detail ? overrides.detail() : work;
  });
  const post = vi.fn(async () => overrides.save ? overrides.save() : { revision: 2 });
  return { client: { bootstrap: vi.fn(), get, post } as unknown as ApiClient, get, post };
}

afterEach(() => { cleanup(); history.replaceState(null, '', '/'); });

describe('work navigation recovery', () => {
  it.each([
    [503, 'The service is busy.'], [404, 'This content is missing'], [null, 'The local service connection'],
  ] as const)('explains content read failures (%s) and retries only the read', async (status, message) => {
    history.replaceState(null, '', `/?work=${workId}`);
    const content = vi.fn().mockRejectedValueOnce(new ApiRequestError('PRIVATE_SERVER_DETAIL', status)).mockResolvedValue({ body: 'Readable requirement' });
    const { client, post } = clientWith({ content });
    render(<WorkPage client={client} />);
    expect((await screen.findByRole('alert')).textContent).toContain(message);
    expect(screen.queryByText('PRIVATE_SERVER_DETAIL')).toBeNull();
    expect(content).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Readable requirement')).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });
  it('can reach the work list after a workspace save fails', async () => {
    history.replaceState(null, '', `/?work=${workId}`);
    const { client, post } = clientWith({ save: async () => { throw new Error('offline'); } });
    render(<WorkPage client={client} />);
    await screen.findByText('Readable requirement');
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    fireEvent.click(screen.getByRole('button', { name: /All work/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(await screen.findByRole('button', { name: /Visible work/ })).toBeTruthy();
    expect(location.search).not.toContain('work=');
  });

  it('offers the work list when a direct link cannot load its selected work', async () => {
    history.replaceState(null, '', `/?work=${workId}`);
    const { client, get } = clientWith({ detail: async () => { throw new Error('not found'); } });
    render(<WorkPage client={client} />);
    expect(await screen.findByRole('button', { name: /Visible work/ })).toBeTruthy();
    expect(get).toHaveBeenCalledWith('/api/v1/works?limit=50&offset=0');
    expect(location.search).not.toContain('work=');
  });

  it('keeps content and return controls available after a content retry and surface switches', async () => {
    history.replaceState(null, '', `/?work=${workId}`);
    const content = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ body: 'Readable requirement' });
    const { client } = clientWith({ content });
    render(<WorkPage client={client} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Readable requirement')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Back to relationship space' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Continue workbench' }));
    expect(await screen.findByText('Readable requirement')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /All work/ }));
    expect(await screen.findByRole('button', { name: /Visible work/ })).toBeTruthy();
  });

  it('keeps a failed in-flight save on its original work when another work opens', async () => {
    const otherId = '33333333-3333-4333-8333-333333333333';
    const other = { ...work, id: otherId, title: 'Other work' };
    let rejectFirst!: (reason: Error) => void;
    const firstSave = new Promise<never>((_, reject) => { rejectFirst = reject; });
    const post = vi.fn((path: string) => path.includes(workId) ? firstSave : Promise.resolve({ revision: 2 }));
    const get = vi.fn(async (path: string) => {
      if (path.endsWith('/execution-summary')) return { work_id: path.includes(otherId) ? otherId : workId, requirement_version: 1, counts: {}, items: [] };
      if (path.includes('?limit=')) return { items: [work, other] };
      if (path.endsWith('/view')) return { revision: 1, state: { ...board, cards: [{ ...board.cards[0], ref_id: path.includes(otherId) ? otherId : workId }] } };
      if (path.includes('/requirements/')) return { body: path.includes(otherId) ? 'Other requirement' : 'Readable requirement' };
      return path.includes(otherId) ? other : work;
    });
    const client = { bootstrap: vi.fn(), get, post } as unknown as ApiClient;
    history.replaceState(null, '', `/?work=${workId}`);
    render(<WorkPage client={client} />);
    await screen.findByText('Readable requirement');
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    fireEvent.click(screen.getByRole('button', { name: /All work/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(`/api/v1/works/${workId}/view`, expect.anything()));
    fireEvent.click(await screen.findByRole('button', { name: /Other work/ }));
    await screen.findByText('Other requirement');
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save workspace' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(`/api/v1/works/${otherId}/view`, expect.anything()));
    rejectFirst(new Error('offline'));
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Workspace saved'));
    fireEvent.click(screen.getByRole('button', { name: /All work/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Visible work/ }));
    await screen.findByText('Readable requirement');
    expect(screen.getByRole('status').textContent).toContain('Workspace not saved');
    expect(screen.getByRole('button', { name: 'Save workspace' })).toBeTruthy();
  });
  it('starts saving the previous workspace immediately when switching directly in the sidebar', async () => {
    const otherId = '33333333-3333-4333-8333-333333333333';
    const other = { ...work, id: otherId, title: 'Other work' };
    const get = vi.fn(async (path: string) => {
      if (path.includes('?limit=')) return { items: [work, other] };
      if (path.endsWith('/execution-summary')) return { items: [], counts: {} };
      if (path.split('?')[0].endsWith('/agents') || path.endsWith('/agent-appearances')) return { items: [] };
      if (path.endsWith('/view')) return { revision: 1, state: { ...board, cards: [{ ...board.cards[0], ref_id: path.includes(otherId) ? otherId : workId }] } };
      if (path.includes('/requirements/')) return { body: path.includes(otherId) ? 'Other requirement' : 'Readable requirement' };
      return path.includes(otherId) ? other : work;
    });
    const post = vi.fn().mockResolvedValue({ revision: 2 });
    history.replaceState(null, '', `/?work=${workId}`);
    render(<WorkPage client={{ bootstrap: vi.fn(), get, post } as unknown as ApiClient} />);
    await screen.findByText('Readable requirement');
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    post.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Other work' }));
    expect(post).toHaveBeenCalledWith(`/api/v1/works/${workId}/view`, expect.objectContaining({
      state: expect.objectContaining({ viewport: expect.objectContaining({ zoom: expect.any(Number) }) }),
    }));
    expect(post.mock.calls[0][1].state.viewport.zoom).toBeGreaterThan(1);
    await screen.findByText('Other requirement');
    expect(location.search).toContain(otherId);
  });

});
