import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

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
    expect(screen.getByText('claude-code')).toBeTruthy();
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
});
