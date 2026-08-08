import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { RecoveryItem, RecoveryView } from '../api/types';
import { RecoveryViewBody } from './RecoveryPage';

function item(partial: Partial<RecoveryItem>): RecoveryItem {
  return {
    checkpoint_id: 'cp-1',
    execution_domain_id: 'self-runtime',
    status: 'COMPLETE',
    reason_code: 'RECOVERY_COVERAGE_COMPLETE',
    requested_targets: 3,
    authorized_snapshot_targets: 3,
    intact_manifest_blob_targets: 3,
    authorized_snapshot_coverage: 1,
    manifest_blob_coverage: 1,
    test_restore_verified_targets: 3,
    test_restore_status: 'VERIFIED_R2',
    recovery_level: 'R3',
    r1_verified: true,
    r2_verified: true,
    r3_verified: true,
    trusted_baseline_status: 'NONE',
    trusted_baseline_id: null,
    evidence_refs: ['evt-cp-1'],
    ...partial,
  };
}

const R3_UNTRUSTED: RecoveryView = {
  schema_version: 'r4-p8-1',
  view: 'recovery',
  status: 'AVAILABLE',
  reason_code: 'R4_RECOVERY_AVAILABLE',
  evidence_refs: ['evt-cp-1'],
  items: [item({})],
  recovery_level: 'R3',
  r1_verified: true,
  r2_verified: true,
  r3_verified: true,
  test_restore_status: 'VERIFIED_R2',
  trusted_baseline_status: 'NONE',
  trusted_baseline_id: null,
};

const R0_FAIL_CLOSED: RecoveryView = {
  ...R3_UNTRUSTED,
  items: [
    item({
      checkpoint_id: 'cp-broken',
      status: 'EVIDENCE_INSUFFICIENT',
      reason_code: 'RECOVERY_ARTIFACT_NOT_FOUND',
      requested_targets: 0,
      authorized_snapshot_targets: 0,
      intact_manifest_blob_targets: 0,
      authorized_snapshot_coverage: null,
      manifest_blob_coverage: null,
      test_restore_verified_targets: null,
      test_restore_status: 'NOT_RUN_P6',
      recovery_level: 'R0',
      r1_verified: false,
      r2_verified: false,
      r3_verified: false,
    }),
  ],
  recovery_level: 'R0',
  r1_verified: false,
  r2_verified: false,
  r3_verified: false,
  test_restore_status: 'NOT_RUN_P6',
};

const DEGRADED: RecoveryView = {
  ...R3_UNTRUSTED,
  status: 'DEGRADED',
  reason_code: 'R4_DATABASE_UNREACHABLE',
  evidence_refs: [],
  items: [],
  recovery_level: 'R0',
  r1_verified: false,
  r2_verified: false,
  r3_verified: false,
  test_restore_status: 'NOT_RUN_P6',
  trusted_baseline_status: 'NONE',
  trusted_baseline_id: null,
};

describe('RecoveryPage R3 vs Trusted Baseline separation', () => {
  it('shows R3 in the chain while Trusted Baseline stays NONE', () => {
    const { container } = render(<RecoveryViewBody data={R3_UNTRUSTED} />);

    const current = container.querySelector('.chain-step.is-current');
    expect(current?.textContent).toContain('R3');
    expect(current?.textContent).toContain('verified');

    const baselinePanel = container.querySelector('.baseline-panel');
    expect(baselinePanel?.querySelector('.badge')?.textContent).toBe('NONE');
    expect(baselinePanel?.querySelector('.badge-ok')).toBeNull();
    // The only TRUSTED mention allowed is the explanatory note.
    expect(baselinePanel?.textContent).toContain('does not imply TRUSTED');
  });
});

describe('RecoveryPage R0 fail-closed display', () => {
  it('fails closed at view level and per checkpoint, never "recoverable"', () => {
    const { container } = render(<RecoveryViewBody data={R0_FAIL_CLOSED} />);
    const alerts = screen.getAllByRole('alert');
    expect(alerts.some((el) => el.textContent?.includes('fail closed'))).toBe(true);
    expect(alerts.some((el) => el.textContent?.includes('Do not treat it as restorable'))).toBe(
      true,
    );
    const current = container.querySelector('.chain-step.is-current');
    expect(current?.textContent).toContain('R0');
    expect(current?.className).toContain('tone-bad');
    expect(container.textContent).not.toContain('Recoverable');
  });

  it('fails closed when the backend is degraded', () => {
    render(<RecoveryViewBody data={DEGRADED} />);
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('Recovery view degraded — fail closed');
    expect(alert.textContent).toContain('R4_DATABASE_UNREACHABLE');
    expect(screen.getByText('DEGRADED')).toBeTruthy();
  });
});
