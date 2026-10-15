/**
 * Controlled Change wire tests: exact endpoints/bodies, backend-driven
 * decision gating, no local policy/lifecycle. Scripted fetch mirrors
 * r4-p9-controlled-change-1 and r4-p8-action-1.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ControlledChangePanel } from './ControlledChangePanel';
import { createApiClient } from '../api/client';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const PREP: Record<string, unknown> = {
  schema_version: 'r4-p9-controlled-change-1',
  supervision_session_id: 'session-aaaa0000-1111-2222-3333-444455556666',
  status: 'AWAITING_APPROVAL',
  decision: 'REVIEW',
  reason_code: 'CONTROLLED_CHANGE_PREPARED',
  requires_manual_approval: true,
  requires_checkpoint: false,
  ai_advisory: 'UNAVAILABLE',
  action_ref: 'a'.repeat(64),
};

const PREP_ALLOW: unknown = {
  ...PREP,
  status: 'EVALUATED',
  decision: 'ALLOW',
  requires_manual_approval: false,
  action_ref: null,
};

const APPROVED: unknown = {
  schema_version: 'r4-p8-action-1',
  action: 'APPROVE_ONCE',
  supervision_session_id: (PREP as { supervision_session_id: string }).supervision_session_id,
  status: 'APPROVED',
  reason_code: 'SUPERVISION_APPROVED_ONCE',
  consumed: true,
  evidence_refs: [],
};

const APPLIED: unknown = {
  schema_version: 'r4-p9-controlled-change-1',
  supervision_session_id: (PREP as { supervision_session_id: string }).supervision_session_id,
  status: 'COMPLETED',
  reason_code: 'CONTROLLED_CHANGE_APPLIED',
  changed: true,
  verification: 'TOML_PARSE_OK',
  rolled_back: false,
  before_digest: 'b'.repeat(64),
  after_digest: 'c'.repeat(64),
  checkpoint_id: 'checkpoint-3',
  evidence_refs: ['evt-cc-1'],
};

type Call = { path: string; body: unknown };

function flowFetch(calls: Call[], overrides: Record<string, () => Response> = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    calls.push({ path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path === '/api/session') return json({ token: 'tok' });
    if (overrides[path]) return overrides[path]();
    return json({}, 404);
  });
}

const CONTENT = '[doc]\nkey = "value"\n';

describe('ControlledChangePanel wire contract', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it('REVIEW flow: prepare → approve-once → apply with exact bodies, approve后才可 apply', async () => {
    const calls: Call[] = [];
    const fetchImpl = flowFetch(calls, {
      '/api/v1/supervision/changes': () => json(PREP),
      [`/api/v1/supervision/${(PREP as { supervision_session_id: string }).supervision_session_id}/approve-once`]:
        () => json(APPROVED),
      [`/api/v1/supervision/${(PREP as { supervision_session_id: string }).supervision_session_id}/apply`]:
        () => json(APPLIED),
    });
    vi.stubGlobal('fetch', fetchImpl);
    const client = createApiClient(fetchImpl as unknown as typeof fetch);

    render(<ControlledChangePanel client={client} onChanged={() => {}} />);
    fireEvent.change(screen.getByLabelText('Controlled Change'), { target: { value: CONTENT } });
    fireEvent.click(screen.getByRole('button', { name: 'Prepare change' }));

    // Backend verdict rendered verbatim; Apply not yet enabled.
    const decision = await screen.findByText('REVIEW');
    expect(decision).toBeTruthy();
    expect((fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls.map(([i]) => String(i))).toContain(
      '/api/v1/supervision/changes',
    );
    expect(calls.find((c) => c.path === '/api/v1/supervision/changes')?.body).toEqual({ content: CONTENT });
    const applyBtn = screen.getByRole('button', { name: 'Apply controlled change' });
    expect((applyBtn as HTMLButtonElement).disabled).toBe(true);

    // Approve once with the server-issued action_ref.
    fireEvent.click(screen.getByRole('button', { name: 'Approve Once' }));
    await waitFor(() => {
      const approveCall = calls.find((c) => c.path.endsWith('/approve-once'));
      expect(approveCall?.body).toEqual({ action_ref: 'a'.repeat(64) });
    });

    // Apply enabled only after the server acknowledged.
    await waitFor(() => {
      expect((screen.getByRole('button', { name: 'Apply controlled change' }) as HTMLButtonElement).disabled).toBe(false);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply controlled change' }));
    await waitFor(() => {
      const applyCall = calls.find((c) => c.path.endsWith('/apply'));
      expect(applyCall?.body).toEqual({ content: CONTENT });
    });
    await screen.findByText('CONTROLLED_CHANGE_APPLIED');
    expect(screen.getByText("TOML_PARSE_OK")).toBeTruthy();
  });

  it('ALLOW verdict is read-only: no approval binding exists, so Apply is never offered', async () => {
    // Backend truth (r4_controlled_change.apply_controlled_change:180-181):
    // apply requires the session to be APPROVED or ACTIVE. An ALLOW/EVALUATED
    // session carries no action_ref and no approval path, so any attempt would
    // fail with CONTROLLED_CHANGE_APPROVAL_REQUIRED. The UI must never offer it.
    const calls: Call[] = [];
    const fetchImpl = flowFetch(calls, {
      '/api/v1/supervision/changes': () => json(PREP_ALLOW),
    });
    vi.stubGlobal('fetch', fetchImpl);
    const client = createApiClient(fetchImpl as unknown as typeof fetch);

    render(<ControlledChangePanel client={client} onChanged={() => {}} />);
    fireEvent.change(screen.getByLabelText('Controlled Change'), { target: { value: CONTENT } });
    fireEvent.click(screen.getByRole('button', { name: 'Prepare change' }));
    await screen.findByText('ALLOW');

    // Verdict surfaces verbatim; there is no approve, and Apply stays disabled.
    expect(screen.getByText('EVALUATED')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Approve Once' })).toBeNull();
    const applyBtn = screen.getByRole('button', { name: 'Apply controlled change' }) as HTMLButtonElement;
    expect(applyBtn.disabled).toBe(true);

    fireEvent.click(applyBtn);
    expect(calls.some((c) => c.path.endsWith('/apply'))).toBe(false);
  });

  it('prepare failure (503) shows stable reason only and never enables apply', async () => {
    const calls: Call[] = [];
    const fetchImpl = flowFetch(calls, {
      '/api/v1/supervision/changes': () =>
        json(
          {
            schema_version: 'r4-p9-controlled-change-1',
            supervision_session_id: null,
            status: 'UNCHANGED',
            reason_code: 'CONTROLLED_CHANGE_AUTHORITY_UNAVAILABLE',
            changed: false,
          },
          503,
        ),
    });
    vi.stubGlobal('fetch', fetchImpl);
    const client = createApiClient(fetchImpl as unknown as typeof fetch);

    render(<ControlledChangePanel client={client} onChanged={() => {}} />);
    fireEvent.change(screen.getByLabelText('Controlled Change'), { target: { value: CONTENT } });
    fireEvent.click(screen.getByRole('button', { name: 'Prepare change' }));
    expect(await screen.findByText('CONTROLLED_CHANGE_AUTHORITY_UNAVAILABLE')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Apply controlled change' })).toBeNull();
    // No authoritative state mutated; only the reason_code surfaced.
    expect(calls.some((c) => c.path.endsWith('/apply'))).toBe(false);
  });
});
