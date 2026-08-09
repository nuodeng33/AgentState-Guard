import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

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

  it('states the action boundary honestly', () => {
    render(<SupervisionViewBody data={VIEW} />);
    // Sessions without a server-issued action_ref expose no executable action.
    expect(screen.getByText(/stays read-only/)).toBeTruthy();
    expect(screen.queryByText('Approve Once')).toBeNull();
    expect(screen.queryByText('Reject')).toBeNull();
  });
});
