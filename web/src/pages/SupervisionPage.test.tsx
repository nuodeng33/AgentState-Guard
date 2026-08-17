import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { SupervisionItem, SupervisionView } from '../api/types';
import { SupervisionViewBody } from './SupervisionPage';

function session(partial: Partial<SupervisionItem>): SupervisionItem {
  return {
    supervision_session_id: 'ssn-unknown',
    status: 'EVALUATED',
    policy_decision: null,
    manual_approval: false,
    requires_manual_approval: false,
    requires_checkpoint: false,
    ai_assessment: null,
    recovery_facts: null,
    action_ref: null,
    evidence_refs: [],
    ...partial,
  };
}

const VIEW: SupervisionView = {
  schema_version: 'r4-p8-1',
  view: 'supervision',
  status: 'AVAILABLE',
  reason_code: 'R4_SUPERVISION_AVAILABLE',
  evidence_refs: ['evt-1', 'evt-2', 'evt-3'],
  items: [
    session({
      supervision_session_id: 'ssn-block',
      status: 'REJECTED',
      policy_decision: 'BLOCK',
      evidence_refs: ['evt-1'],
    }),
    session({
      supervision_session_id: 'ssn-review',
      status: 'AWAITING_APPROVAL',
      policy_decision: 'REVIEW',
      requires_manual_approval: true,
      requires_checkpoint: true,
      ai_assessment: { decision: 'REVIEW', severity: 'high' },
      recovery_facts: {
        recovery_level: 'R2',
        r1_verified: true,
        r2_verified: true,
        r3_verified: false,
        test_restore_status: 'VERIFIED_R2',
        trusted_baseline_status: 'NONE',
        reason_code: 'RECOVERY_COVERAGE_COMPLETE',
      },
      evidence_refs: ['evt-2'],
    }),
    session({
      supervision_session_id: 'ssn-unknown',
      status: 'PENDING',
      policy_decision: 'UNKNOWN',
      evidence_refs: ['evt-3'],
    }),
  ],
};

describe('SupervisionPage policy display', () => {
  it('renders BLOCK, REVIEW and UNKNOWN with visually distinct, non-safe UNKNOWN', () => {
    const { container } = render(<SupervisionViewBody data={VIEW} />);

    const block = screen.getByText('BLOCK');
    const review = screen.getAllByText('REVIEW')[0];
    const unknown = screen.getByText('UNKNOWN');

    expect(block.className).toContain('badge-bad');
    expect(review.className).toContain('badge-warn');
    expect(unknown.className).toContain('badge-unknown');
    // UNKNOWN must never read as safe.
    expect(unknown.className).not.toContain('badge-ok');
    expect(unknown.className).not.toContain('badge-info');

    expect(container.textContent).toContain('ssn-block');
    expect(container.textContent).toContain('ssn-review');
    expect(container.textContent).toContain('ssn-unknown');
  });

  it('shows approval flags, AI assessment and authoritative recovery facts', () => {
    render(<SupervisionViewBody data={VIEW} />);
    expect(screen.getAllByText('Requires manual approval').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Requires checkpoint').length).toBeGreaterThan(0);
    expect(screen.getAllByText('AI assessment').length).toBeGreaterThan(0);
    expect(screen.getByText('Authoritative recovery facts')).toBeTruthy();
    expect(screen.getByText('VERIFIED_R2')).toBeTruthy();
  });

  it('renders missing approval/checkpoint and recovery booleans as unknown, never No', () => {
    const unknown = {
      ...session({ supervision_session_id: 'ssn-missing-facts' }),
      pending_approval: undefined,
      requires_manual_approval: undefined,
      manual_approval: undefined,
      requires_checkpoint: undefined,
      recovery_facts: {
        reason_code: 'RECOVERY_FACTS_PARTIAL',
        r1_verified: null,
        r2_verified: undefined,
        r3_verified: null,
      },
    } as unknown as SupervisionItem;
    const view: SupervisionView = { ...VIEW, items: [unknown] };

    render(<SupervisionViewBody data={view} />);

    for (const label of [
      'Pending approval',
      'Requires manual approval',
      'Manual approval granted',
      'Requires checkpoint',
      'R1 verified',
      'R2 verified',
      'R3 verified',
    ]) {
      const row = screen.getByText(label).closest('.kv-row');
      expect(row?.textContent).toContain('—');
      expect(row?.textContent).not.toContain('No');
    }
  });


  it('states the action boundary honestly', () => {
    render(<SupervisionViewBody data={VIEW} />);
    // Sessions without a server-issued action_ref expose no executable action.
    expect(screen.getByText(/stays read-only/)).toBeTruthy();
    expect(screen.queryByText('Approve Once')).toBeNull();
    expect(screen.queryByText('Reject')).toBeNull();
  });
});

describe('SupervisionPage observed_agents projection', () => {
  it('renders backend observed_agents verbatim with no invented lifecycle', () => {
    const view: SupervisionView = {
      ...VIEW,
      observed_agents: [
        {
          detected_identity: 'claude-code',
          role: 'detected',
          lifecycle: 'DETECTED',
          confidence: 0.5,
          execution_domain_id: 'edge-1',
          workspace: { status: 'BOUND', binding_ref: 'ws-1' },
          reason_code: 'AGENT_DETECTED',
          uncertainty: false,
          evidence_refs: ['evt-a-1'],
        },
      ],
    };
    render(<SupervisionViewBody data={view} />);
    expect(screen.getByText('Observed agents (backend projection)')).toBeTruthy();
    expect(screen.getByText('claude-code')).toBeTruthy();
    expect(screen.getByText('DETECTED')).toBeTruthy();
    expect(screen.getByText('AGENT_DETECTED')).toBeTruthy();
  });
});

describe('SupervisionPage evidence wiring', () => {
  function json(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const SUPERVISION_WITH_ACTIVITY: SupervisionView = {
    schema_version: 'r4-p8-1',
    view: 'supervision',
    status: 'AVAILABLE',
    reason_code: 'R4_SUPERVISION_AVAILABLE',
    evidence_refs: ['evt-c-1'],
    items: [
      {
        ...VIEW.items[1],
        supervision_session_id: 'ssn-evidence',
        recent_verified_activities: [
          {
            event_id: 'evt-c-1',
            timestamp: '2026-08-15T06:00:00Z',
            observed_at: '2026-08-15T06:00:00Z',
            recorded_at: '2026-08-15T06:00:00Z',
            actor: 'core',
            subject: null,
            type: 'CHECKPOINT_CREATED',
            result: 'CREATED',
            affected_objects: [],
            checkpoint_id: 'checkpoint-3',
            execution_domain_id: null,
            attribution: null,
            change_kind: null,
            coverage_before: null,
            coverage_after: null,
            recovery_disposition: null,
            workspace_id: null,
            change_id: null,
            supervision_session_id: 'ssn-evidence',
            verification_summary: null,
            reason_code: 'CHECKPOINT_CREATED',
            evidence_refs: ['evt-c-1'],
          },
        ],
        latest_checkpoint: {
          checkpoint_id: 'checkpoint-3',
          reason_code: 'CHECKPOINT_CREATED',
          evidence_refs: ['evt-c-1'],
        },
      },
    ],
  };

  const EVIDENCE: import('../api/types').EvidenceDetail = {
    schema_version: 'r4-product-evidence-1',
    status: 'AVAILABLE',
    reason_code: 'CHECKPOINT_CREATED',
    event_id: 'evt-c-1',
    event_type: 'CHECKPOINT_CREATED',
    observed_at: '2026-08-15T06:00:00Z',
    recorded_at: '2026-08-15T06:00:00Z',
    source: 'core',
    subject: 'checkpoint-3',
    result: 'CREATED',
    verification_summary: null,
    checkpoint_id: 'checkpoint-3',
    change_id: null,
    chain_ref: 'e'.repeat(64),
    sanitized_detail: { affected_objects: [] },
    related_evidence_refs: [],
  };

  it('opens evidence from an activity event_id only; refetch supervision after mutations', async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path === '/api/session') return json({ token: 'tok' });
      if (path === '/api/v1/supervision') return json(SUPERVISION_WITH_ACTIVITY);
      if (path === '/api/v1/evidence/evt-c-1') return json(EVIDENCE);
      return json({}, 404);
    });
    vi.stubGlobal('fetch', fetchImpl);
    const { createApiClient } = await import('../api/client');
    const client = createApiClient(fetchImpl as unknown as typeof fetch);
    const { default: SupervisionPage } = await import('./SupervisionPage');

    render(<SupervisionPage client={client} />);
    const btn = await screen.findByRole('button', { name: 'Evidence' });
    fireEvent.click(btn);
    expect(await screen.findByText('e'.repeat(64))).toBeTruthy();
    expect(calls).toContain('/api/v1/evidence/evt-c-1');
    expect(calls.filter((p) => p === '/api/v1/supervision').length).toBeGreaterThanOrEqual(1);
  });
});
