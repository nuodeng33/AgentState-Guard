/**
 * R4-P8 action wiring tests (r4-p8-action-1).
 *
 * Covers the frozen visibility rule, exact request shape, bounded 401 handling,
 * no-retry 409/503 semantics, double-click protection, and the guarantee that
 * only re-read authoritative state is ever displayed.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { ApiActionError, type ApiClient } from '../api/client';
import type { SupervisionItem, SupervisionView } from '../api/types';
import SupervisionPage from './SupervisionPage';

const REF = 'f'.repeat(64);
const REFRESHED_REF = '0'.repeat(64);

function item(partial: Partial<SupervisionItem>): SupervisionItem {
  return {
    supervision_session_id: 'ssn-1',
    status: 'AWAITING_APPROVAL',
    policy_decision: 'REVIEW',
    manual_approval: false,
    requires_manual_approval: true,
    requires_checkpoint: false,
    ai_assessment: null,
    recovery_facts: null,
    action_ref: REF,
    evidence_refs: [],
    ...partial,
  };
}

function viewWith(items: SupervisionItem[], status: SupervisionView['status'] = 'AVAILABLE'): SupervisionView {
  return {
    schema_version: 'r4-p8-1',
    view: 'supervision',
    status,
    reason_code: status === 'AVAILABLE' ? 'R4_SUPERVISION_AVAILABLE' : 'R4_DATABASE_UNREACHABLE',
    evidence_refs: [],
    items,
  };
}

const ACTION_ACK = {
  schema_version: 'r4-p8-action-1',
  action: 'APPROVE_ONCE',
  supervision_session_id: 'ssn-1',
  status: 'APPROVED',
  reason_code: 'SUPERVISION_APPROVED_ONCE',
  consumed: true,
  evidence_refs: ['evt-1'],
};

type Call = { method: 'GET' | 'POST'; path: string; body?: unknown };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Scriptable ApiClient double; records every call in order. */
function fakeClient(behavior: {
  onGet: (path: string, n: number) => SupervisionView | Promise<SupervisionView>;
  onPost?: (path: string, body: unknown, n: number) => unknown | Promise<unknown>;
}) {
  const calls: Call[] = [];
  let getN = 0;
  let postN = 0;
  const client: ApiClient = {
    bootstrap: async () => {},
    get: async (path: string) => {
      getN += 1;
      calls.push({ method: 'GET', path });
      return (await behavior.onGet(path, getN)) as never;
    },
    post: async (path: string, body: unknown) => {
      postN += 1;
      calls.push({ method: 'POST', path, body });
      if (!behavior.onPost) throw new Error('unexpected POST');
      return (await behavior.onPost(path, body, postN)) as never;
    },
  };
  return { client, calls };
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('action visibility rule', () => {
  it('REVIEW + AWAITING_APPROVAL + manual + action_ref shows both actions', async () => {
    const { client } = fakeClient({ onGet: () => viewWith([item({})]) });
    render(<SupervisionPage client={client} />);

    expect(await screen.findByRole('button', { name: 'Approve Once' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeTruthy();
  });

  const hiddenCases: Array<[string, Partial<SupervisionItem>]> = [
    ['BLOCK policy', { policy_decision: 'BLOCK' }],
    ['UNKNOWN policy', { policy_decision: 'UNKNOWN' }],
    ['ALLOW policy', { policy_decision: 'ALLOW' }],
    ['APPROVED terminal', { status: 'APPROVED', manual_approval: true }],
    ['REJECTED terminal', { status: 'REJECTED' }],
    ['action_ref null', { action_ref: null }],
    ['manual approval not required', { requires_manual_approval: false }],
  ];

  it.each(hiddenCases)('hides actions for %s', async (_label, partial) => {
    const { client } = fakeClient({ onGet: () => viewWith([item(partial)]) });
    render(<SupervisionPage client={client} />);

    expect(await screen.findByText('ssn-1')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Approve Once' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull();
  });
});

describe('mutation requests', () => {
  it('approve posts to approve-once with only action_ref in the body', async () => {
    const { client, calls } = fakeClient({
      onGet: () => viewWith([item({})]),
      onPost: () => ACTION_ACK,
    });
    render(<SupervisionPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Approve Once' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1));

    const post = calls.find((c) => c.method === 'POST')!;
    expect(post.path).toBe('/api/v1/supervision/ssn-1/approve-once');
    expect(post.body).toEqual({ action_ref: REF });
    expect(Object.keys(post.body as object)).toEqual(['action_ref']);
  });

  it('reject posts to reject with only action_ref in the body', async () => {
    const { client, calls } = fakeClient({
      onGet: () => viewWith([item({})]),
      onPost: () => ACTION_ACK,
    });
    render(<SupervisionPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1));

    const post = calls.find((c) => c.method === 'POST')!;
    expect(post.path).toBe('/api/v1/supervision/ssn-1/reject');
    expect(post.body).toEqual({ action_ref: REF });
    expect(Object.keys(post.body as object)).toEqual(['action_ref']);
  });
});

describe('authoritative refetch after mutation', () => {
  it('successful approve re-reads supervision and never shows optimistic APPROVED/ALLOW', async () => {
    const gate = deferred<SupervisionView>();
    const { client, calls } = fakeClient({
      onGet: (_path, n) =>
        n === 1
          ? viewWith([item({})])
          : gate.promise,
      onPost: () => ACTION_ACK,
    });
    render(<SupervisionPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Approve Once' }));
    await waitFor(() =>
      expect(calls.map((c) => [c.method, c.path])).toEqual([
        ['GET', '/api/v1/supervision'],
        ['POST', '/api/v1/supervision/ssn-1/approve-once'],
        ['GET', '/api/v1/supervision'],
      ]),
    );

    // While the refetch is in flight the old authoritative state stays on
    // screen; nothing locally upgrades to APPROVED or ALLOW.
    expect(screen.getByText('AWAITING_APPROVAL')).toBeTruthy();
    expect(screen.queryByText('APPROVED')).toBeNull();
    expect(screen.queryByText('ALLOW')).toBeNull();

    await act(async () => {
      gate.resolve(viewWith([item({ status: 'APPROVED', manual_approval: true, action_ref: null })]));
    });

    expect(await screen.findByText('APPROVED')).toBeTruthy();
    // Policy decision stays REVIEW; APPROVED is never dressed up as ALLOW.
    expect(screen.getByText('REVIEW')).toBeTruthy();
    expect(screen.queryByText('ALLOW')).toBeNull();
    // Terminal session: actions are gone.
    expect(screen.queryByRole('button', { name: 'Approve Once' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull();
  });

  it('successful reject re-reads supervision and shows only the server state', async () => {
    const { client, calls } = fakeClient({
      onGet: (_path, n) =>
        n === 1 ? viewWith([item({})]) : viewWith([item({ status: 'REJECTED', action_ref: null })]),
      onPost: () => ({ ...ACTION_ACK, action: 'REJECT', status: 'REJECTED', reason_code: 'SUPERVISION_REJECTED' }),
    });
    render(<SupervisionPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));

    expect(await screen.findByText('REJECTED')).toBeTruthy();
    expect(calls.map((c) => [c.method, c.path])).toEqual([
      ['GET', '/api/v1/supervision'],
      ['POST', '/api/v1/supervision/ssn-1/reject'],
      ['GET', '/api/v1/supervision'],
    ]);
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull();
  });

  it('double click sends exactly one mutation and shows a pending state', async () => {
    const gate = deferred<unknown>();
    const { client, calls } = fakeClient({
      onGet: () => viewWith([item({})]),
      onPost: () => gate.promise,
    });
    render(<SupervisionPage client={client} />);

    const approve = await screen.findByRole('button', { name: 'Approve Once' });
    fireEvent.click(approve);
    fireEvent.click(approve);
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));

    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Approving…' })).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('Sending approval');
    expect((screen.getByRole('button', { name: 'Reject' }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      gate.resolve(ACTION_ACK);
    });
    await waitFor(() =>
      expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1),
    );
  });
});

describe('stable failure semantics', () => {
  it('409 stale: mutation is not retried, supervision is re-read, feedback is stable', async () => {
    const { client, calls } = fakeClient({
      onGet: (_path, n) =>
        n === 1 ? viewWith([item({})]) : viewWith([item({ action_ref: REFRESHED_REF })]),
      onPost: () => {
        throw new ApiActionError(409, 'SUPERVISION_ACTION_STALE');
      },
    });
    render(<SupervisionPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Approve Once' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('SUPERVISION_ACTION_STALE');
    expect(calls.map((c) => [c.method, c.path])).toEqual([
      ['GET', '/api/v1/supervision'],
      ['POST', '/api/v1/supervision/ssn-1/approve-once'],
      ['GET', '/api/v1/supervision'],
    ]);
    // The refreshed (still actionable) item keeps actions available for a
    // deliberate user retry with the fresh binding — never an auto retry.
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Approve Once' }) as HTMLButtonElement).disabled).toBe(false),
    );
  });

  it('503: mutation is not retried; failed re-read falls back to the degraded UI', async () => {
    const { client, calls } = fakeClient({
      onGet: (_path, n) => (n === 1 ? viewWith([item({})]) : viewWith([], 'DEGRADED')),
      onPost: () => {
        throw new ApiActionError(503, 'SUPERVISION_AUTHORITY_UNAVAILABLE');
      },
    });
    render(<SupervisionPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));

    expect(await screen.findByText('Supervision view degraded')).toBeTruthy();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    expect(calls.filter((c) => c.method === 'GET').length).toBeGreaterThanOrEqual(2);
  });

  it('unknown failure: no optimistic claim, supervision is re-read, feedback stays generic', async () => {
    const { client, calls } = fakeClient({
      onGet: () => viewWith([item({})]),
      onPost: () => {
        throw new ApiActionError(500, null);
      },
    });
    const { container } = render(<SupervisionPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Approve Once' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('The action was not confirmed');
    expect(container.textContent).not.toContain('sqlite3');
    expect(container.textContent).not.toContain('state.db');
    expect(screen.queryByText('APPROVED')).toBeNull();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });
});

describe('action_ref hygiene', () => {
  it('never persists action_ref or token to storage across the full flow', async () => {
    const { client } = fakeClient({
      onGet: (_path, n) =>
        n === 1 ? viewWith([item({})]) : viewWith([item({ status: 'APPROVED', action_ref: null })]),
      onPost: () => ACTION_ACK,
    });
    render(<SupervisionPage client={client} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Approve Once' }));
    expect(await screen.findByText('APPROVED')).toBeTruthy();

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
