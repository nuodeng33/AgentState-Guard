/**
 * Changes + Evidence vertical slice tests. All data comes from a scripted
 * fetch mirroring the frozen r4-p8-1 / r4-product-evidence-1 backend DTOs.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ChangesView, EvidenceDetail } from '../api/types';
import ChangesPage, { ChangesViewBody } from './ChangesPage';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ITEM_APPROVED = {
  event_id: 'evt-101',
  timestamp: '2026-08-15T05:00:02Z',
  observed_at: '2026-08-15T05:00:01Z',
  recorded_at: '2026-08-15T05:00:02Z',
  actor: 'desktop-operator',
  subject: 'checkpoint-9',
  type: 'USER_APPROVED',
  result: 'APPROVED',
  affected_objects: ['d'.repeat(64)],
  checkpoint_id: 'checkpoint-9',
  change_id: null,
  supervision_session_id: 'sess-20260815_1',
  policy_summary: null,
  approval_summary: 'APPROVED',
  verification_summary: null,
  reason_code: 'SUPERVISION_APPROVED',
  execution_domain_id: 'windows-current',
  attribution: 'UNATTRIBUTED',
  change_kind: 'MODIFIED',
  coverage_before: 'restorable',
  coverage_after: 'audit_only',
  recovery_disposition: 'AUDIT_ONLY',
  workspace_id: 'workspace-safe-id',
  evidence_refs: ['evt-101'],
};

const ITEM_DRIFT = {
  ...ITEM_APPROVED,
  event_id: 'evt-102',
  type: 'SCOPE_DRIFT',
  result: 'CONFIRMED',
  checkpoint_id: null,
  supervision_session_id: null,
  approval_summary: null,
  verification_summary: 'FAILED',
  reason_code: 'SCOPE_DRIFT',
  subject: null,
};

const AVAILABLE_VIEW: ChangesView = {
  schema_version: 'r4-p8-1',
  view: 'changes',
  status: 'AVAILABLE',
  reason_code: 'R4_CHANGES_AVAILABLE',
  evidence_refs: ['evt-101', 'evt-102'],
  items: [ITEM_APPROVED, ITEM_DRIFT],
};

const EMPTY_VIEW: ChangesView = {
  schema_version: 'r4-p8-1',
  view: 'changes',
  status: 'EMPTY',
  reason_code: 'R4_STATE_EMPTY',
  evidence_refs: [],
  items: [],
};

const DEGRADED_VIEW: ChangesView = {
  ...EMPTY_VIEW,
  status: 'DEGRADED',
  reason_code: 'R4_LEDGER_INVALID',
};

const EVIDENCE_AVAILABLE: EvidenceDetail = {
  schema_version: 'r4-product-evidence-1',
  status: 'AVAILABLE',
  reason_code: 'SUPERVISION_APPROVED',
  event_id: 'evt-101',
  event_type: 'USER_APPROVED',
  observed_at: '2026-08-15T05:00:01Z',
  recorded_at: '2026-08-15T05:00:02Z',
  source: 'core',
  subject: 'checkpoint-9',
  result: 'APPROVED',
  verification_summary: null,
  checkpoint_id: 'checkpoint-9',
  change_id: null,
  execution_domain_id: 'windows-current',
  chain_ref: 'c'.repeat(64),
  sanitized_detail: {
    affected_objects: ['d'.repeat(64)],
    attribution: 'UNATTRIBUTED',
    change_kind: 'MODIFIED',
    coverage_before: 'restorable',
    coverage_after: 'audit_only',
    recovery_disposition: 'AUDIT_ONLY',
    workspace_id: 'workspace-safe-id',
  },
  related_evidence_refs: ['evt-099'],
};

function changesFetch(bodyByPath: Record<string, unknown>): (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> {
  return (input: RequestInfo | URL, _init?: RequestInit) => {
    const path = String(input);
    if (path === '/api/session') return Promise.resolve(json({ token: 'tok' }));
    if (path in bodyByPath) {
      const body = bodyByPath[path];
      if (body instanceof Response) return Promise.resolve(body);
      return Promise.resolve(json(body));
    }
    return Promise.resolve(new Response('not found', { status: 404 }));
  };
}

describe('ChangesViewBody bounded states', () => {
  it('EMPTY renders the honest empty state with backend reason_code', () => {
    render(<ChangesViewBody data={EMPTY_VIEW} />);
    expect(screen.getByText('No verified activity')).toBeTruthy();
    expect(screen.getAllByText('R4_STATE_EMPTY').length).toBeGreaterThan(0);
  });

  it('DEGRADED fails closed, never implying healthy data', () => {
    render(<ChangesViewBody data={DEGRADED_VIEW} />);
    expect(screen.getByText('R4_LEDGER_INVALID')).toBeTruthy();
    expect(screen.queryByText('evt-101')).toBeNull();
    expect(screen.queryByText('USER_APPROVED')).toBeNull();
  });
});

describe('ChangesPage real wiring', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(changesFetch({ '/api/v1/changes': AVAILABLE_VIEW })));
  });

  it('renders item fields verbatim, tokens preserved, no aggregate verdict', async () => {
    render(<ChangesPage />);
    expect((await screen.findAllByText('USER_APPROVED')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('SCOPE_DRIFT')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('desktop-operator')[0]).toBeTruthy();
    expect(screen.getByText('sess-20260815_1')).toBeTruthy();
    expect(screen.getAllByText('checkpoint-9')[0]).toBeTruthy();
    expect(screen.getAllByText('SUPERVISION_APPROVED')[0]).toBeTruthy();
    expect(screen.getAllByText('APPROVED')[0]).toBeTruthy();
    // affected_objects are bounded digests, rendered verbatim
    expect(screen.getAllByText('d'.repeat(64)).length).toBeGreaterThan(0);
    // view-level status stays the backend authority token; the machine
    // reason_code moves to secondary diagnostics (collapsed evidence).
    expect(screen.getAllByText('AVAILABLE').length).toBeGreaterThan(0);
    expect(screen.queryByText('R4_CHANGES_AVAILABLE')).toBeNull();
    expect(screen.getAllByText('windows-current').length).toBeGreaterThan(0);
    expect(screen.getAllByText('UNATTRIBUTED').length).toBeGreaterThan(0);
    expect(screen.getAllByText('MODIFIED').length).toBeGreaterThan(0);
    expect(screen.getAllByText('restorable').length).toBeGreaterThan(0);
    expect(screen.getAllByText('audit_only').length).toBeGreaterThan(0);
    expect(screen.getAllByText('AUDIT_ONLY').length).toBeGreaterThan(0);
    expect(screen.getAllByText('workspace-safe-id').length).toBeGreaterThan(0);
    expect(screen.getByText('AVAILABLE')).toBeTruthy();
  });

  it('opens evidence by canonical event_id and renders sanitized detail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(changesFetch({
        '/api/v1/changes': AVAILABLE_VIEW,
        '/api/v1/evidence/evt-101': EVIDENCE_AVAILABLE,
      })),
    );
    render(<ChangesPage />);
    await screen.findAllByText('USER_APPROVED');

    fireEvent.click(screen.getAllByRole('button', { name: 'Evidence' })[0]);

    expect((await screen.findAllByText('evt-101')).length).toBeGreaterThan(0);
    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map(([i]) => String(i));
    expect(calls).toContain('/api/v1/evidence/evt-101');
    expect(calls).not.toContain('/api/v1/evidence/evt-099'); // related refs never dereferenced
    // sanitized chain + detail render
    expect(screen.getAllByText('c'.repeat(64)).length).toBeGreaterThan(0);
    expect(screen.getByText('core')).toBeTruthy();
  });

  it('NOT_FOUND: shows honest DTO, item facts stay in place', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(changesFetch({
        '/api/v1/changes': AVAILABLE_VIEW,
        '/api/v1/evidence/evt-101': json(
          { schema_version: 'r4-product-evidence-1', status: 'NOT_FOUND', reason_code: 'EVIDENCE_EVENT_NOT_FOUND', event_id: 'evt-101' },
          404,
        ),
      })),
    );
    render(<ChangesPage />);
    await screen.findAllByText('USER_APPROVED');

    fireEvent.click(screen.getAllByRole('button', { name: 'Evidence' })[0]);
    expect((await screen.findAllByText('EVIDENCE_EVENT_NOT_FOUND')).length).toBeGreaterThan(0);
    // the activity that generated the lookup remains true and visible
    expect(screen.getAllByText('USER_APPROVED')[0]).toBeTruthy();
    expect(screen.getAllByText('desktop-operator')[0]).toBeTruthy();
  });

  it('evidence 5xx reports unavailability without fabricating detail, item facts unchanged', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(changesFetch({
        '/api/v1/changes': AVAILABLE_VIEW,
        '/api/v1/evidence/evt-101': json(
          { schema_version: 'r4-product-evidence-1', status: 'DEGRADED', reason_code: 'EVIDENCE_DETAIL_UNAVAILABLE', event_id: 'evt-101' },
          503,
        ),
      })),
    );
    render(<ChangesPage />);
    await screen.findAllByText('USER_APPROVED');

    fireEvent.click(screen.getAllByRole('button', { name: 'Evidence' })[0]);
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.some((el) => el.textContent?.includes('API request failed (HTTP 503)'))).toBe(true);
    // No fabricated AVAILABLE content; the changes card stays true
    expect(screen.queryByText('c'.repeat(64))).toBeNull();
    expect(screen.getAllByText('USER_APPROVED')[0]).toBeTruthy();
  });

  it('session failure on evidence is honest and does not alter the view', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const path = String(input);
        if (path === '/api/session') return Promise.resolve(json({ token: 'tok' }));
        if (path === '/api/v1/changes') return Promise.resolve(json(AVAILABLE_VIEW));
        if (path === '/api/v1/evidence/evt-101') return Promise.resolve(json({}, 401));
        return Promise.resolve(new Response('nf', { status: 404 }));
      }),
    );
    render(<ChangesPage />);
    await screen.findAllByText('USER_APPROVED');

    fireEvent.click(screen.getAllByRole('button', { name: 'Evidence' })[0]);
    expect(await screen.findByText('SESSION_UNAVAILABLE')).toBeTruthy();
    expect(screen.getAllByText('USER_APPROVED')[0]).toBeTruthy();
  });

  it('never renders raw ledger payloads: no payload keys leak into the UI', async () => {
    render(<ChangesPage />);
    await screen.findAllByText('USER_APPROVED');
    // payload_safe_json is never exposed as a raw object in the UI; only the
    // bounded allowlisted fields appear.
    expect(screen.queryByText(/payload_safe_json/)).toBeNull();
    expect(screen.queryByText(/curr_hash/)).toBeNull();
  });
});
