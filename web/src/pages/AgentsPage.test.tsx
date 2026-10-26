import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { AgentsView } from '../api/types';
import { AgentsViewBody } from './AgentsPage';

const WITH_UNKNOWN_AGENT: AgentsView = {
  schema_version: 'r4-p8-1',
  view: 'agents',
  status: 'AVAILABLE',
  reason_code: 'R4_AGENTS_AVAILABLE',
  evidence_refs: ['evt-agent-1'],
  items: [
    {
      detected_identity: 'claude-code',
      role: 'detected',
      lifecycle: 'UNKNOWN',
      confidence: 0.4,
      execution_domain_id: null,
      workspace: { status: 'UNKNOWN', binding_ref: null },
      reason_code: 'AGENT_STATE_UNCERTAIN',
      uncertainty: true,
      evidence_refs: ['evt-agent-1'],
    },
  ],
};

const WITH_DETECTED_AGENT: AgentsView = {
  ...WITH_UNKNOWN_AGENT,
  items: [
    {
      detected_identity: 'cloudcli',
      role: 'assistant',
      lifecycle: 'DETECTED',
      confidence: 0.95,
      execution_domain_id: 'self-runtime',
      workspace: { status: 'UNKNOWN', binding_ref: null },
      reason_code: 'AGENT_DETECTED',
      uncertainty: false,
      evidence_refs: ['evt-agent-2'],
    },
  ],
};

describe('AgentsPage lifecycle honesty', () => {
  it('keeps UNKNOWN lifecycle as UNKNOWN and never upgrades by identity name', () => {
    render(<AgentsViewBody data={WITH_UNKNOWN_AGENT} />);
    expect(screen.getByText('Claude Code')).toBeTruthy();
    // lifecycle UNKNOWN and workspace UNKNOWN both stay UNKNOWN.
    expect(screen.getAllByText('UNKNOWN').length).toBeGreaterThan(0);
    expect(screen.queryByText('RUNNING')).toBeNull();
    expect(screen.queryByText('INTEGRATED')).toBeNull();
    expect(screen.queryByText('ENFORCED')).toBeNull();
    expect(screen.queryByText('ACTIVE')).toBeNull();
  });

  it('renders DETECTED verbatim without inflating it to a stronger state', () => {
    render(<AgentsViewBody data={WITH_DETECTED_AGENT} />);
    expect(screen.getByText('DETECTED')).toBeTruthy();
    expect(screen.queryByText('RUNNING')).toBeNull();
    expect(screen.queryByText('INTEGRATED')).toBeNull();
    expect(screen.getByText('0.95')).toBeTruthy();
  });

  it('shows source and stable instance identity with copyable observation details', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const agent = { ...WITH_DETECTED_AGENT.items[0], agent_id: 'external-agent-12345678',
      source_label: 'Local processes', source_short_id: 'abcdef123456',
      program_path: 'C:/Apps/CloudCLI/cloudcli.exe', pid: 441, cwd: 'C:/Work',
      observed_at: '2026-09-29T10:00:00Z' };
    render(<AgentsViewBody data={{ ...WITH_DETECTED_AGENT, items: [agent] }} />);
    expect(screen.getByText('cloudcli', { selector: '.agent-identity-label' })).toBeTruthy();
    fireEvent.click(screen.getByText('Identity and observation details'));
    expect(screen.getByText(agent.program_path)).toBeTruthy();
    expect(screen.getByText('441')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Copy ID' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(agent.agent_id));
  });
});
