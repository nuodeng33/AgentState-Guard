/**
 * V1 Environment surface: folds the former Runtime and Agents primary views
 * into one real backend projection.
 *
 * Sources (frozen contract, docs/FRONTEND_BACKEND_CONTRACT.md):
 *   GET  /api/status      — bounded host status payload (verbatim checks)
 *   GET  /api/doctor      — bounded check list {check, status, message}
 *   GET  /api/v1/runtime  — r4-p8-1 runtime view
 *   GET  /api/v1/agents   — r4-p8-1 agents view
 *   GET  /api/v1/discovery/sources — source-scoped running/probe facts
 *   GET  /api/v1/discovery/applications — source-scoped installed inventory
 *   POST /api/v1/discovery/refresh — exactly {}, then re-read the views above
 *
 * The page never infers freshness from elapsed time: observed_at and
 * timestamp_utc are rendered as source times only, and no stale/offline
 * verdict is derived. reason_code tokens stay verbatim.
 */

import { useEffect, useRef, useState } from 'react';

import { apiClient, type ApiClient } from '../api/client';
import { DISCOVERY_POLL_INTERVAL_MS, getDoctor, getStatus, refreshDiscovery } from '../api/product';
import type { AgentsView, RuntimeView, DiscoverySource, DiscoverySources, DiscoveryApplication, DiscoveryPage } from '../api/types';
import { useAsync } from '../api/useAsync';
import { useR4View } from '../api/useR4View';
import { AnalyzeSection } from '../components/AnalyzeSection';
import { SectionHeader } from '../components/SectionHeader';
import { ViewGate } from '../components/ViewGate';
import { useI18n, useT, type Translate } from '../i18n/I18nProvider';
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
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const [refresh, setRefresh] = useState<RefreshPhase>({ kind: 'idle' });
  const [attempt, setAttempt] = useState(0);

  // Authoritative read projections; existing presentation bodies reused as-is.
  const runtime = useR4View<RuntimeView>('runtime', client);
  const agents = useR4View<AgentsView>('agents', client);
  const agentReadBusy = agents.phase === 'loading' || agents.refreshing;
  const agentReadBusyRef = useRef(agentReadBusy);
  const pendingAgentReload = useRef(false);
  agentReadBusyRef.current = agentReadBusy;
  useEffect(() => {
    if (!agentReadBusy && pendingAgentReload.current) {
      pendingAgentReload.current = false;
      agents.reload();
    }
  }, [agentReadBusy, agents.reload]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible' || agentReadBusyRef.current) return;
      agents.reload();
    }, DISCOVERY_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [agents.reload]);
  // Bounded host projections; re-read whenever `attempt` bumps.
  const status = useAsync(() => getStatus(client), [client, attempt]);
  const doctor = useAsync(() => getDoctor(client), [client, attempt]);
  const sources = useAsync(() => client.get<DiscoverySources>('/api/v1/discovery/sources'), [client, attempt]);
  const applications = useAsync(() => client.get<DiscoveryPage<DiscoveryApplication>>('/api/v1/discovery/applications?limit=100&offset=0'), [client, attempt]);

  const reloadAll = () => {
    setAttempt((n) => n + 1);
    runtime.reload();
    if (agentReadBusyRef.current) pendingAgentReload.current = true;
    else agents.reload();
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

      <SectionHeader title={zh ? '发现的环境与程序' : 'Discovered environments and programs'} />
      <p className="muted">{zh ? '运行状态、进程探测与已安装程序分别记录；运行中的容器不代表已连接或允许受管执行。' : 'Running state, process probes and installed programs are separate observations. A running container does not imply a connected or authorized executor.'}</p>
      <ViewGate state={sources} label={zh ? '环境来源' : 'Environment sources'}>
        {data => <EnvironmentSources data={data} applications={applications.data?.items ?? []} zh={zh} />}
      </ViewGate>
      {applications.phase === 'api-error' && <p role="alert">{zh ? '程序库存读取失败；未将其视为空。' : 'Program inventory could not be read; it is not treated as empty.'}</p>}
      {applications.data?.has_more && <p>{zh ? '此处显示前100条程序记录；其余记录可在程序与会话库中查看。' : 'The first 100 program records are shown here; additional records remain available in Programs and session stores.'}</p>}
      <p className="muted">{zh ? '下方版本和检查仅描述运行 Core 的主机。Python 表示 Core 自身的运行时；其他版本取自主机 PATH，未观测到不代表其他环境未安装。' : 'Versions below describe the Core host. Python is the Core runtime; other versions come from the host PATH. An unobserved host version does not mean a tool is absent from other environments.'}</p>
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

      <SectionHeader title={t('env.section.analyze')} />
      {client && <AnalyzeSection client={client} />}
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


type EnvironmentSource = DiscoverySource & { running?: boolean; connected?: boolean;
  protection?: { running?: boolean; connected?: boolean } };
function EnvironmentSources({ data, applications, zh }: { data: DiscoverySources; applications: DiscoveryApplication[]; zh: boolean }) {
  if (!data.items.length) return <p>{zh ? '尚无环境观测，请刷新发现。' : 'No environment observations yet. Refresh discovery.'}</p>;
  return <>{data.items.map(raw => {
    const source = raw as EnvironmentSource;
    const running = source.protection?.running ?? source.running;
    const connected = source.protection?.connected ?? source.connected;
    const programs = applications.filter(item => item.source_id === source.source_id);
    const coverage = data.inventory_coverage?.find(item => item.source_id === source.source_id);
    const runLabel = running === true ? (zh ? '正在运行' : 'Running') : running === false ? (zh ? '未运行' : 'Stopped') : (zh ? '运行状态未观测' : 'Running state unobserved');
    const probeLabel = source.state === 'UNREACHABLE' ? (zh ? '进程探测不可达' : 'Process probe unavailable') : source.state;
    return <section className="card" key={source.source_id}>
      <h3>{source.label}</h3>
      <p style={{ overflowWrap: 'anywhere' }}>{source.kind} · {source.source_id}</p>
      <p>{runLabel} · {probeLabel}</p>
      {connected !== undefined && <p>{zh ? '目录连接' : 'Directory connection'}: {connected ? (zh ? '已连接' : 'Connected') : (zh ? '未连接' : 'Disconnected')}</p>}
      <p>{source.freshness} · <code>{source.reason_code}</code></p>
      {coverage && <p>{zh ? '程序库存' : 'Program inventory'}: {coverage.status} · <code>{coverage.reason_code}</code></p>}
      {programs.map(item => <div key={item.id}>
        <strong>{item.label || item.provider} · {item.version || (zh ? '版本未观测' : 'Version unobserved')}</strong>
        <p style={{ overflowWrap: 'anywhere' }}>{item.path}</p>
        <p>{item.running ? (zh ? '进程已观测' : 'Process observed') : (zh ? '未观测到进程' : 'No process observed')} · {item.status} · {item.freshness}</p>
      </div>)}
      {!programs.length && <p>{zh ? '暂无可展示的程序记录；请核对该来源的库存覆盖状态。' : 'No program records displayed; check inventory coverage for this source.'}</p>}
    </section>;
  })}</>;
}
