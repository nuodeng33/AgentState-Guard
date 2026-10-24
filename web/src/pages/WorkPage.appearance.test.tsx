import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import WorkPage from './WorkPage';
import type { WorkRestSceneProps } from './WorkRestScene';

const workId = '11111111-1111-4111-8111-111111111111';
const agentId = 'actual-agent-123456';
vi.mock('./WorkRestScene', () => ({ default: (props: WorkRestSceneProps) =>
  <output data-testid="3d-color">{props.agentTints?.[agentId]}</output> }));
afterEach(() => { cleanup(); history.replaceState(null, '', '/'); });

it('loads authoritative color into both 2D and 3D and closes color dialog without leaving Work', async () => {
  const get = vi.fn(async (path: string) => {
    if (path.endsWith('/agent-appearances')) return { work_id: workId, items: [{ agent_id: agentId, color: '#e3a5bf', revision: 2, updated_at: null }] };
    if (path.endsWith('/agents')) return { items: [{ agent_id: agentId, detected_identity: 'CODEX', task_ids: [], lifecycle: 'RUNNING', presence: 'CURRENT', association: 'USER_LINKED' }] };
    if (path.endsWith('/execution-summary')) return { work_id: workId, requirement_version: 1, items: [], counts: {} };
    if (path.endsWith('/view')) return { revision: 1, state: { schema_version: 1, surface: 'space', cards: [], viewport: { x: 0, y: 0, zoom: 1 },
      space: { viewport: { x: 0, y: 0, zoom: 1 }, nodes: [{ kind: 'requirement', ref_id: workId, version: 1, x: 0, y: 0 }] } } };
    return { id: workId, title: 'Color Work', requirement: 'Actual Work', revision: 1, requirement_version: 1, tasks: [], artifacts: [] };
  });
  history.replaceState(null, '', `/?work=${workId}`);
  const { container } = render(<WorkPage client={{ get, post: vi.fn() } as unknown as ApiClient} />);
  const open = await screen.findByRole('button', { name: 'Agent colors' });
  fireEvent.click(open);
  await screen.findByRole('dialog', { name: 'Agent colors' });
  expect((screen.getByRole('radio', { name: 'Rose' }) as HTMLInputElement).checked).toBe(true);
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByRole('button', { name: 'Agent colors' })).toBeTruthy();
  expect(container.querySelector('svg.work-film path[stroke="#e3a5bf"]')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '3D view' }));
  expect((await screen.findByTestId('3d-color')).textContent).toBe('#e3a5bf');
});
