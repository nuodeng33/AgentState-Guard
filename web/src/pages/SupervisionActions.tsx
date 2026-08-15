/**
 * Authoritative one-time supervision actions (r4-p8-action-1).
 *
 * Rules enforced here:
 * - The only field crossing the boundary is the server-issued `action_ref`
 *   from the latest GET /api/v1/supervision item. The client never generates,
 *   persists, or reuses it across sessions.
 * - A mutation is sent exactly once per click; the shared client applies at
 *   most one 401 re-bootstrap. 409/503 are never retried.
 * - No optimistic authority update: after every attempt the supervision view
 *   is re-read and only the refreshed server state is displayed. Approving
 *   never activates operations, creates checkpoints, or shows ALLOW.
 * - Feedback is stable and display-safe: HTTP status class plus the backend's
 *   allowlisted reason_code only. Raw server/exception text never renders.
 *   reason_code values are machine tokens and render verbatim in every locale.
 */

import { useRef, useState } from 'react';

import {
  ApiActionError,
  SessionUnavailableError,
  type ApiClient,
} from '../api/client';
import type { SupervisionItem } from '../api/types';
import { useT } from '../i18n/I18nProvider';
import type { MessageKey } from '../i18n/messages';

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
  messageKey: MessageKey;
  reasonCode: string | null;
}

/** Map failures to stable, display-safe feedback keyed by the frozen error mapping. */
function feedbackFor(err: unknown): Feedback {
  if (err instanceof SessionUnavailableError) {
    return { messageKey: 'feedback.sessionUnavailable', reasonCode: 'SESSION_UNAVAILABLE' };
  }
  if (err instanceof ApiActionError) {
    switch (err.status) {
      case 404:
        return { messageKey: 'feedback.notFound', reasonCode: err.reasonCode };
      case 409:
        return { messageKey: 'feedback.conflict', reasonCode: err.reasonCode };
      case 422:
        return { messageKey: 'feedback.invalid', reasonCode: err.reasonCode };
      case 503:
        return { messageKey: 'feedback.unavailable', reasonCode: err.reasonCode };
      default:
        return { messageKey: 'feedback.unconfirmed', reasonCode: err.reasonCode };
    }
  }
  return { messageKey: 'feedback.unknown', reasonCode: null };
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
  const t = useT();
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
          {pending === 'approve' ? t('action.approving') : t('action.approveOnce')}
        </button>
        <button
          type="button"
          className="btn btn-danger"
          disabled={disabled}
          onClick={() => run('reject')}
        >
          {pending === 'reject' ? t('action.rejecting') : t('action.reject')}
        </button>
        {pending !== null && (
          <span className="action-pending" role="status">
            {pending === 'approve' ? t('action.sendingApproval') : t('action.sendingRejection')}
          </span>
        )}
      </div>
      {feedback && (
        <p className="action-feedback" role="alert">
          {t(feedback.messageKey)}
          {feedback.reasonCode !== null && (
            <>
              {' '}
              <code>{feedback.reasonCode}</code>
            </>
          )}
        </p>
      )}
      <p className="action-note">{t('action.note')}</p>
    </div>
  );
}
