import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import WorkPage, { type Board } from './WorkPage';
import { frameTwoCards } from './workbenchViewport';

const workId = '11111111-1111-4111-8111-111111111111';
const artifactId = '22222222-2222-4222-8222-222222222222';
const cards: Board['cards'] = [
  { kind: 'requirement', ref_id: workId, version: 1, x: 0, y: 0, width: 400, height: 430, scroll: 17 },
  { kind: 'artifact', ref_id: artifactId, version: 1, x: 436, y: 0, width: 400, height: 430, scroll: 19 },
];

function fixture(surface: Board['surface'], narrow = false) {
  history.replaceState(null, '', '/?work=' + workId);
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('work-canvas') ? (narrow ? 390 : 776) : 0;
  });
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('work-canvas') ? 504 : 0;
  });
  const computed = window.getComputedStyle;
  if (narrow) vi.spyOn(window, 'getComputedStyle').mockImplementation(el => {
    const style = computed(el);
    if (el.classList.contains('work-canvas')) style.setProperty('--work-board-stack', '1');
    return style;
  });
  const viewport = { x: 11, y: 12, zoom: 1 };
  const board: Board = { schema_version: 1, surface, viewport, cards };
  const artifact = { id: artifactId, title: 'Opening script', version: 1, requirement_version: 1 };
  const work = { id: workId, title: 'Podcast opening', owner: 'Production', revision: 1,
    requirement_version: 1, requirement: 'Current requirement preview', tasks: [], artifacts: [artifact] };
  const get = vi.fn(async (path: string) => {
    if (path.endsWith('/view')) return { revision: 1, state: board };
    if (path.includes('/artifacts/')) return { title: artifact.title, body: 'Read the artifact text', requirement_version: 1 };
    if (path.includes('/requirements/')) return { body: 'Read the requirement text' };
    if (path.endsWith('/execution-summary')) return { work_id: workId, requirement_version: 1, counts: {}, items: [] };
    if (path.startsWith('/api/v1/works?')) return { items: [work] };
    if (path === '/api/v1/works/' + workId) return work;
    return { items: [] };
  });
  const client = { bootstrap: vi.fn(), get, post: vi.fn(async () => ({ revision: 2 })) } as unknown as ApiClient;
  return render(<WorkPage client={client} />);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
  history.replaceState(null, '', '/');
});

it('frames the cards within the usable width and height without making them too small', () => {
  const view = frameTwoCards(cards, 776, 504, 1)!;
  expect(view.zoom).toBeCloseTo(.870813, 5);
  for (const card of cards) {
    expect(view.x + card.x * view.zoom).toBeGreaterThanOrEqual(24 - Number.EPSILON);
    expect(view.y + card.y * view.zoom).toBeGreaterThanOrEqual(24);
    expect(view.x + (card.x + card.width) * view.zoom).toBeLessThanOrEqual(752);
    expect(view.y + (card.y + card.height) * view.zoom).toBeLessThanOrEqual(480);
  }
  expect(frameTwoCards(cards, 700, 504, 1)).toBeNull();
  expect(frameTwoCards(cards, 776, 390, 1)).toBeNull();
  expect(frameTwoCards([cards[0], { ...cards[1], x: 200 }], 1200, 800, 1)).toBeNull();
});

it.each(['desk', 'relationship'] as const)('frames two cards when entering from the %s and keeps explicit card location available', async origin => {
  const { container } = fixture('space');
  let button: HTMLElement;
  if (origin === 'desk') button = await screen.findByRole('button', { name: 'Opening script v1' });
  else {
    fireEvent.click(await screen.findByRole('button', { name: 'Show relationships' }));
    button = await screen.findByRole('button', { name: 'Open Opening script' });
  }
  fireEvent.click(button);
  const article = await screen.findByRole('article', { name: 'Opening script' });
  const read = within(article).getByRole('button', { name: 'Focus reading Artifact 1' });
  expect(document.activeElement).toBe(read);
  const world = container.querySelector<HTMLElement>('.work-world')!;
  const view = frameTwoCards(cards, 776, 504, 1)!;
  expect(world.style.transform).toBe('translate(' + view.x + 'px, ' + view.y + 'px) scale(' + view.zoom + ')');
  fireEvent.change(screen.getByRole('combobox', { name: 'Locate an open card' }), { target: { value: 'requirement:' + workId + ':1' } });
  expect(world.style.transform).toBe('translate(' + (388 - 200 * view.zoom) + 'px, 20px) scale(' + view.zoom + ')');
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Focus reading Requirement 1' }));
});

it.each([{ surface: 'board', narrow: false }, { surface: 'space', narrow: true }] as const)(
  'preserves the viewport for $surface entry with mobile stacking $narrow', async ({ surface, narrow }) => {
    const { container } = fixture(surface, narrow);
    if (surface === 'space') fireEvent.click(await screen.findByRole('button', { name: 'Opening script v1' }));
    await screen.findByRole('article', { name: 'Opening script' });
    expect(container.querySelector<HTMLElement>('.work-world')?.style.transform).toBe('translate(11px, 12px) scale(1)');
  },
);
