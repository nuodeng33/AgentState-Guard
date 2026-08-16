/**
 * V1 Environment surface: folds the former Runtime and Agents primary views
 * into one real backend projection.
 *
 * Sources (frozen contract, docs/FRONTEND_BACKEND_CONTRACT.md):
 *   GET  /api/status      — bounded host status payload (verbatim checks)
 *   GET  /api/doctor      — bounded check list {check, status, message}
 *   GET  /api/v1/runtime  — r4-p8-1 runtime view
 *   GET  /api/v1/agents   — r4-p8-1 agents view
 *   POST /api/v1/discovery/refresh — exactly {}, then re-read the views above
 *
 * The page never infers freshness from elapsed time: observed_at and
 * timestamp_utc are rendered as source times only, and no stale/offline
 * verdict is derived. reason_code tokens stay verbatim.
 */

import { useState } from 'react';

import { apiClient, type ApiClient } from '../api/client';
import { getDoctor, getStatus, refreshDiscovery } from '../api/product';
import type { AgentsView, RuntimeView } from '../api/types';
import { useAsync } from '../api/useAsync';
import { useR4View } from '../api/useR4View';
import { SectionHeader } from '../components/SectionHeader';
import { ViewGate } from '../components/ViewGate';
import { useT, type Translate } from '../i18n/I18nProvider';
import { AgentsViewBody } from './AgentsPage';
import { RuntimeViewBody } from './RuntimePage';
import { DoctorSection, StatusSection } from './EnvironmentSections';

type RefreshPhase =
  | { kind: 'idle' }
  | { kind: 'refreshing' }
  | {
      kind: 'done';
      status: string;
      reason_code: string;
      runtime_count: number;
      agent_count: number;
      observed_at: string | null;
    }
  | { kind: 'failed' };

export default function EnvironmentPage({ client = apiClient }: { client?: ApiClient }) {
  const t = useT();
  const [refresh, setRefresh] = useState<RefreshPhase>({ kind: 'idle' });
  const [attempt, setAttempt] = useState(0);

  // Authoritative read projections; existing presentation bodies reused as-is.
  const runtime = useR4View<RuntimeView>('runtime', client);
  const agents = useR4View<AgentsView>('agents', client);
  // Bounded host projections; re-read whenever `attempt` bumps.
  const status = useAsync(() => getStatus(client), [client, attempt]);
  const doctor = useAsync(() => getDoctor(client), [client, attempt]);

  const reloadAll = () => {
    setAttempt((n) => n + 1);
    runtime.reload();
    agents.reload();
  };

  const runRefresh = () => {
    setRefresh({ kind: 'refreshing' });
    refreshDiscovery(client).then(
      (result) => {
        setRefresh({
          kind: 'done',
          status: result.status,
          reason_code: result.reason_code,
          runtime_count: result.runtime_count,
          agent_count: result.agent_count,
          observed_at: result.observed_at,
        });
        // Frozen contract: after a refresh, re-read the affected projections.
        reloadAll();
      },
      // The existing transport already maps 401 to re-bootstrap; a failure
      // here is a plain failed refresh, never a health verdict.
      () => setRefresh({ kind: 'failed' }),
    );
  };

  return (
    <div className="env-page">
      <div className="action-row env-actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={refresh.kind === 'refreshing'}
          onClick={runRefresh}
        >
          {refresh.kind === 'refreshing' ? t('env.refreshing') : t('env.refresh')}
        </button>
        {refresh.kind === 'done' && <RefreshSummary refresh={refresh} t={t} />}
        {refresh.kind === 'failed' && (
          <span className="muted" role="alert">
            {t('env.refreshFailed')}
          </span>
        )}
      </div>

      <SectionHeader title={t('env.section.status')} />
      <ViewGate state={status} label={t('env.section.status')}>
        {(data) => <StatusSection data={data} />}
      </ViewGate>

      <SectionHeader title={t('env.section.doctor')} />
      <ViewGate state={doctor} label={t('env.section.doctor')}>
        {(data) => <DoctorSection checks={data} />}
      </ViewGate>

      <SectionHeader title={t('env.section.runtime')} />
      {runtime.data?.observed_at && (
        <p className="muted env-observed">
          {t('env.observedAt', { time: runtime.data.observed_at })}
        </p>
      )}
      <ViewGate state={runtime} label={t('nav.runtime')}>
        {(data) => <RuntimeViewBody data={data} />}
      </ViewGate>

      <SectionHeader title={t('env.section.agents')} />
      {agents.data?.observed_at && (
        <p className="muted env-observed">
          {t('env.observedAt', { time: agents.data.observed_at })}
        </p>
      )}
      <ViewGate state={agents} label={t('nav.agents')}>
        {(data) => <AgentsViewBody data={data} />}
      </ViewGate>
    </div>
  );
}

function RefreshSummary({
  refresh,
  t,
}: {
  refresh: Extract<RefreshPhase, { kind: 'done' }>;
  t: Translate;
}) {
  return (
    <span className="muted env-refresh-summary" role="status">
      {t('env.refreshDone')} <code>{refresh.status}</code> <code>{refresh.reason_code}</code>
      {' · '}
      {t('kv.runtimeCount')}: {refresh.runtime_count}
      {' · '}
      {t('kv.agentCount')}: {refresh.agent_count}
      {refresh.observed_at ? ` · ${t('env.observedAt', { time: refresh.observed_at })}` : ''}
    </span>
  );
}
