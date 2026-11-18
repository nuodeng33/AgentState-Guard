import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import WorkPage, { type Board, type Card } from './WorkPage';
import type { WorkArtifactRef, WorkReview } from './workContentTypes';

const workId = '11111111-1111-4111-8111-111111111111';
const a = '22222222-2222-4222-8222-222222222222';
const b = '33333333-3333-4333-8333-333333333333';
const reviewId = '44444444-4444-4444-8444-444444444444';
const submissionId = '55555555-5555-4555-8555-555555555555';
const refA = { artifact_id: a, version: 1 };
const refB = { artifact_id: b, version: 1 };
const originalCard: Card = { kind: 'requirement', ref_id: workId, version: 2, x: 51, y: 9, width: 410, height: 440, scroll: 73 };
const reviewPath = '/api/v1/works/' + workId + '/reviews/' + reviewId;
const exactArtifact = '/api/v1/works/' + workId + '/artifacts/' + a + '/1';
const latestArtifact = '/api/v1/works/' + workId + '/artifacts/' + a + '/2';
const requirementPath = '/api/v1/works/' + workId + '/requirements/1';

function fixture({ refs = [refA, refB], full = false, readReview }: {
  refs?: WorkArtifactRef[]; full?: boolean; readReview?: (value: WorkReview) => Promise<WorkReview>;
} = {}) {
  const review: WorkReview = { id: reviewId, submission_id: submissionId, requirement_version: 1, reviewer: 'Reviewer Ada',
    decision: 'CHANGES_REQUESTED', created_at: '2026-11-18T08:15:00Z', body: 'Please keep the first edit. The last line is part of the full review.',
    refs: { artifacts: refs, files: [] } };
  const tasks = full ? Array.from({ length: 39 }, (_, index) => ({ id: 't' + index, title: 'Task ' + index })) : [];
  const cards = [originalCard, ...tasks.map((task, index): Card => ({
    kind: 'task', ref_id: task.id, version: null, x: index * 24, y: 100, width: 350, height: 300, scroll: 0,
  }))];
  let layout: Board = { schema_version: 1, surface: 'board', viewport: { x: 20, y: 20, zoom: 1 }, cards };
  const work = { id: workId, title: 'Delivery revisions', owner: 'Owner', revision: 4, requirement_version: 2,
    requirement: 'Current requirement.', tasks, artifacts: [
      { id: a, version: 1, title: 'Opening score', requirement_version: 1 },
      { id: a, version: 2, title: 'Opening score', requirement_version: 2 },
      { id: b, version: 1, title: 'Timing notes', requirement_version: 1 },
    ] };
  const get = vi.fn(async (path: string) => {
    if (path.startsWith('/api/v1/works?')) return { items: [work] };
    if (path === '/api/v1/works/' + workId) return work;
    if (path.endsWith('/view')) return { revision: 1, state: layout };
    if (path === reviewPath) return readReview ? readReview(review) : review;
    if (path.includes('/submissions?')) return { items: [{
      id: submissionId, responsibility: 'Author', current_requirement: false, requirement_version: 1,
      reviews: [{ ...review, body: 'Summary in the delivery record.' }], acceptances: [], refs: review.refs, created_at: review.created_at,
    }] };
    if (path === exactArtifact) return { title: 'Opening score', body: 'Fixed artifact version one.' };
    if (path === latestArtifact) return { title: 'Opening score', body: 'Different newer version.' };
    if (path === requirementPath) return { body: 'Historical requirement for this review.' };
    if (path.includes('/requirements/')) return { body: 'Current requirement in the workbench.' };
    if (path.includes('/tasks/')) return { title: 'Task', body: 'Task already on the workbench.' };
    if (path.endsWith('/execution-summary')) return { work_id: workId, requirement_version: 2, counts: {}, items: [] };
    return { items: [] };
  });
  const post = vi.fn(async (_path: string, payload: { state: Board }) => {
    layout = payload.state;
    return { revision: 2 };
  });
  const client = { bootstrap: vi.fn(), get, post } as unknown as ApiClient;
  history.replaceState(null, '', '/?work=' + workId);
  return { client, get, post, review, cards };
}

async function records() {
  fireEvent.click(await screen.findByRole('button', { name: 'More actions' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delivery' }));
  fireEvent.click(await screen.findByRole('tab', { name: /Records/ }));
  fireEvent.click(await screen.findByRole('button', { name: /Author.*Requirement v1/ }));
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  history.replaceState(null, '', '/');
});

it('compares the chosen fixed artifact and saves and restores its review card', async () => {
  const { client, get, post, review } = fixture();
  const first = render(<WorkPage client={client} />);
  await records();
  expect(screen.getByRole('button', { name: 'Read review with Timing notes v1' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Read review with Opening score v2' })).toBeNull();
  expect(get.mock.calls.some(([path]) => path === reviewPath)).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Read review with Opening score v1' }));
  const dialog = await screen.findByRole('dialog', { name: 'Focus reading' });
  const reviewPane = dialog.querySelector<HTMLElement>('.source-sheet[data-side="review"]')!;
  const artifactPane = within(dialog).getByRole('region', { name: 'Artifact v1' });
  expect(await within(reviewPane).findByText(review.body)).toBeTruthy();
  expect(within(reviewPane).getByText(review.reviewer)).toBeTruthy();
  expect(within(reviewPane).getByText('Changes requested')).toBeTruthy();
  expect(await within(artifactPane).findByText('Fixed artifact version one.')).toBeTruthy();
  expect(get.mock.calls.filter(([path]) => path === reviewPath)).toHaveLength(1);
  expect(get.mock.calls.some(([path]) => path === latestArtifact)).toBe(false);

  const picker = within(dialog).getByRole('group', { name: 'Choose document' });
  expect(within(picker).getByRole('button', { name: 'Review' }).getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(within(picker).getByRole('button', { name: 'Artifact v1' }));
  expect(artifactPane.dataset.current).toBe('true');

  fireEvent.keyDown(document, { key: 'Escape' });
  const card = screen.getByRole('article', { name: 'Review' });
  expect(document.activeElement).toBe(within(card).getByRole('button', { name: 'Focus reading Review' }));
  fireEvent.click(within(card).getByRole('button', { name: 'Compare requirement v1' }));
  const historical = await screen.findByRole('dialog', { name: 'Focus reading' });
  expect(await within(historical).findByText('Historical requirement for this review.')).toBeTruthy();
  fireEvent.keyDown(document, { key: 'Escape' });
  fireEvent.click(screen.getByRole('button', { name: 'Save workspace' }));
  await waitFor(() => expect(post).toHaveBeenCalled());
  const saved = post.mock.calls[post.mock.calls.length - 1][1].state;
  expect(saved.cards).toContainEqual(originalCard);
  expect(saved.cards).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: 'review', ref_id: reviewId, version: null }),
    expect.objectContaining({ kind: 'artifact', ref_id: a, version: 1 }),
    expect.objectContaining({ kind: 'requirement', ref_id: workId, version: 1 }),
  ]));
  expect(saved.space?.nodes.map(node => node.kind)).not.toContain('review');
  first.unmount();
  render(<WorkPage client={client} />);
  const resumed = await screen.findByRole('article', { name: 'Review' });
  expect(resumed.dataset.current).toBe('true');
  expect(await within(resumed).findByText(review.body)).toBeTruthy();
});

it('uses the historical requirement for a review of a submission with no artifacts, including when the board is full', async () => {
  const { client, post, review } = fixture({ refs: [], full: true });
  const { container } = render(<WorkPage client={client} />);
  await records();
  fireEvent.click(screen.getByRole('button', { name: 'Read review with requirement v1' }));
  const dialog = await screen.findByRole('dialog', { name: 'Focus reading' });
  expect(await within(dialog).findByText(review.body)).toBeTruthy();
  expect(await within(dialog).findByText('Historical requirement for this review.')).toBeTruthy();
  expect(within(dialog).getByRole('status').textContent).toContain('40 cards');
  expect(container.querySelectorAll('.work-card')).toHaveLength(40);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(container.querySelectorAll('.work-card')).toHaveLength(40);
  expect(post).not.toHaveBeenCalled();
});

it('leaves the workbench in place if the delivery panel closes before the review read resolves', async () => {
  let finish: (review: WorkReview) => void = () => {};
  const { client, get, review } = fixture({ readReview: () => new Promise(resolve => { finish = resolve; }) });
  render(<WorkPage client={client} />);
  await records();
  fireEvent.click(screen.getByRole('button', { name: 'Read review with Opening score v1' }));
  await waitFor(() => expect(get).toHaveBeenCalledWith(reviewPath));
  fireEvent.click(screen.getByRole('button', { name: 'Close delivery' }));
  await act(async () => finish(review));
  expect(screen.queryByRole('dialog', { name: 'Focus reading' })).toBeNull();
  expect(screen.queryByRole('article', { name: 'Review' })).toBeNull();
});

it('distinguishes two open reviews by their read contents after reopening', async () => {
  const other = '66666666-6666-4666-8666-666666666666';
  const otherPath = '/api/v1/works/' + workId + '/reviews/' + other;
  const { client, get, review } = fixture();
  const original = get.getMockImplementation()!;
  get.mockImplementation(async path => {
    if (path.endsWith('/view')) {
      const state: Board = { schema_version: 1, surface: 'board', viewport: { x: 0, y: 0, zoom: 1 }, cards: [
        { ...originalCard, kind: 'review', ref_id: reviewId, version: null },
        { ...originalCard, kind: 'review', ref_id: other, version: null, x: 500 },
      ] };
      return { revision: 4, state };
    }
    if (path === otherPath) return { ...review, id: other, body: 'Restore the second edit.' };
    return original(path);
  });
  const view = render(<WorkPage client={client} />);
  const names = [/Review · Reviewer Ada · Changes requested · Please keep the first edit/,
    /Review · Reviewer Ada · Changes requested · Restore the second edit/];
  for (const name of names) await screen.findByRole('option', { name });
  view.unmount();
  render(<WorkPage client={client} />);
  for (const name of names) await screen.findByRole('option', { name });
  expect(get.mock.calls.filter(([path]) => path.includes('/reviews/')).map(([path]) => path)).toEqual([reviewPath, otherPath]);
});
