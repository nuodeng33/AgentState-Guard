/**
 * Authoritative one-time supervision actions (r4-p8-action-1).
 *
 * Rules enforced here:
 * - The only field crossing the boundary is the server-issued `action_ref`
 *   from the latest GET /api/v1/supervision item. The client never generates,
 *   persists, or reuses it across sessions.
 * - A mutation is sent exactly once per click; the shared client applies at
 *   most one 401 session re-bootstrap. 409/503 are never retried.
 * - No optimistic authority update: after every attempt the supervision view
 *   is re-read and only the refreshed server state is displayed. Approving
 *   never activates operations, creates checkpoints, or shows ALLOW.
 * - Feedback is stable and display-safe: HTTP status class plus the backend's
 *   allowlisted reason_code only. Raw server/exception text never renders.
 */

import { useRef, useState } from 'react';

import {
  ApiActionError,
  SessionUnavailableError,
  type ApiClient,
} from '../api/client';
import type { SupervisionItem } from '../api/types';

export type SupervisionActionKind = 'approve' | 'reject';

/**
 * The frozen visibility rule: both buttons appear only while the item is an
 * actionable REVIEW session. Anything else — BLOCK, UNKNOWN, ALLOW, terminal
 * states, missing binding — never shows an executable action.
 */
export function isActionable(item: SupervisionItem): boolean {
  return (
    item.policy_decision === 'REVIEW' &&
    item.status === 'AWAITING_APPROVAL' &&
    item.requires_manual_approval === true &&
    typeof item.action_ref === 'string' &&
    item.action_ref.length > 0
  );
}

interface Feedback {
  text: string;
  reasonCode: string | null;
}

/** Map failures to stable, display-safe feedback keyed by the frozen error mapping. */
function feedbackFor(err: unknown): Feedback {
  if (err instanceof SessionUnavailableError) {
    return {
      text: 'Session unavailable — the action was not confirmed.',
      reasonCode: 'SESSION_UNAVAILABLE',
    };
  }
  if (err instanceof ApiActionError) {
    switch (err.status) {
      case 404:
        return { text: 'This supervision session no longer exists.', reasonCode: err.reasonCode };
      case 409:
        return {
          text: 'Action not applied: the authoritative state has moved on. The latest state has been reloaded.',
          reasonCode: err.reasonCode,
        };
      case 422:
        return { text: 'The action request was rejected as invalid.', reasonCode: err.reasonCode };
      case 503:
        return {
          text: 'Authoritative state is temporarily unavailable. The latest readable state has been reloaded.',
          reasonCode: err.reasonCode,
        };
      default:
        return {
          text: 'The action was not confirmed. The latest authoritative state has been reloaded.',
          reasonCode: err.reasonCode,
        };
    }
  }
  return {
    text: 'The action result is unknown. The latest authoritative state has been reloaded.',
    reasonCode: null,
  };
}

export function SupervisionActions({
  sessionId,
  actionRef,
  client,
  onChanged,
  busy = false,
}: {
  sessionId: string;
  actionRef: string;
  client: ApiClient;
  /** Re-read GET /api/v1/supervision; invoked after every settled attempt. */
  onChanged: () => void;
  /** True while the view is already re-reading; keeps actions disabled. */
  busy?: boolean;
}) {
  const [pending, setPending] = useState<SupervisionActionKind | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  // Synchronous guard against double-click before the state flush lands.
  const inFlight = useRef(false);

  async function run(kind: SupervisionActionKind) {
    if (inFlight.current || busy) return;
    inFlight.current = true;
    setPending(kind);
    setFeedback(null);
    const actionPath = kind === 'approve' ? 'approve-once' : 'reject';
    try {
      await client.post(`/api/v1/supervision/${sessionId}/${actionPath}`, {
        action_ref: actionRef,
      });
      // Success is acknowledged but never applied locally; the refetch below
      // is the only source of truth.
    } catch (err: unknown) {
      setFeedback(feedbackFor(err));
    } finally {
      inFlight.current = false;
      setPending(null);
      onChanged();
    }
  }

  const disabled = pending !== null || busy;
  return (
    <div className="action-area">
      <div className="action-row">
        <button
          type="button"
          className="btn btn-primary"
          disabled={disabled}
          onClick={() => run('approve')}
        >
          {pending === 'approve' ? 'Approving…' : 'Approve Once'}
        </button>
        <button
          type="button"
          className="btn btn-danger"
          disabled={disabled}
          onClick={() => run('reject')}
        >
          {pending === 'reject' ? 'Rejecting…' : 'Reject'}
        </button>
        {pending !== null && (
          <span className="action-pending" role="status">
            Sending {pending === 'approve' ? 'approval' : 'rejection'}…
          </span>
        )}
      </div>
      {feedback && (
        <p className="action-feedback" role="alert">
          {feedback.text}
          {feedback.reasonCode !== null && (
            <>
              {' '}
              <code>{feedback.reasonCode}</code>
            </>
          )}
        </p>
      )}
      <p className="action-note">
        One-time action bound to the latest authoritative read. Approval never activates
        operations, creates checkpoints, or changes policy; rejection is terminal. The view
        always re-reads the server state afterwards.
      </p>
    </div>
  );
}
