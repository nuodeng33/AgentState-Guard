import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiActionError, type ApiClient } from '../api/client';
import WorkPage, { type Board } from './WorkPage';
import type { WorkRestSceneProps } from './WorkRestScene';

vi.mock('./WorkRestScene', () => ({ default: (props: WorkRestSceneProps) => <section>
  <p>{`${props.title} | v${props.requirementVersion} | ${props.taskCount} tasks | ${props.artifactCount} artifacts`}</p>
  <button onClick={props.onReturn}>Return from observation</button>
</section> }));

const id = '11111111-1111-4111-8111-111111111111';
const artifact = '22222222-2222-4222-8222-222222222222';
const board: Board = { schema_version: 1, surface: 'board', viewport: { x: -120, y: 40, zoom: .8 }, cards: [
  { kind: 'requirement', ref_id: id, version: 1, x: 10, y: 20, width: 400, height: 430, scroll: 0 },
  { kind: 'artifact', ref_id: artifact, version: 1, x: 470, y: 30, width: 450, height: 460, scroll: 0 },
] };
function fixture(state: Board = board) {
  const post = vi.fn().mockResolvedValue({ revision: 6 });
  const client = { bootstrap: vi.fn(), post, get: vi.fn(async (path: string) => {
    if (path.endsWith('/view')) return { revision: 5, state };
    if (path.includes('/requirements/')) return { body: '<script>do not execute</script>\nReal requirement' };
    if (path.includes('/artifacts/')) return { title: 'Real artifact', body: 'Saved content', requirement_version: 1 };
    return { id, title: 'Actual work', owner: 'Owner', revision: 2, requirement_version: 1,
      tasks: [], artifacts: [{ id: artifact, title: 'Real artifact', version: 1, requirement_version: 1 }] };
  }) } as unknown as ApiClient;
  history.replaceState(null, '', `/?work=${id}`);
  return { client, post };
}
afterEach(() => { cleanup(); history.replaceState(null, '', '/'); });

describe('persistent workbench', () => {
  it('opens a real node through the space and returns through each layer without losing position', async () => {
    const { client } = fixture({ ...board, surface: 'space', space: { viewport: { x: 47, y: -20, zoom: 1.2 }, nodes: [
      { kind: 'requirement', ref_id: id, version: 1, x: 0, y: 0 },
      { kind: 'artifact', ref_id: artifact, version: 1, x: 300, y: 30 },
    ] } });
    const { container } = render(<WorkPage client={client} />);
    expect(screen.queryByRole('button', { name: 'Open Real artifact' })).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Show relationships' }));
    const node = await screen.findByRole('button', { name: 'Open Real artifact' });
    const before = container.querySelector('.work-space-world')?.getAttribute('style');
    fireEvent.click(node);
    expect(await screen.findByText('Saved content')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Focus reading Artifact 1' }));
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(container.querySelector('.work-space-world')?.getAttribute('style')).toBe(before);
  });

  it('keeps the workbench viewport when resumed and attaches wheel zoom after switching surfaces', async () => {
    const state: Board = { ...board, surface: 'space', space: { viewport: { x: 0, y: 0, zoom: 1 }, nodes: [
      { kind: 'requirement', ref_id: id, version: 1, x: 0, y: 0 },
      { kind: 'artifact', ref_id: artifact, version: 1, x: 3000, y: 0 },
    ] } };
    const { client, post } = fixture(state);
    const { container } = render(<WorkPage client={client} />);
    await screen.findByRole('button', { name: 'Continue workbench' });
    fireEvent.click(screen.getByRole('button', { name: 'Show relationships' }));
    fireEvent.click(screen.getByRole('button', { name: 'Fit canvas' }));
    expect(container.querySelector('.work-space-world')?.getAttribute('style')).toContain('scale(0.25)');
    fireEvent.click(screen.getByRole('button', { name: 'Continue workbench' }));
    expect(container.querySelector('.work-world')?.getAttribute('style')).toContain('scale(0.8)');
    fireEvent.wheel(container.querySelector('.work-canvas')!, { deltaY: -200 });
    fireEvent.click(screen.getByRole('button', { name: 'Save workspace' }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[post.mock.calls.length - 1][1].state.viewport.zoom).toBeGreaterThan(.8);
  });
  it('reads full real content and preserves the board across focused reading', async () => {
    const { client } = fixture(); const { container } = render(<WorkPage client={client} />);
    await screen.findByText('Saved content');
    expect(container.querySelector('.work-world')?.getAttribute('style')).toContain('scale(0.8)');
    expect(container.querySelector('.work-prose script')).toBeNull();
    expect(screen.getByText(/do not execute/)).toBeTruthy();
    const before = container.querySelector('[data-card^="artifact:"]')?.getAttribute('style');
    fireEvent.click(screen.getByRole('button', { name: 'Focus reading Artifact 1' }));
    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByText('Saved content')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: /Back to workbench/ }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Focus reading Artifact 1' }));
    expect(container.querySelector('[data-card^="artifact:"]')?.getAttribute('style')).toBe(before);
  });

  it('persists zoom and removes a card reference without deleting content', async () => {
    const { client, post } = fixture(); render(<WorkPage client={client} />);
    await screen.findByText('Saved content');
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close card Artifact 1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save workspace' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const [path, command] = post.mock.calls[0];
    expect(path).toBe(`/api/v1/works/${id}/view`);
    expect(command.expected_revision).toBe(5);
    expect(command.state.viewport.zoom).toBeCloseTo(.96);
    expect(command.state.cards).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Add related content' }));
    expect(screen.getByRole('button', { name: /Real artifact/ })).toBeTruthy();
  });

  it('retries uncertain saves with the same request and does not overwrite a conflicting view', async () => {
    const { client, post } = fixture(); post.mockRejectedValueOnce(new Error('network')).mockRejectedValueOnce(new ApiActionError(409, 'WORK_VIEW_CONFLICT'));
    render(<WorkPage client={client} />); await screen.findByText('Saved content');
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save workspace' }));
    await screen.findByText('Workspace not saved');
    fireEvent.click(screen.getByRole('button', { name: 'Save workspace' }));
    await screen.findByText(/Another window saved a newer workspace/);
    expect(post.mock.calls[1][1].request_id).toBe(post.mock.calls[0][1].request_id);
    expect((screen.getByRole('button', { name: 'Save workspace' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('resumes an empty archived workbench with real requirement and latest artifact cards', async () => {
    const { client } = fixture({ ...board, surface: 'space', cards: [], viewport: { x: 100000, y: 100000, zoom: .25 }, space: { viewport: { x: 0, y: 0, zoom: 1 }, nodes: [
      { kind: 'requirement', ref_id: id, version: 1, x: 0, y: 0 },
      { kind: 'artifact', ref_id: artifact, version: 1, x: 300, y: 0 },
    ] } });
    const { container } = render(<WorkPage client={client} />);
    await screen.findByRole('button', { name: 'Continue workbench' });
    expect(screen.queryByRole('button', { name: 'Open Real artifact' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Continue workbench' }));
    expect(await screen.findByText('Saved content')).toBeTruthy();
    expect(container.querySelectorAll('.work-card')).toHaveLength(2);
    expect(container.querySelector('.work-world')?.getAttribute('style')).not.toContain('100000');
  });

  it('centers the core despite a saved distant relationship viewport and restores that view on expansion', async () => {
    const { client } = fixture({ ...board, surface: 'space', space: { viewport: { x: 100000, y: -90000, zoom: .4 }, nodes: [
      { kind: 'requirement', ref_id: id, version: 1, x: 50000, y: -40000 },
      { kind: 'artifact', ref_id: artifact, version: 1, x: 50300, y: -40000 },
    ] } });
    const { container } = render(<WorkPage client={client} />);
    await screen.findByRole('button', { name: 'Open Actual work' });
    expect(container.querySelector('.work-space-world')?.getAttribute('style')).toContain('calc(50% - 50000px)');
    expect(screen.queryByRole('button', { name: 'Zoom in' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show relationships' }));
    expect(container.querySelector('.work-space-world')?.getAttribute('style')).toContain('100000px');
    expect(screen.getByRole('button', { name: 'Zoom in' })).toBeTruthy();
  });

  it('keeps an empty board actionable', async () => {
    const { client } = fixture({ ...board, cards: [], viewport: { x: 100000, y: 100000, zoom: .25 } });
    const { container } = render(<WorkPage client={client} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Open requirement version' }));
    expect(await screen.findByText(/Real requirement/)).toBeTruthy();
    expect(container.querySelectorAll('.work-card')).toHaveLength(1);
    expect(container.querySelector('.work-world')?.getAttribute('style')).toContain('scale(1)');
  });

  it('opens 3D with the same facts and returns by button or Escape without losing the 2D layout', async () => {
    const { client } = fixture();
    const { container, rerender } = render(<WorkPage client={client} />);
    await screen.findByText('Saved content');
    const layout = container.querySelector('.work-world')?.getAttribute('style');
    fireEvent.click(screen.getByRole('button', { name: '3D view' }));
    expect(await screen.findByText('Actual work | v1 | 0 tasks | 1 artifacts')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Return from observation' }));
    expect(screen.queryByText('Actual work | v1 | 0 tasks | 1 artifacts')).toBeNull();
    expect(container.querySelector('.work-world')?.getAttribute('style')).toBe(layout);
    fireEvent.click(screen.getByRole('button', { name: '3D view' }));
    await screen.findByRole('button', { name: 'Return from observation' });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(container.querySelector('.work-world')?.getAttribute('style')).toBe(layout);
    expect(screen.queryByRole('button', { name: 'Return from observation' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '3D view' }));
    await screen.findByRole('button', { name: 'Return from observation' });
    rerender(<WorkPage client={client} visible={false} />);
    expect(screen.queryByText('Actual work | v1 | 0 tasks | 1 artifacts')).toBeNull();
  });
});
