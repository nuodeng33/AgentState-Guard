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
    actual_restore_status: 'NOT_RUN',
    actual_restore_verified_at: null,
    actual_restore_evidence_refs: [],
    scope_kind: 'PRODUCT_CONFIG',
    workspace_id: null,
    coverage: null,
    created_at: '2026-08-16T00:00:00Z',
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
  checkpoint_count: 1,
  latest_checkpoint: item({}),
  actual_restore_status: 'NOT_RUN',
  recovery_verified: false,
  verified_at: null,
  scope_kind: 'PRODUCT_CONFIG',
  workspace_id: null,
  coverage: null,
  capabilities: { create_checkpoint: true, test_restore: true, restore: true },
  limitations: ['PRODUCT_CONFIG_TARGET_ONLY'],
};

const R0_BROKEN_ITEM = item({
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
});

const R0_FAIL_CLOSED: RecoveryView = {
  ...R3_UNTRUSTED,
  items: [R0_BROKEN_ITEM],
  recovery_level: 'R0',
  r1_verified: false,
  r2_verified: false,
  r3_verified: false,
  test_restore_status: 'NOT_RUN_P6',
  latest_checkpoint: R0_BROKEN_ITEM,
  actual_restore_status: 'NOT_RUN',
  recovery_verified: false,
  verified_at: null,
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
  checkpoint_count: 0,
  latest_checkpoint: null,
  actual_restore_status: 'NOT_RUN',
  recovery_verified: false,
  verified_at: null,
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

describe('RecoveryPage authoritative scope and restore facts', () => {
  it('renders HOST_WORKSPACE coverage and keeps partial actual restore unverified', () => {
    const coverage = {
      counts: { restorable: 4, audit_only: 2, excluded: 1, unreachable: 3 },
      reason_counts: {
        WORKSPACE_AUDIT_ONLY: 2,
        WORKSPACE_PATH_EXCLUDED: 1,
        WORKSPACE_TARGET_UNREACHABLE: 3,
      },
      scan_complete: false,
      scan_reason_code: 'WORKSPACE_SCAN_PARTIAL',
    };
    const latest = item({
      checkpoint_id: 'cp-workspace',
      scope_kind: 'HOST_WORKSPACE',
      workspace_id: 'workspace-safe-id',
      coverage,
      actual_restore_status: 'PARTIAL',
      actual_restore_verified_at: null,
      actual_restore_evidence_refs: ['evt-restore-partial'],
    });
    const view: RecoveryView = {
      ...R3_UNTRUSTED,
      items: [latest],
      latest_checkpoint: latest,
      actual_restore_status: 'PARTIAL',
      recovery_verified: false,
      verified_at: null,
      scope_kind: 'HOST_WORKSPACE',
      workspace_id: 'workspace-safe-id',
      coverage,
      limitations: [
        'NOT_WHOLE_HOST_BACKUP',
        'COVERAGE_BASED_WORKSPACE_PROTECTION',
        'EXPLICIT_RESTORE_CONFIRMATION_REQUIRED',
      ],
    };

    const firstFact = (label: string) =>
      screen.getAllByText(label)[0].closest('.kv-row')?.textContent;
    render(<RecoveryViewBody data={view} />);

    expect(screen.getAllByText('HOST_WORKSPACE').length).toBeGreaterThan(0);
    expect(screen.getAllByText('workspace-safe-id').length).toBeGreaterThan(0);
    expect(screen.getAllByText('PARTIAL').length).toBeGreaterThan(0);
    expect(screen.getByText('recovery_verified').closest('.kv-row')?.textContent).toContain('No');
    expect(firstFact('coverage.restorable')).toContain('4');
    expect(firstFact('coverage.audit_only')).toContain('2');
    expect(firstFact('coverage.excluded')).toContain('1');
    expect(firstFact('coverage.unreachable')).toContain('3');
    expect(firstFact('coverage.scan_complete')).toContain('No');
    expect(screen.getAllByText('WORKSPACE_SCAN_PARTIAL').length).toBeGreaterThan(0);
    expect(firstFact('WORKSPACE_AUDIT_ONLY')).toContain('2');
    expect(firstFact('WORKSPACE_PATH_EXCLUDED')).toContain('1');
    expect(firstFact('WORKSPACE_TARGET_UNREACHABLE')).toContain('3');
    expect(screen.getByText('NOT_WHOLE_HOST_BACKUP')).toBeTruthy();
    expect(screen.getByText('COVERAGE_BASED_WORKSPACE_PROTECTION')).toBeTruthy();
    expect(screen.getAllByText('evt-restore-partial').length).toBeGreaterThan(0);
  });

  it('labels PRODUCT_CONFIG fallback explicitly beside checkpoint creation context', () => {
    render(<RecoveryViewBody data={R3_UNTRUSTED} />);

    expect(screen.getAllByText('PRODUCT_CONFIG').length).toBeGreaterThan(0);
    expect(screen.getByText('PRODUCT_CONFIG_TARGET_ONLY')).toBeTruthy();
    expect(screen.queryByText('HOST_WORKSPACE')).toBeNull();
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

  it('renders a real minimal degraded projection without inventing recovery facts', () => {
    const minimalDegraded: RecoveryView = {
      schema_version: 'r4-p8-1',
      view: 'recovery',
      status: 'DEGRADED',
      reason_code: 'R4_LEDGER_INVALID',
      evidence_refs: [],
      items: [],
    };

    render(<RecoveryViewBody data={minimalDegraded} />);

    expect(screen.getByRole('alert').textContent).toContain('R4_LEDGER_INVALID');
    expect(screen.getByText('scope_kind').closest('.kv-row')?.textContent).toContain('—');
    expect(screen.getByText('recovery_verified').closest('.kv-row')?.textContent).toContain('—');
    expect(document.querySelector('.chain-step.is-current')).toBeNull();
    expect(document.querySelector('.baseline-panel .badge')?.textContent).toBe('UNKNOWN');
  });

  it('shows an honest EMPTY state with R0 chain and NONE baseline', () => {
    const empty: RecoveryView = {
      ...DEGRADED,
      status: 'EMPTY',
      reason_code: 'R4_STATE_EMPTY',
    };
    const { container } = render(<RecoveryViewBody data={empty} />);
    expect(screen.getByText('No recovery checkpoints')).toBeTruthy();
    const current = container.querySelector('.chain-step.is-current');
    expect(current?.textContent).toContain('R0');
    expect(container.querySelector('.baseline-panel .badge')?.textContent).toBe('NONE');
    // EMPTY is not dressed up as an error, but R0 is still not "recoverable".
    expect(container.textContent).not.toContain('Recoverable');
  });
});
