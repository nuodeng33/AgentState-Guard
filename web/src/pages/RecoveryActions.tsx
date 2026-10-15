/**
 * Recovery actions (Desktop only): Create Checkpoint / Test Restore / Restore.
 *
 * These are the only recovery mutations and they call exactly the frozen
 * contract endpoints (POST /api/v1/recovery/checkpoints {}, /{id}/test {},
 * /{id}/restore {confirm:true}). The action result is rendered only as a
 * receipt: the authoritative recovery projection is always re-read after
 * every mutation. A created checkpoint is never presented as recoverable;
 * restore always passes through the explicit typed confirmation in this
 * component plus the backend's own {confirm:true} requirement.
 *
 * Failures show only the stable backend reason_code (never raw error text)
 * and never replace the last authoritative view on screen.
 */

import { useState } from 'react';

import type { ApiClient } from '../api/client';
import { failureText } from '../api/errorText';
import { createRecoveryCheckpoint, restoreRecovery, testRecovery } from '../api/product';
import type { RecoveryActionResult } from '../api/types';
import { useT, type Translate } from '../i18n/I18nProvider';

export type ActionPhase =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'done'; result: RecoveryActionResult }
  | { kind: 'fail'; reasonCode: string };

function reasonOf(err: unknown): string {
  return failureText(err);
}

export function useRecoveryAction(
  run: (client: ApiClient) => Promise<RecoveryActionResult>,
  client: ApiClient,
  onChanged: () => void,
): { phase: ActionPhase; start: () => void } {
  const [phase, setPhase] = useState<ActionPhase>({ kind: 'idle' });
  const start = () => {
    setPhase({ kind: 'running' });
    run(client).then(
      (result) => {
        setPhase({ kind: 'done', result });
        onChanged();
      },
      (err: unknown) => setPhase({ kind: 'fail', reasonCode: reasonOf(err) }),
    );
  };
  return { phase, start };
}

/** Top-of-page Create Checkpoint action. Creation ≠ recoverable. */
export function CreateCheckpointAction({
  client,
  onChanged,
}: {
  client: ApiClient;
  onChanged: () => void;
}) {
  const t = useT();
  const { phase, start } = useRecoveryAction((c) => createRecoveryCheckpoint(c), client, onChanged);
  return (
    <div className="recovery-create">
      <div className="action-row">
        <button
          type="button"
          className="btn btn-primary"
          onClick={start}
          disabled={phase.kind === 'running'}
        >
          {phase.kind === 'running' ? t('recovery.create.running') : t('recovery.create')}
        </button>
        <ActionOutcome phase={phase} t={t} />
      </div>
      {phase.kind === 'done' && (
        <p className="muted">
          {t('recovery.create.done', {
            status: phase.result.status,
            id: phase.result.checkpoint_id ?? '—',
          })}
        </p>
      )}
    </div>
  );
}

/** Per-checkpoint Test Restore / Restore actions. */
export function RecoveryItemActions({
  checkpointId,
  client,
  onChanged,
  disabled,
}: {
  checkpointId: string;
  client: ApiClient;
  onChanged: () => void;
  /** Fail-closed items reject mutations at the server anyway; keep the row quiet. */
  disabled?: boolean;
}) {
  const t = useT();
  const test = useRecoveryAction((c) => testRecovery(checkpointId, c), client, onChanged);
  const restore = useRecoveryAction((c) => restoreRecovery(checkpointId, c), client, onChanged);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const yes = t('common.yes');

  return (
    <div className="recovery-item-actions">
      <div className="action-row">
        <button
          type="button"
          className="btn"
          onClick={test.start}
          disabled={disabled || test.phase.kind === 'running' || restore.phase.kind === 'running'}
        >
          {test.phase.kind === 'running' ? t('recovery.test.running') : t('recovery.test')}
        </button>
        <button
          type="button"
          className="btn btn-danger"
          onClick={() => setConfirmOpen(true)}
          disabled={disabled || test.phase.kind === 'running' || restore.phase.kind === 'running'}
        >
          {restore.phase.kind === 'running' ? t('recovery.restore.running') : t('recovery.restore')}
        </button>
      </div>
      <ActionOutcome phase={test.phase} t={t} />
      <ActionOutcome phase={restore.phase} t={t} />
      {confirmOpen && (
        <div
          className="panel panel-bad"
          role="alertdialog"
          aria-label={t('recovery.restore.confirmTitle')}
        >
          <p className="panel-title">{t('recovery.restore.confirmTitle')}</p>
          <p>{t('recovery.restore.confirmBody', { id: checkpointId })}</p>
          <TypedConfirmation
            expected={yes}
            prompt={t('recovery.restore.confirmType', { word: yes })}
            confirmLabel={t('recovery.restore')}
            cancelLabel={t('devices.cancel')}
            onConfirmed={() => {
              setConfirmOpen(false);
              restore.start();
            }}
            onCancel={() => setConfirmOpen(false)}
          />
        </div>
      )}
    </div>
  );
}

function ActionOutcome({ phase, t }: { phase: ActionPhase; t: Translate }) {
  if (phase.kind === 'done') {
    return (
      <span className="muted recovery-action-receipt" role="status">
        {t('recovery.action.receipt')} <code>{phase.result.status}</code>{' '}
        <code>{phase.result.reason_code}</code>
      </span>
    );
  }
  if (phase.kind === 'fail') {
    return (
      <span className="muted recovery-action-fail" role="alert">
        {t('recovery.action.failed', { reasonCode: phase.reasonCode })}
      </span>
    );
  }
  return null;
}

function TypedConfirmation({
  expected,
  prompt,
  confirmLabel,
  cancelLabel,
  onConfirmed,
  onCancel,
}: {
  expected: string;
  prompt: string;
  confirmLabel: string;
  cancelLabel: string;
  onConfirmed: () => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState('');
  return (
    <form
      className="action-row"
      onSubmit={(event) => {
        event.preventDefault();
        if (value.trim() === expected) onConfirmed();
      }}
    >
      <label htmlFor="restore-confirm-input" className="muted">
        {prompt}
      </label>
      <input
        id="restore-confirm-input"
        className="cc-content recovery-confirm-input"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        autoComplete="off"
      />
      <button type="submit" className="btn btn-danger" disabled={value.trim() !== expected}>
        {confirmLabel}
      </button>
      <button type="button" className="btn" onClick={onCancel}>
        {cancelLabel}
      </button>
    </form>
  );
}
