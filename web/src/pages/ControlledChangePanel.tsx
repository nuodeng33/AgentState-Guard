/**
 * Bounded controlled configuration change flow (r4-p9-controlled-change-1).
 *
 * Production boundaries enforced here:
 * - The only caller-owned input is `content` (TOML text). Target, policy,
 *   checkpoint requirement, approval, verification and result are entirely
 *   server-owned; the UI offers no path/target/command input.
 * - prepare → POST /api/v1/supervision/changes with exactly {content}.
 *   The backend verdict (decision, requires_*, action_ref, advisory) drives
 *   the next step; nothing is inferred locally.
 * - The backend decision drives everything: REVIEW requires the one-time
 *   Approve Once action (server-issued action_ref) before Apply is enabled.
 * - apply → POST /api/v1/supervision/{session_id}/apply with the exact same
 *   content the session was prepared with.
 * - After every settled mutation, the authoritative supervision projection
 *   is re-read; only the re-read state is displayed. Feedback is limited to
 *   the backend's stable reason_code.
 */

import { useRef, useState } from 'react';

import {
  ApiActionError,
  SessionUnavailableError,
  type ApiClient,
} from '../api/client';
import { applyControlledChange, prepareControlledChange } from '../api/product';
import type {
  ControlledChangeApplyResult,
  ControlledChangePrep,
} from '../api/types';
import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { SectionHeader } from '../components/SectionHeader';
import { policyTone, StateBadge } from '../components/StateBadge';
import { useT } from '../i18n/I18nProvider';
import { SupervisionActions } from './SupervisionActions';

type Phase =
  | { kind: 'edit' }
  | { kind: 'preparing' }
  | { kind: 'prepared'; prep: ControlledChangePrep }
  | { kind: 'approving'; prep: ControlledChangePrep }
  | { kind: 'approved'; prep: ControlledChangePrep }
  | { kind: 'applying'; prep: ControlledChangePrep }
  | { kind: 'applied'; result: ControlledChangeApplyResult }
  | { kind: 'failed'; error: string };

export function ControlledChangePanel({
  client,
  onChanged,
}: {
  client: ApiClient;
  /** Re-read the authoritative supervision projection after any mutation. */
  onChanged: () => void;
}) {
  const t = useT();
  const [content, setContent] = useState('');
  const [phase, setPhase] = useState<Phase>({ kind: 'edit' });
  const inFlight = useRef(false);

  const stableReason = (err: unknown, fallback: string): string => {
    if (err instanceof ApiActionError && err.reasonCode !== null) return err.reasonCode;
    if (err instanceof SessionUnavailableError) return 'SESSION_UNAVAILABLE';
    return fallback;
  };

  const prepare = async () => {
    if (inFlight.current || content.trim() === '') return;
    inFlight.current = true;
    setPhase({ kind: 'preparing' });
    try {
      const prep = await prepareControlledChange(content, client);
      setPhase({ kind: 'prepared', prep });
    } catch (err: unknown) {
      setPhase({ kind: 'failed', error: stableReason(err, 'CONTROLLED_CHANGE_REQUEST_INVALID') });
    } finally {
      inFlight.current = false;
      onChanged();
    }
  };

  /**
   * Apply only unlocks after the backend's own approval acknowledgement.
   * The product flow is always prepare → REVIEW/AWAITING_APPROVAL →
   * Approve Once → APPROVED → apply. An ALLOW/EVALUATED session is a
   * read-only verdict here: no approval binding exists, so backend's apply
   * gate (status APPROVED or ACTIVE per r4_controlled_change.py:180-181)
   * would be unprovable; the UI exposes no way to attempt it.
   */
  const apply = async (prep: ControlledChangePrep) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPhase({ kind: 'applying', prep });
    try {
      const result = await applyControlledChange(prep.supervision_session_id, content, client);
      setPhase({ kind: 'applied', result });
    } catch (err: unknown) {
      setPhase({ kind: 'failed', error: stableReason(err, 'CONTROLLED_CHANGE_REQUEST_INVALID') });
    } finally {
      inFlight.current = false;
      onChanged();
    }
  };

  const reset = () => {
    setContent('');
    setPhase({ kind: 'edit' });
  };

  const prep = phase.kind === 'prepared' || phase.kind === 'approved' || phase.kind === 'applying' ? phase.prep : null;

  return (
    <section className="card" data-testid="cc-panel">
      <SectionHeader title={t('cc.section')} />
      <textarea
        className="cc-content"
        aria-label={t('cc.section')}
        placeholder={t('cc.contentPlaceholder')}
        rows={10}
        value={content}
        disabled={phase.kind !== 'edit' && phase.kind !== 'failed'}
        onChange={(event) => setContent(event.target.value)}
      />
      <div className="action-row">
        <button
          type="button"
          className="btn btn-primary"
          disabled={inFlight.current || content.trim() === '' || (phase.kind !== 'edit' && phase.kind !== 'failed')}
          onClick={prepare}
        >
          {phase.kind === 'preparing' ? t('cc.preparing') : t('cc.prepare')}
        </button>
        {phase.kind !== 'edit' && (
          <button type="button" className="btn" onClick={reset}>
            {t('cc.reset')}
          </button>
        )}
      </div>

      {phase.kind === 'failed' && (
        <p className="action-feedback" role="alert">
          <code>{phase.error}</code>
        </p>
      )}

      {prep && <PreparedSummary prep={prep} phase={phase.kind} />}

      {prep && prep.decision === 'REVIEW' && phase.kind === 'prepared' && prep.action_ref && (
        <>
          <p className="muted">{t('cc.applyApprovedHint')}</p>
          <SupervisionActions
            sessionId={prep.supervision_session_id}
            actionRef={prep.action_ref}
            client={client}
            onChanged={onChanged}
            onSettled={(ok) => {
              if (ok) setPhase((prev) => (prev.kind === 'prepared' ? { kind: 'approved', prep } : prev));
            }}
          />
        </>
      )}

      {(phase.kind === 'prepared' || phase.kind === 'approved' || phase.kind === 'applying') && (
        <div className="action-row">
          <button
            type="button"
            className="btn btn-primary"
            disabled={phase.kind !== 'approved'}
            onClick={() => {
              if (phase.kind === 'approved') apply(phase.prep);
            }}
          >
            {phase.kind === 'applying' ? t('cc.applyRunning') : t('cc.apply')}
          </button>
        </div>
      )}

      {phase.kind === 'applied' && <ApplySummary result={phase.result} />}
    </section>
  );
}

function PreparedSummary({ prep, phase }: { prep: ControlledChangePrep; phase: Phase['kind'] }) {
  const t = useT();
  return (
    <div className="cc-prepared">
      <p className="card-sub">{t('cc.preparedTitle')}</p>
      <KeyValueGrid>
        <KeyValue k="supervision_session_id" v={<code>{prep.supervision_session_id}</code>} />
        <KeyValue
          k={t('cc.decision')}
          v={<StateBadge label={prep.decision} tone={policyTone(prep.decision)} />}
        />
        <KeyValue k="status" v={<code>{prep.status}</code>} />
        <KeyValue k="reason_code" v={<code>{prep.reason_code}</code>} />
        <KeyValue
          k={t('cc.requiresApproval')}
          v={prep.requires_manual_approval ? t('common.yes') : t('common.no')}
        />
        <KeyValue
          k={t('cc.requiresCheckpoint')}
          v={prep.requires_checkpoint ? t('common.yes') : t('common.no')}
        />
        <KeyValue k="ai_advisory" v={<code>{prep.ai_advisory}</code>} />
      </KeyValueGrid>
      {phase === 'approved' && (
        <p className="muted" role="status">
          {t('cc.applyApprovedHint')}
        </p>
      )}
    </div>
  );
}

function ApplySummary({ result }: { result: ControlledChangeApplyResult }) {
  const t = useT();
  return (
    <div className="cc-applied">
      <p className="card-sub">{t('cc.applyDoneTitle')}</p>
      <KeyValueGrid>
        <KeyValue k="status" v={<code>{result.status}</code>} />
        <KeyValue k="reason_code" v={<code>{result.reason_code}</code>} />
        <KeyValue k={t('cc.changed')} v={result.changed ? t('common.yes') : t('common.no')} />
        <KeyValue k={t('cc.verification')} v={orDash(result.verification)} />
        <KeyValue
          k={t('cc.rolledBack')}
          v={result.rolled_back === null ? <span className="muted">—</span> : result.rolled_back ? t('common.yes') : t('common.no')}
        />
        <KeyValue
          k={t('cc.beforeDigest')}
          v={result.before_digest === null ? <span className="muted">—</span> : <code>{result.before_digest}</code>}
        />
        <KeyValue
          k={t('cc.afterDigest')}
          v={result.after_digest === null ? <span className="muted">—</span> : <code>{result.after_digest}</code>}
        />
        <KeyValue k={t('cc.checkpointId')} v={orDash(result.checkpoint_id)} />
      </KeyValueGrid>
    </div>
  );
}
