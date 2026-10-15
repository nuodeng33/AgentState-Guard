import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import WorkRestScene, { projectedLabelScale } from './WorkRestScene';

vi.mock('../vendor/three.module.js', () => ({
  WebGLRenderer: class { constructor() { throw new Error('WebGL unavailable'); } },
}));

afterEach(cleanup);

it('keeps real work facts and a working return control when WebGL is unavailable', () => {
  const onReturn = vi.fn();
  render(<WorkRestScene title="Actual work" requirementVersion={3} taskCount={2}
    artifactCount={0} onReturn={onReturn} />);
  expect(screen.getByRole('heading', { name: 'Actual work' })).toBeTruthy();
  expect(screen.getByText('v3')).toBeTruthy();
  expect(screen.getByText('Artifacts').nextElementSibling?.textContent).toBe('0');
  expect(screen.getByRole('status').textContent).toContain('3D is unavailable');
  fireEvent.click(screen.getByRole('button', { name: 'Return to workspace' }));
  expect(onReturn).toHaveBeenCalledOnce();
});

it('shows only user-linked actual agents and opens their facts when WebGL is unavailable', () => {
  const onAgentSelect = vi.fn();
  const onManageAgents = vi.fn();
  render(<WorkRestScene title="Actual work" requirementVersion={3} taskCount={2}
    artifactCount={0} onReturn={vi.fn()} onAgentSelect={onAgentSelect}
    onManageAgents={onManageAgents} agents={[
      { agent_id: 'agent-123456', detected_identity: 'CODEX', lifecycle: 'RUNNING',
        uncertainty: false, association: 'USER_LINKED', activity: 'UNKNOWN', evidence_refs: [] },
      { agent_id: 'agent-987654', detected_identity: 'CODEX', lifecycle: 'RUNNING',
        uncertainty: true, association: 'USER_LINKED', presence: 'NOT_CURRENT', evidence_refs: [] },
      { agent_id: 'agent-112233', detected_identity: 'KIMI_CODE', lifecycle: 'UNKNOWN',
        uncertainty: true, association: 'USER_LINKED', presence: 'STALE', evidence_refs: [] },
      { agent_id: 'agent-unlinked', detected_identity: 'OTHER', lifecycle: 'RUNNING',
        uncertainty: false, association: null, evidence_refs: [] },
    ]} />);
  expect(screen.getByText('Linked agents').nextElementSibling?.textContent).toBe('3');
  expect(screen.queryByText(/OTHER/)).toBeNull();
  expect(screen.getByRole('button', { name: /CODEX 123456.*Activity unknown/ }).textContent).toContain('CODEX · 123456');
  expect(screen.getByRole('button', { name: /CODEX 987654.*Not observed now.*Activity unknown/ }).textContent).toContain('CODEX · 987654');
  expect(screen.getByRole('button', { name: /KIMI_CODE 112233.*Observation expired.*Activity unknown/ }).className).toContain('is-not-current');
  fireEvent.click(screen.getByRole('button', { name: /CODEX 123456.*Activity unknown/ }));
  expect(onAgentSelect).toHaveBeenCalledWith('agent-123456');
  fireEvent.click(screen.getByRole('button', { name: 'Manage links' }));
  expect(onManageAgents).toHaveBeenCalledOnce();
});

it('provides a real-agent linking entry instead of decorative bubbles for an empty work', () => {
  const onManageAgents = vi.fn();
  render(<WorkRestScene title="Actual work" requirementVersion={1} taskCount={0}
    artifactCount={0} onReturn={vi.fn()} onManageAgents={onManageAgents} />);
  expect(screen.getByText('No linked agents')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Link an actual agent' }));
  expect(onManageAgents).toHaveBeenCalledOnce();
});

it('reveals explicit task-agent links on demand and opens the selected 2D task', () => {
  const onTaskSelect = vi.fn();
  render(<WorkRestScene title="Actual work" requirementVersion={1} taskCount={2}
    artifactCount={0} onReturn={vi.fn()} onTaskSelect={onTaskSelect}
    agents={[{ agent_id: 'agent-123456', detected_identity: 'CODEX', lifecycle: 'RUNNING',
      uncertainty: false, association: 'USER_LINKED', evidence_refs: [] }]}
    tasks={[
      { id: 'task-one', title: 'Task one', assignee_agent_ids: ['agent-123456'] },
      { id: 'task-two', title: 'CODEX appears in task title' },
    ]} />);
  expect(screen.queryByText('Task one')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Task links' }));
  expect(screen.getByText('Task one')).toBeTruthy();
  expect(screen.getByText('Task one').closest('button')?.querySelector('.work-rest-task-assignees')?.textContent)
    .toBe('CODEX · 123456');
  expect(screen.getByText('No linked agent')).toBeTruthy();
  fireEvent.click(screen.getByText('Task one').closest('button')!);
  expect(onTaskSelect).toHaveBeenCalledWith('task-one');
  fireEvent.click(screen.getByRole('button', { name: 'Task links' }));
  expect(screen.queryByText('Task one')).toBeNull();
});

it('scales projected labels with the object while keeping a readable lower bound', () => {
  expect(projectedLabelScale(52, 52)).toBe(1);
  expect(projectedLabelScale(26, 52)).toBe(.5);
  expect(projectedLabelScale(13, 52)).toBe(.25);
  expect(projectedLabelScale(84, 52)).toBe(1.4);
});
