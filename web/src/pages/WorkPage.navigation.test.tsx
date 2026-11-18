import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiRequestError, type ApiClient } from '../api/client';
import WorkPage, { type Board } from './WorkPage';

const workId = '11111111-1111-4111-8111-111111111111';
const work = { id: workId, title: 'Visible work', owner: 'Owner', revision: 1,
  requirement_version: 1, requirement: 'Requirement', tasks: [], artifacts: [] };
const board: Board = { schema_version: 1, surface: 'board', viewport: { x: 24, y: 24, zoom: 1 },
  cards: [{ kind: 'requirement', ref_id: workId, version: 1, x: 0, y: 0, width: 400, height: 430, scroll: 0 }] };

function clientWith(overrides: { detail?: () => Promise<unknown>; content?: () => Promise<unknown>;
  save?: () => Promise<unknown>; view?: Board } = {}) {
  const get = vi.fn(async (path: string) => {
    if (path.endsWith('/execution-summary')) return { work_id: workId, requirement_version: 1, counts: {}, items: [] };
    if (path.includes('?limit=')) return { items: [work] };
    if (path.endsWith('/view')) return { revision: 1, state: overrides.view ?? board };
    if (path.includes('/requirements/')) return overrides.content ? overrides.content() : { body: 'Readable requirement' };
    return overrides.detail ? overrides.detail() : work;
  });
  const post = vi.fn(async () => overrides.save ? overrides.save() : { revision: 2 });
  return { client: { bootstrap: vi.fn(), get, post } as unknown as ApiClient, get, post };
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); history.replaceState(null, '', '/'); });

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

  it('opens a record from the overview, locates a relationship card in the mounted canvas, and restores focus on return', async () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('work-canvas') ? 560 : 800;
    });
    const { client } = clientWith({ view: { ...board, surface: 'space', viewport: { x: 10, y: 16, zoom: 0.8 } } });
    const { container } = render(<WorkPage client={client} />);
    const record = await screen.findByRole('link', { name: /Visible work/ });
    expect(record.getAttribute('href')).toBe(`?work=${workId}`);
    fireEvent.click(record);
    const node = await screen.findByRole('button', { name: 'Open Visible work' });
    act(() => node.focus());
    fireEvent.click(node, { detail: 0 });

    const preview = await screen.findByRole('button', { name: 'Focus reading Requirement 1' });
    expect(document.activeElement).toBe(preview);
    const viewport = container.querySelector('.work-world')?.getAttribute('style');
    expect(viewport).toContain('translate(120px, 20px) scale(0.8)');
    fireEvent.click(preview);

    const dialog = screen.getByRole('dialog', { name: 'Focus reading' });
    await within(dialog).findByText('Readable requirement');
    const back = within(dialog).getByRole('button', { name: 'Back to workbench' });
    expect(document.activeElement).toBe(back);
    fireEvent.keyDown(back, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(within(dialog).getByRole('region', { name: 'Requirement' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.activeElement).toBe(preview);
    expect(container.querySelector('.work-world')?.getAttribute('style')).toBe(viewport);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Open Visible work' }));
  });

  it('returns focus to a visible control when dismissing a creation form', async () => {
    vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(function (this: HTMLElement) {
      const hiddenDrawer = this.closest('.work-sidebar') && !this.closest('[data-nav-open]');
      const rects = hiddenDrawer ? [] : [new DOMRect(0, 0, 80, 32)];
      return Object.assign(rects, { item: (index: number) => rects[index] ?? null });
    });
    const { client } = clientWith();
    const { container } = render(<WorkPage client={client} />);
    await screen.findByRole('link', { name: /Visible work/ });

    const file = screen.getByRole('button', { name: 'File' });
    act(() => file.focus());
    fireEvent.keyDown(file, { key: 'ArrowDown' });
    fireEvent.click(screen.getByRole('button', { name: 'Create work' }));
    const form = screen.getByRole('dialog', { name: 'Create work' });
    expect(document.activeElement).toBe(within(form).getByRole('textbox', { name: 'Work title' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.activeElement).toBe(file);

    const drawer = container.querySelector<HTMLButtonElement>('.work-mobile-works')!;
    fireEvent.click(drawer);
    fireEvent.click(screen.getByRole('button', { name: 'New work' }));
    expect(drawer.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Create work' })).getByRole('button', { name: 'Cancel' }));
    expect(document.activeElement).toBe(drawer);
  });

  it('gives an empty work creation actions without opening either board or relationships, and can collapse the directory', async () => {
    history.replaceState(null, '', `/?work=${workId}`);
    const { client } = clientWith({ view: { ...board, surface: 'space' } });
    render(<WorkPage client={client} />);
    const contents = within(await screen.findByRole('complementary', { name: 'Work contents' }));
    const toggle = contents.getByRole('button', { name: 'Work contents' });
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(contents.queryByRole('button', { name: 'Add task' })).toBeNull();
    fireEvent.click(toggle);
    fireEvent.click(contents.getByRole('button', { name: 'Add task' }));
    expect(screen.getByRole('dialog', { name: 'Add task' })).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(contents.getByRole('button', { name: 'Save artifact' }));
    expect(screen.getByRole('dialog', { name: 'Save artifact' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Show relationships' })).toBeTruthy();
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
