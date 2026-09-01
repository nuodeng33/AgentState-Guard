/**
 * Changes product surface: the verified activity feed.
 *
 * Consumes only GET /api/v1/changes (r4-p8-1). Every field is rendered
 * verbatim from the backend projection — timestamps stay source times, no
 * lifecycle is derived, and reason_code/status tokens are never translated.
 *
 * Evidence detail is opened only from an item's own event_id, which per the
 * backend contract is a safe, queryable id for GET /api/v1/evidence/{event_id}.
 * Related evidence_refs are shown as references only — never dereferenced.
 */

import { useEffect, useState } from 'react';

import { apiClient, ApiRequestError, SessionUnavailableError, type ApiClient } from '../api/client';
import { getChangesPage, getEvidence } from '../api/product';
import type { ChangeItem, ChangesView, EvidenceDetail } from '../api/types';
import { useR4View } from '../api/useR4View';
import { EmptyState } from '../components/EmptyState';
import { ActivityTimeline } from '../components/ActivityTimeline';
import { EvidenceDetailPanel } from '../components/EvidenceDetailPanel';
import { EvidenceRefs } from '../components/EvidenceRefs';
import { SectionHeader } from '../components/SectionHeader';
import { StateBadge, viewStatusTone } from '../components/StateBadge';
import { DegradedPanel, UnknownPanel } from '../components/StatePanels';
import { ViewGate } from '../components/ViewGate';
import { useT } from '../i18n/I18nProvider';

type PanelState = {
  phase: 'idle' | 'loading' | 'error';
  detail: EvidenceDetail | null;
  error: string | null;
};

const IDLE_PANEL: PanelState = { phase: 'idle', detail: null, error: null };

export default function ChangesPage({
  client = apiClient,
  checkpointIdFilter = null,
  workspaceIdFilter = null,
  onOpenRecovery,
}: {
  client?: ApiClient;
  checkpointIdFilter?: string | null;
  workspaceIdFilter?: string | null;
  onOpenRecovery?: (checkpointId: string, workspaceId: string | null) => void;
}) {
  const [includeProcess, setIncludeProcess] = useState(false);
  const params = new URLSearchParams();
  if (includeProcess) params.set('include_process_activity', 'true');
  if (checkpointIdFilter) params.set('checkpoint_id', checkpointIdFilter);
  if (workspaceIdFilter) params.set('workspace_id', workspaceIdFilter);
  if (params.size > 0) params.set('limit', '100');
  const query = params.size > 0 ? `?${params.toString()}` : '';
  const state = useR4View<ChangesView>('changes', client, query);
  const t = useT();
  const [selected, setSelected] = useState<string | null>(null);
  const [panel, setPanel] = useState<PanelState>(IDLE_PANEL);

  const openEvidence = (item: ChangeItem) => {
    const eventId = item.event_id;
    setSelected(eventId);
    setPanel({ phase: 'loading', detail: null, error: null });
    getEvidence(eventId, client).then(
      (detail) => setPanel({ phase: 'idle', detail, error: null }),
      (err: unknown) => {
        if (err instanceof SessionUnavailableError) {
          setPanel({ phase: 'error', detail: null, error: 'SESSION_UNAVAILABLE' });
          return;
        }
        if (err instanceof ApiRequestError && err.status === 404) {
          // The frozen backend contract binds HTTP 404 on this endpoint to
          // exactly one meaning: the event is NOT_FOUND. The stable
          // reason_code is recovered from the failure body when whitelisted.
          setPanel({
            phase: 'idle',
            detail: {
              schema_version: 'r4-product-evidence-1',
              status: 'NOT_FOUND',
              reason_code: err.reasonCode ?? 'EVIDENCE_EVENT_NOT_FOUND',
              event_id: eventId,
            },
            error: null,
          });
          return;
        }
        setPanel({
          phase: 'error',
          detail: null,
          error: err instanceof ApiRequestError ? err.message : 'API request failed',
        });
      },
    );
  };

  const closePanel = () => {
    setSelected(null);
    setPanel(IDLE_PANEL);
  };

  return (
    <ViewGate state={state} label={t('nav.changes')}>
      {(data) => (
        <ChangesReady
          key={query}
          data={data}
          client={client}
          includeProcess={includeProcess}
          checkpointIdFilter={checkpointIdFilter}
          workspaceIdFilter={workspaceIdFilter}
          selected={selected}
          panel={panel}
          onToggleProcess={() => setIncludeProcess((value) => !value)}
          onOpenEvidence={openEvidence}
          onOpenRecovery={onOpenRecovery}
          onClosePanel={closePanel}
        />
      )}
    </ViewGate>
  );
}

function ChangesReady({
  data,
  client,
  includeProcess,
  checkpointIdFilter,
  workspaceIdFilter,
  selected,
  panel,
  onToggleProcess,
  onOpenEvidence,
  onOpenRecovery,
  onClosePanel,
}: {
  data: ChangesView;
  client: ApiClient;
  includeProcess: boolean;
  checkpointIdFilter: string | null;
  workspaceIdFilter: string | null;
  selected: string | null;
  panel: PanelState;
  onToggleProcess: () => void;
  onOpenEvidence: (item: ChangeItem) => void;
  onOpenRecovery?: (checkpointId: string, workspaceId: string | null) => void;
  onClosePanel: () => void;
}) {
  const [older, setOlder] = useState<ChangeItem[]>([]);
  const [cursor, setCursor] = useState<number | null>(data.next_cursor ?? null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    setOlder([]);
    setCursor(data.next_cursor ?? null);
  }, [data]);
  const combined: ChangesView = { ...data, items: [...data.items, ...older], next_cursor: cursor };
  const loadMore = () => {
    if (cursor == null || loadingMore) return;
    setLoadingMore(true);
    setLoadError(null);
    getChangesPage(cursor, client, {
      includeProcessActivity: includeProcess,
      workspaceId: workspaceIdFilter,
      checkpointId: checkpointIdFilter,
    }).then(
      (page) => {
        setOlder((items) => [...items, ...page.items]);
        setCursor(page.next_cursor ?? null);
        setLoadingMore(false);
      },
      () => {
        setLoadError('CHANGE_HISTORY_PAGE_UNAVAILABLE');
        setLoadingMore(false);
      },
    );
  };
  return (
    <>
      <div className="activity-toolbar">
        <button type="button" className="btn" onClick={onToggleProcess}>
          {includeProcess ? 'Hide process activity' : 'Show process activity'}
        </button>
      </div>
      {(checkpointIdFilter || workspaceIdFilter) && (
        <p className="card-sub">
          {checkpointIdFilter ? `Checkpoint ${checkpointIdFilter}` : `Workspace ${workspaceIdFilter}`}
        </p>
      )}
      <ChangesViewBody
        data={combined}
        includeProcess={includeProcess}
        selected={selected}
        panel={panel}
        onOpenEvidence={onOpenEvidence}
        onOpenRecovery={onOpenRecovery}
        onClosePanel={onClosePanel}
      />
      {cursor != null && (
        <button type="button" className="btn" disabled={loadingMore} onClick={loadMore}>
          {loadingMore ? 'Loading…' : 'Load more'}
        </button>
      )}
      {loadError && <p className="muted">{loadError}</p>}
    </>
  );
}

export function ChangesViewBody({
  data,
  includeProcess = false,
  selected = null,
  panel = IDLE_PANEL,
  onOpenEvidence,
  onOpenRecovery,
  onClosePanel,
}: {
  data: ChangesView;
  includeProcess?: boolean;
  selected?: string | null;
  panel?: PanelState;
  onOpenEvidence?: (item: ChangeItem) => void;
  onOpenRecovery?: (checkpointId: string, workspaceId: string | null) => void;
  onClosePanel?: () => void;
}) {
  const t = useT();
  const showPanel = selected !== null && (panel.phase !== 'idle' || panel.detail !== null);
  return (
    <div>
      <div className="view-head">
        <StateBadge label={data.status} tone={viewStatusTone(data.status)} />
      </div>

      {data.status === 'EMPTY' && (
        <EmptyState
          title={t('changes.empty.title')}
          detail={t('changes.empty.detail')}
          reasonCode={data.reason_code}
        />
      )}
      {data.status === 'DEGRADED' && (
        <DegradedPanel label={t('nav.changes')} reasonCode={data.reason_code} />
      )}
      {data.status === 'UNKNOWN' && (
        <UnknownPanel label={t('nav.changes')} reasonCode={data.reason_code} />
      )}

      <SectionHeader title={t('changes.section.recent')} />
      <ActivityTimeline
        activities={data.items}
        mode={includeProcess ? 'activity' : 'changes'}
        selectedEvidence={selected}
        onOpenEvidence={onOpenEvidence}
        onOpenRecovery={onOpenRecovery}
      />

      <EvidenceRefs refs={data.evidence_refs} />

      {showPanel && (
        <div>
          <EvidenceDetailPanel
            detail={panel.detail}
            phase={panel.phase}
            error={panel.error}
            onClose={onClosePanel}
          />
        </div>
      )}
    </div>
  );
}
