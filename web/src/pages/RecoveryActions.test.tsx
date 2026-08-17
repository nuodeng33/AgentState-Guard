/**
 * Recovery action wiring tests. The fetch mock verifies the exact frozen
 * request bodies ({}, {}, {confirm:true}); the view is always re-read after
 * a mutation; failures surface only the stable backend reason_code.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { createApiClient, type ApiClient } from '../api/client';
import RecoveryPage from './RecoveryPage';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const BASE_VIEW = {
  schema_version: 'r4-p8-1',
  view: 'recovery',
  status: 'AVAILABLE',
  reason_code: 'R4_RECOVERY_AVAILABLE',
  evidence_refs: [],
  items: [] as Record<string, unknown>[],
  recovery_level: 'R0',
  r1_verified: false,
  r2_verified: false,
  r3_verified: false,
  test_restore_status: 'NOT_RUN_P6',
  trusted_baseline_status: 'NONE',
  trusted_baseline_id: null,
  checkpoint_count: 0,
  latest_checkpoint: null as Record<string, unknown> | null,
  actual_restore_status: 'NOT_RUN',
  recovery_verified: false,
  verified_at: null,
  scope_kind: 'PRODUCT_CONFIG',
  workspace_id: null,
  coverage: null,
  capabilities: { create_checkpoint: true, test_restore: true, restore: true },
  limitations: ['PRODUCT_CONFIG_TARGET_ONLY'],
};

const GOOD_ITEM = {
  checkpoint_id: '42',
  execution_domain_id: 'self-runtime',
  status: 'COMPLETE',
  reason_code: 'RECOVERY_COVERAGE_COMPLETE',
  requested_targets: 1,
  authorized_snapshot_targets: 1,
  intact_manifest_blob_targets: 1,
  authorized_snapshot_coverage: 1,
  manifest_blob_coverage: 1,
  test_restore_verified_targets: 1,
  test_restore_status: 'VERIFIED_R2',
  recovery_level: 'R2',
  r1_verified: true,
  r2_verified: true,
  r3_verified: false,
  trusted_baseline_status: 'NONE',
  trusted_baseline_id: null,
  evidence_refs: [],
  actual_restore_status: 'NOT_RUN',
  actual_restore_verified_at: null,
  actual_restore_evidence_refs: [],
  scope_kind: 'PRODUCT_CONFIG',
  workspace_id: null,
  coverage: null,
  created_at: '2026-08-16T00:00:00Z',
};

function withItem(item: Record<string, unknown>): typeof BASE_VIEW {
  return { ...BASE_VIEW, items: [item], checkpoint_count: 1, latest_checkpoint: item };
}

interface Recorded {
  path: string;
  method: string;
  body: unknown;
}

const CREATED = {
  schema_version: 'product-recovery-action-1',
  status: 'AVAILABLE',
  reason_code: 'RECOVERY_SNAPSHOT_CREATED',
  checkpoint_id: '43',
  manifest_digest: 'ab12',
  verified_targets: 1,
  evidence_refs: ['evt-9'],
  scope_kind: 'HOST_WORKSPACE',
  workspace_id: 'workspace-7',
  coverage: { restorable: 3, audit_only: 2, excluded: 1, unreachable: 4 },
  coverage_reason_counts: { WORKSPACE_AUDIT_ONLY: 2, WORKSPACE_PATH_EXCLUDED: 1 },
  scan_complete: false,
  scan_reason_code: 'WORKSPACE_SCAN_PARTIAL',
  post_restore_status: null,
  quarantined_targets: null,
  residue_targets: null,
};

function buildClient(viewPayload: () => object, action: { body: object; status?: number }) {
  const calls: Recorded[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === '/api/session') {
      calls.push({ path, method: 'GET', body: null });
      return json({ token: 't' });
    }
    if (path === '/api/v1/recovery' && init?.method !== 'POST') {
      calls.push({ path, method: 'GET', body: null });
      return json(viewPayload());
    }
    if (path.startsWith('/api/v1/recovery') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body ?? '{}')) as unknown;
      calls.push({ path, method: 'POST', body });
      return json(action.body, action.status ?? 200);
    }
    return json({ error: 'unexpected' }, 500);
  });
  const client = createApiClient(fetchImpl as unknown as typeof fetch);
  return { client: client as ApiClient, calls };
}

describe('Recovery actions', () => {
  it('Create Checkpoint posts exactly {} then re-reads the projection', async () => {
    const { client, calls } = buildClient(() => BASE_VIEW, { body: CREATED });
    render(<RecoveryPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create Checkpoint' }));
    const posts = () => calls.filter((c) => c.method === 'POST');
    await waitFor(() => expect(posts().length).toBe(1));
    expect(posts()[0].path).toBe('/api/v1/recovery/checkpoints');
    expect(posts()[0].body).toEqual({});
    // created ≠ recoverable
    expect((await screen.findByText(/43/)).textContent).toContain('≠ recoverable');
    expect(calls.filter((c) => c.method === 'GET' && c.path === '/api/v1/recovery').length).toBeGreaterThanOrEqual(2);
    expect(await screen.findByText('HOST_WORKSPACE')).toBeTruthy();
    expect(screen.getByText('workspace-7')).toBeTruthy();
    expect(screen.getByText('coverage.restorable').closest('.kv-row')?.textContent).toContain('3');
    expect(screen.getByText('coverage.unreachable').closest('.kv-row')?.textContent).toContain('4');
    expect(screen.getByText('WORKSPACE_SCAN_PARTIAL')).toBeTruthy();
  });

  it('Test Restore posts exactly {} to /{id}/test and shows the receipt', async () => {
    const { client, calls } = buildClient(() => withItem(GOOD_ITEM), {
      body: { ...CREATED, reason_code: 'TEST_RESTORE_VERIFIED', verified_targets: 2 },
    });
    render(<RecoveryPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Test Restore' }));
    const posts = () => calls.filter((c) => c.method === 'POST');
    await waitFor(() => expect(posts().length).toBe(1));
    expect(posts()[0].path).toBe('/api/v1/recovery/42/test');
    expect(posts()[0].body).toEqual({});
    await waitFor(() =>
      expect(screen.getAllByRole('status').some((el) => el.textContent?.includes('TEST_RESTORE_VERIFIED'))).toBe(true),
    );
  });

  it('Restore sends {confirm:true} only after typing the confirmation word', async () => {
    const { client, calls } = buildClient(() => withItem(GOOD_ITEM), {
      body: {
        ...CREATED,
        reason_code: 'RECOVERY_RESTORED_AND_VERIFIED',
        post_restore_status: 'POST_RESTORE_VERIFIED',
        quarantined_targets: 2,
        residue_targets: 0,
      },
    });
    render(<RecoveryPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Restore' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(calls.filter((c) => c.method === 'POST').length).toBe(0);

    const submit = Array.from(dialog.querySelectorAll('button')).find(
      (b) => b.getAttribute('type') === 'submit',
    )!;
    expect(submit.hasAttribute('disabled')).toBe(true);

    fireEvent.change(screen.getByLabelText(/to confirm/), { target: { value: 'Yes' } });
    expect(submit.hasAttribute('disabled')).toBe(false);
    fireEvent.submit(dialog.querySelector('form')!);

    const posts = () => calls.filter((c) => c.method === 'POST');
    await waitFor(() => expect(posts().length).toBe(1));
    expect(posts()[0].path).toBe('/api/v1/recovery/42/restore');
    expect(posts()[0].body).toEqual({ confirm: true });
    await waitFor(() => expect(screen.getByText('POST_RESTORE_VERIFIED')).toBeTruthy());
    expect(screen.getByText('quarantined_targets').closest('.kv-row')?.textContent).toContain('2');
    expect(screen.getByText('residue_targets').closest('.kv-row')?.textContent).toContain('0');
  });

  it('failed action surfaces only the stable backend reason_code', async () => {
    const { client } = buildClient(() => withItem(GOOD_ITEM), {
      body: { reason_code: 'RECOVERY_DISCOVERY_UNAVAILABLE' },
      status: 503,
    });
    render(<RecoveryPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Test Restore' }));
    const hit = await screen.findByText(/RECOVERY_DISCOVERY_UNAVAILABLE/);
    expect(hit.getAttribute('role')).toBe('alert');
    expect(hit.textContent).toContain('left unchanged');
    expect(hit.textContent).not.toMatch(/stack|Error:/);
  });

  it('fail-closed checkpoints keep mutation actions disabled', async () => {
    const broken = { ...GOOD_ITEM, recovery_level: 'R0', status: 'EVIDENCE_INSUFFICIENT' };
    const { client } = buildClient(() => withItem(broken), { body: CREATED });
    render(<RecoveryPage client={client} />);

    expect((await screen.findByRole('button', { name: 'Test Restore' })).hasAttribute('disabled')).toBe(true);
    expect(screen.getByRole('button', { name: 'Restore' }).hasAttribute('disabled')).toBe(true);
    // Create is still allowed: it is the way out of R0.
    expect(screen.getByRole('button', { name: 'Create Checkpoint' }).hasAttribute('disabled')).toBe(false);
  });
});
