import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import WorkRestScene, { projectedLabelScale } from './WorkRestScene';

const rendererState = vi.hoisted(() => ({ available: false, renders: [] as { scene: any; camera: any }[], disposed: 0 }));
vi.mock('../vendor/three.module.js', async importOriginal => {
  const actual = await importOriginal<Record<string, any>>();
  return { ...actual,
    PMREMGenerator: class { fromEquirectangular() { return { texture: null, dispose() {} }; } dispose() {} },
    WebGLRenderer: class {
      domElement = document.createElement('canvas');
      constructor() { if (!rendererState.available) throw new Error('WebGL unavailable'); }
      getContext() { return {}; }
      setPixelRatio() {} setClearColor() {} setSize() {}
      render(scene: any, camera: any) { rendererState.renders.push({ scene, camera }); }
      dispose() { rendererState.disposed++; }
    },
  };
});
beforeEach(() => { rendererState.available = false; rendererState.renders = []; rendererState.disposed = 0; });

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


it('preserves camera, pause and task visibility when linked facts refresh', () => {
  rendererState.available = true;
  const props = { title: 'Scene work', requirementVersion: 1, taskCount: 1, artifactCount: 0,
    onReturn: vi.fn(), tasks: [{ id: 'task-one', title: 'Task one' }],
    agents: [{ agent_id: 'agent-123456', detected_identity: 'CODEX', lifecycle: 'RUNNING',
      uncertainty: false, association: 'USER_LINKED' as const, presence: 'CURRENT' as const, evidence_refs: [] }] };
  const { rerender, container } = render(<WorkRestScene {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Pause motion' }));
  fireEvent.click(screen.getByRole('button', { name: 'Task links' }));
  expect(container.querySelector('.work-rest-core-label')?.textContent).toContain('Scene work');
  const stage = screen.getByRole('group', { name: /3D scene:/ });
  fireEvent.keyDown(stage, { key: 'ArrowRight' });
  fireEvent.keyDown(stage, { key: '-' });
  const before = rendererState.renders[rendererState.renders.length - 1]!;
  const yaw = before.scene.children[0].rotation.y, distance = before.camera.position.z;
  expect(yaw).toBeCloseTo(.12); expect(distance).toBeGreaterThan(9.8);
  rerender(<WorkRestScene {...props} agents={[{ ...props.agents[0], presence: 'STALE' }]} />);
  const after = rendererState.renders[rendererState.renders.length - 1]!;
  expect(after.scene).not.toBe(before.scene);
  expect(after.scene.children[0].rotation.y).toBeCloseTo(yaw);
  expect(after.camera.position.z).toBeCloseTo(distance);
  expect(after.scene.children[0].children[after.scene.children[0].children.length - 1].visible).toBe(true);
  expect(screen.getByRole('button', { name: 'Resume motion' }).getAttribute('aria-pressed')).toBe('true');
  expect(rendererState.disposed).toBe(1);
});

it('updates selected film colors without rebuilding the scene', () => {
  rendererState.available = true;
  const props = { title: 'Scene work', requirementVersion: 1, taskCount: 0, artifactCount: 0, onReturn: vi.fn(),
    agents: [{ agent_id: 'agent-123456', detected_identity: 'CODEX', lifecycle: 'RUNNING',
      uncertainty: false, association: 'USER_LINKED' as const, evidence_refs: [] }] };
  const { rerender } = render(<WorkRestScene {...props} agentTints={{ 'agent-123456': '#7cc6bc' }} />);
  const before = rendererState.renders[rendererState.renders.length - 1]!.scene;
  rerender(<WorkRestScene {...props} agentTints={{ 'agent-123456': '#b9a6f4' }} />);
  const after = rendererState.renders[rendererState.renders.length - 1]!.scene;
  expect(after).toBe(before);
  const bubble = after.children[0].children.find((child: any) => child.material?.uniforms?.tint);
  expect(bubble.material.uniforms.tint.value.toArray()).toEqual([185 / 255, 166 / 255, 244 / 255]);
  expect(rendererState.disposed).toBe(0);
});

it('pauses for a live reduced-motion change and cleans up listeners', () => {
  rendererState.available = true;
  const original = window.matchMedia;
  const change = document.createElement('div');
  window.matchMedia = vi.fn(() => ({ matches: false, media: '', onchange: null,
    addEventListener: change.addEventListener.bind(change), removeEventListener: change.removeEventListener.bind(change),
    addListener() {}, removeListener() {}, dispatchEvent: change.dispatchEvent.bind(change),
  })) as typeof window.matchMedia;
  try {
    const { unmount } = render(<WorkRestScene title="Scene work" requirementVersion={1} taskCount={0} artifactCount={0} onReturn={vi.fn()} />);
    const event = new Event('change'); Object.assign(event, { matches: true });
    fireEvent(change, event);
    expect(screen.getByRole('button', { name: 'Resume motion' }).getAttribute('aria-pressed')).toBe('true');
    unmount();
    const count = rendererState.renders.length;
    fireEvent(change, event);
    expect(rendererState.renders).toHaveLength(count);
  } finally { window.matchMedia = original; }
});

it('retains every linked Agent in the readable overflow list', () => {
  render(<WorkRestScene title="Scene work" requirementVersion={1} taskCount={0} artifactCount={0} onReturn={vi.fn()}
    onAgentSelect={vi.fn()} agents={Array.from({ length: 6 }, (_, index) => ({ agent_id: `agent-${index}`,
      detected_identity: `Agent ${index}`, lifecycle: 'UNKNOWN', uncertainty: true,
      association: 'USER_LINKED' as const, evidence_refs: [] }))} />);
  expect(screen.getAllByRole('button', { name: /^Agent [0-5] / })).toHaveLength(6);
});


it('suspends animation while hidden and preserves a manual pause when foregrounded', () => {
  rendererState.available = true;
  let hidden = false;
  const originalHidden = Object.getOwnPropertyDescriptor(document, 'hidden');
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  const request = vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(91);
  const cancel = vi.spyOn(window, 'cancelAnimationFrame');
  try {
    render(<WorkRestScene title="Scene work" requirementVersion={1} taskCount={0} artifactCount={0} onReturn={vi.fn()} />);
    const scheduled = request.mock.calls.length;
    hidden = true; fireEvent(document, new Event('visibilitychange'));
    expect(cancel).toHaveBeenCalledWith(91);
    expect(request).toHaveBeenCalledTimes(scheduled);
    hidden = false; fireEvent(document, new Event('visibilitychange'));
    expect(request).toHaveBeenCalledTimes(scheduled + 1);
    fireEvent.click(screen.getByRole('button', { name: 'Pause motion' }));
    hidden = true; fireEvent(document, new Event('visibilitychange'));
    hidden = false; fireEvent(document, new Event('visibilitychange'));
    expect(request).toHaveBeenCalledTimes(scheduled + 1);
  } finally {
    cleanup(); request.mockRestore(); cancel.mockRestore();
    if (originalHidden) Object.defineProperty(document, 'hidden', originalHidden);
    else delete (document as unknown as { hidden?: boolean }).hidden;
  }
});
