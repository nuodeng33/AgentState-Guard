import { fireEvent, render, screen } from '@testing-library/react';
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
  it('scopes partial observability instead of implying all detected Agents failed', () => {
    const degraded: AgentsView = {
      ...WITH_DETECTED_AGENT,
      status: 'DEGRADED',
      reason_code: 'R4_AGENT_DISCOVERY_PARTIAL',
      identified_count: 1,
      degradation_scopes: [
        {
          execution_domain_id: 'wsl-distro-ubuntu',
          label: 'Ubuntu',
          reason_code: 'NO_BOUNDED_HOST_READ',
        },
      ],
    };

    render(<AgentsViewBody data={degraded} />);

    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('Identified 1 Agent');
    expect(alert.textContent).toContain('Partially unobservable domains: Ubuntu');
    expect(alert.textContent).not.toContain('Agents view degraded');
  });

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

  it('keeps the machine reason_code tucked into collapsed secondary diagnostics', () => {
    render(<AgentsViewBody data={WITH_UNKNOWN_AGENT} />);
    expect(screen.queryByText('R4_AGENTS_AVAILABLE')).toBeNull();
    expect(screen.queryByText('AGENT_STATE_UNCERTAIN')).toBeNull();
    // The per-item reason_code is never hidden; it lives behind the
    // collapsed evidence toggle as secondary diagnostics.
    fireEvent.click(screen.getAllByRole('button', { name: /Evidence/ })[0]);
    expect(screen.getByText('AGENT_STATE_UNCERTAIN')).toBeTruthy();
    expect(screen.getByText('evt-agent-1')).toBeTruthy();
  });

  it('distinguishes same-product instances by bounded instance label', () => {
    const two: AgentsView = {
      ...WITH_DETECTED_AGENT,
      items: [
        { ...WITH_DETECTED_AGENT.items[0], detected_identity: 'CODEX', instance_label: 'CODEX 4f2a91', evidence_refs: ['e1'] },
        { ...WITH_DETECTED_AGENT.items[0], detected_identity: 'CODEX', instance_label: 'CODEX 8c10bd', evidence_refs: ['e2'] },
      ],
    };
    render(<AgentsViewBody data={two} />);
    expect(screen.getByText('Codex · 4f2a91')).toBeTruthy();
    expect(screen.getByText('Codex · 8c10bd')).toBeTruthy();
    expect(screen.queryByText('Agent 1')).toBeNull();
    expect(screen.queryByText('Agent 2')).toBeNull();
    expect(screen.getByText('4f2a91')).toBeTruthy();
    expect(screen.getByText('8c10bd')).toBeTruthy();
  });

  it('renders DETECTED verbatim without inflating it to a stronger state', () => {
    render(<AgentsViewBody data={WITH_DETECTED_AGENT} />);
    expect(screen.getByText('DETECTED')).toBeTruthy();
    expect(screen.queryByText('RUNNING')).toBeNull();
    expect(screen.queryByText('INTEGRATED')).toBeNull();
    expect(screen.getByText('0.95')).toBeTruthy();
  });

  it('renders activity only from backend evidence and does not infer it from RUNNING', () => {
    const withActivity: AgentsView = {
      ...WITH_DETECTED_AGENT,
      items: [
        {
          ...WITH_DETECTED_AGENT.items[0],
          lifecycle: 'RUNNING',
          activity_observability: 'OBSERVABLE',
          recent_activity_count: 1,
          activity_reason_code: 'HOST_ACTIVITY_OBSERVED',
          latest_activity: {
            event_id: 'host-process-1',
            timestamp: '2026-08-26T02:00:00Z',
            observed_at: '2026-08-26T02:00:00Z',
            recorded_at: '2026-08-26T02:00:00Z',
            actor: 'host-native-observer',
            subject: 'codex-process',
            type: 'PROCESS_STARTED',
            result: 'OBSERVED',
            affected_objects: [],
            checkpoint_id: null,
            change_id: null,
            supervision_session_id: null,
            verification_summary: null,
            reason_code: 'AGENT_CHILD_PROCESS_STARTED',
            execution_domain_id: 'windows-current',
            attribution: null,
            change_kind: null,
            coverage_after: null,
            coverage_before: null,
            recovery_disposition: null,
            workspace_id: null,
            evidence_refs: ['host-process-1'],
          },
          recent_verified_activities: [],
        },
      ],
    };

    render(<AgentsViewBody data={withActivity} />);
    expect(screen.getByText('OBSERVABLE')).toBeTruthy();
    expect(screen.getByText(/PROCESS_STARTED/)).toBeTruthy();
  });
});
