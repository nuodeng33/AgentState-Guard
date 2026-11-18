import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import WorkPage, { type Board, type Card } from './WorkPage';

const workId = '11111111-1111-4111-8111-111111111111';
const artifactId = '22222222-2222-4222-8222-222222222222';
const artifactKey = `artifact:${artifactId}:2`;
const requirementKey = `requirement:${workId}:1`;
const requirement: Card = { kind: 'requirement', ref_id: workId, version: 1, x: 0, y: 0, width: 400, height: 430, scroll: 0 };
const artifact: Card = { ...requirement, kind: 'artifact', ref_id: artifactId, version: 2, x: 440, scroll: 140 };
const board: Board = { schema_version: 1, surface: 'board', viewport: { x: 72, y: 39, zoom: .8 }, cards: [requirement, artifact] };
const work = { id: workId, title: 'Launch plan', owner: 'Owner', revision: 1, requirement_version: 1, requirement: 'Project premise',
  tasks: [], artifacts: [{ id: artifactId, version: 2, title: 'Final summary', requirement_version: 1 }] };

function open(state = board, detail = work, save = async () => ({ revision: 9 })) {
  const get = vi.fn(async (path: string) => {
    if (path.includes('/works?')) return { items: [detail] };
    if (path.endsWith('/view')) return { revision: 8, state };
    if (path.includes('/requirements/')) return { body: 'Project premise' };
    if (path.includes('/artifacts/')) return { title: 'Final summary', body: 'Actual delivered content' };
    if (path === `/api/v1/works/${workId}`) return detail;
    return { counts: {}, items: [] };
  });
  const post = vi.fn(save);
  history.replaceState(null, '', `/?work=${workId}`);
  return { ...render(<WorkPage client={{ get, post } as unknown as ApiClient} />), get, post };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
  history.replaceState(null, '', '/');
});

it('resumes the exact artifact version after reopening at the saved viewport and scroll', async () => {
  const first = open();
  await screen.findByText('Actual delivered content');
  fireEvent.change(screen.getByRole('combobox', { name: 'Locate an open card' }), { target: { value: artifactKey } });
  first.unmount();

  // If reopening calls the explicit locate behavior, the measured canvas
  // width would make it center and dirty the viewport.
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(640);
  const reopened = open();
  await screen.findByText('Actual delivered content');
  expect((screen.getByRole('combobox', { name: 'Locate an open card' }) as HTMLSelectElement).value).toBe(artifactKey);
  const currentCard = reopened.container.querySelector('[data-card][data-current="true"]')!;
  expect(currentCard.getAttribute('data-card')).toBe(artifactKey);
  expect(within(currentCard as HTMLElement).getByRole('region', { name: 'Artifact' }).scrollTop).toBe(140);
  expect(reopened.container.querySelector('.work-world')?.getAttribute('style')).toContain('translate(72px, 39px) scale(0.8)');
  fireEvent.click(screen.getByRole('button', { name: 'Save workspace' }));
  expect(reopened.post).not.toHaveBeenCalled();
});

it('falls back after the remembered artifact version disappears from current Work detail', async () => {
  const first = open();
  const read = await screen.findByRole('button', { name: 'Focus reading Artifact 2' });
  fireEvent.click(read);
  await within(screen.getByRole('dialog', { name: 'Focus reading' })).findByText('Actual delivered content');
  first.unmount();
  open({ ...board, cards: [artifact, requirement] }, { ...work, artifacts: [] });
  await screen.findByText('Project premise');
  expect((screen.getByRole('combobox', { name: 'Locate an open card' }) as HTMLSelectElement).value).toBe(requirementKey);
});

it('forgets a closed card and does not select it from an older saved layout', async () => {
  const first = open();
  const locator = await screen.findByRole('combobox', { name: 'Locate an open card' }) as HTMLSelectElement;
  fireEvent.change(locator, { target: { value: artifactKey } });
  fireEvent.click(screen.getByRole('button', { name: 'Close card Artifact 2' }));
  expect(locator.value).toBe(requirementKey);
  first.unmount();

  open(board);
  await screen.findByText('Actual delivered content');
  expect((screen.getByRole('combobox', { name: 'Locate an open card' }) as HTMLSelectElement).value).toBe(requirementKey);
});

it('keeps the current selection while a cached layout is waiting for its save', async () => {
  const { get, post } = open(board, work, () => new Promise(() => {}));
  await screen.findByText('Actual delivered content');
  fireEvent.change(screen.getByRole('combobox', { name: 'Locate an open card' }), { target: { value: artifactKey } });
  localStorage.removeItem('asg.recentWorkCards');
  fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
  fireEvent.click(screen.getByRole('button', { name: 'All work' }));
  fireEvent.click(await screen.findByRole('link', { name: /Launch plan/ }));
  await screen.findByText('Actual delivered content');

  expect((screen.getByRole('combobox', { name: 'Locate an open card' }) as HTMLSelectElement).value).toBe(artifactKey);
  expect(get.mock.calls.filter(([path]) => path.endsWith('/view'))).toHaveLength(1);
  expect(post).toHaveBeenCalledOnce();
});
