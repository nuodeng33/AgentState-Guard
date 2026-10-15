import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiActionError, type ApiClient } from '../api/client';
import type { AgentItem, DiscoveryInventoryCoverage, DiscoverySource, DiscoverySources, EvidenceDetail } from '../api/types';
import { DISCOVERY_POLL_INTERVAL_MS, getEvidence, refreshDiscovery } from '../api/product';
import { AgentIdentityDetails, AgentIdentityLabel, agentIdentityLabel } from '../components/AgentIdentity';
import { DiscoveryCatalog } from '../components/DiscoveryCatalog';
import { EvidenceDetailPanel } from '../components/EvidenceDetailPanel';
import { useI18n } from '../i18n/I18nProvider';
import './work-agents.css';

export type WorkAgent = AgentItem & {
  agent_id: string;
  association?: 'USER_LINKED' | null;
  activity: 'UNKNOWN';
  presence?: 'CURRENT' | 'NOT_CURRENT' | 'STALE' | 'UNKNOWN';
  task_ids?: string[];
};

function enrichLinked(linked: WorkAgent[], detected: WorkAgent[]): WorkAgent[] {
  const byId = new Map(detected.map(item => [item.agent_id, item]));
  return linked.map(item => {
    const current = byId.get(item.agent_id);
    return current ? {
      ...item,
      application_id: item.application_id ?? current.application_id,
      program_path: item.program_path ?? current.program_path,
      source_id: item.source_id ?? current.source_id,
      source_label: item.source_label ?? current.source_label,
      source_short_id: item.source_short_id ?? current.source_short_id,
      pid: item.pid ?? current.pid,
      cwd: item.cwd ?? current.cwd,
      control_capabilities: item.control_capabilities ?? current.control_capabilities,
    } : item;
  });
}

export function useWorkAgents(client: ApiClient, workId: string | undefined, visible: boolean) {
  const [result, setResult] = useState<{ id?: string; items: WorkAgent[]; failed: boolean }>({ items: [], failed: false });
  const sequence = useRef(0);
  const reload = useCallback(async () => {
    if (!workId) return;
    const seq = ++sequence.current;
    try {
      const value = await client.get<{ items: WorkAgent[] }>(`/api/v1/works/${workId}/agents`);
      if (!Array.isArray(value.items)) throw new Error('Missing agent list');
      if (seq === sequence.current) setResult({ id: workId, items: value.items, failed: false });
    } catch {
      if (seq === sequence.current) setResult(previous => ({ id: workId, items: previous.id === workId ? previous.items : [], failed: true }));
    }
  }, [client, workId]);
  useEffect(() => {
    if (visible) void reload();
    return () => { sequence.current += 1; };
  }, [reload, visible]);
  return { items: result.id === workId ? result.items : [], failed: result.id === workId && result.failed, reload,
    replace: (items: WorkAgent[]) => { sequence.current += 1; setResult({ id: workId, items, failed: false }); } };
}

export default function WorkAgents({ client, workId, revision, linked, tasks = [], initialAgentId, visible = true, onClose, onLinkedChanged, onRevision }: {
  client: ApiClient; workId: string; revision: number; linked: WorkAgent[]; initialAgentId?: string; visible?: boolean;
  tasks?: { id: string; title: string }[];
  onClose: () => void; onLinkedChanged: (items: WorkAgent[]) => void; onRevision: (revision: number) => void;
}) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const [items, setItems] = useState(linked); const [candidates, setCandidates] = useState<WorkAgent[]>([]);
  const [selected, setSelected] = useState<string | undefined>(initialAgentId);
  const [currentRevision, setCurrentRevision] = useState(revision);
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [discoveryState, setDiscoveryState] = useState('');
  const [sources, setSources] = useState<DiscoverySource[]>([]);
  const [inventoryCoverage, setInventoryCoverage] = useState<DiscoveryInventoryCoverage[]>([]);
  const [catalogRefreshKey, setCatalogRefreshKey] = useState(0);
  const [sourceError, setSourceError] = useState('');
  const [sourceBusy, setSourceBusy] = useState<string | null>(null);
  const [taskIds, setTaskIds] = useState<string[]>([]);
  const pendingTasks = useRef<{ signature: string; payload: object } | null>(null);
  const [evidence, setEvidence] = useState<EvidenceDetail | null>(null);
  const [evidencePhase, setEvidencePhase] = useState<'idle' | 'loading' | 'error'>('idle');
  const [showEvidence, setShowEvidence] = useState(false);
  const live = useRef(true); const root = useRef<HTMLElement>(null);
  const evidenceSequence = useRef(0);
  const loadInFlight = useRef<Promise<void> | null>(null);
  const linkedSignature = useRef('');
  const revisionRef = useRef(revision);
  const callbacks = useRef({ onLinkedChanged, onRevision, onClose }); callbacks.current = { onLinkedChanged, onRevision, onClose };
  const pending = useRef<{ agentId: string; detach: boolean; path: string; payload: object } | null>(null);
  const failed = zh ? '未能读取 Agent 记录，请重试。' : 'Could not load Agent records. Retry.';
  useEffect(() => { setCurrentRevision(revision); revisionRef.current = revision; }, [revision]);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  async function load(refresh = false, quiet = false) {
    if (quiet && loadInFlight.current) return;
    while (loadInFlight.current) {
      await loadInFlight.current;
    }
    const request = (async () => {
      if (!quiet) { setLoading(true); setError(''); }
      try {
        if (refresh) await refreshDiscovery(client);
        const [associated, detected, work, sourceList] = await Promise.all([
          client.get<{ items: WorkAgent[] }>(`/api/v1/works/${workId}/agents`),
          client.get<{ items: WorkAgent[]; status: string }>('/api/v1/agents'),
          client.get<{ revision: number }>(`/api/v1/works/${workId}`),
          client.get<DiscoverySources>('/api/v1/discovery/sources').catch(() => null),
        ]);
        if (!live.current) return;
        if (!Array.isArray(associated.items) || !Array.isArray(detected.items)) throw new Error('Missing agent list');
        const linkedItems = enrichLinked(associated.items, detected.items);
        setItems(linkedItems); setCandidates(detected.items.filter(agent => Boolean(agent.agent_id)));
        setDiscoveryState(detected.status); setCurrentRevision(work.revision);
        const signature = JSON.stringify(linkedItems);
        if (signature !== linkedSignature.current) {
          linkedSignature.current = signature;
          callbacks.current.onLinkedChanged(linkedItems);
        }
        if (work.revision !== revisionRef.current) {
          revisionRef.current = work.revision;
          callbacks.current.onRevision(work.revision);
        }
        if (sourceList && Array.isArray(sourceList.items)) {
          setSources(sourceList.items); setInventoryCoverage(sourceList.inventory_coverage ?? []); setSourceError('');
        } else setSourceError(zh ? '未能读取观察来源。' : 'Could not load observation sources.');
        if (!quiet) setCatalogRefreshKey(value => value + 1);
      } catch { if (live.current && !quiet) setError(failed); }
      finally { if (live.current && !quiet) setLoading(false); }
    })();
    loadInFlight.current = request;
    try { await request; } finally { if (loadInFlight.current === request) loadInFlight.current = null; }
  }
  useEffect(() => { void load(); }, [client, workId]);
  useEffect(() => {
    if (!visible) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load(false, true);
    }, DISCOVERY_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [client, workId, visible, zh]);
  const leave = () => {
    if (showEvidence) { evidenceSequence.current += 1; setShowEvidence(false); setEvidence(null); }
    else if (selected && !initialAgentId) setSelected(undefined);
    else callbacks.current.onClose();
  };
  useEffect(() => {
    if (!visible) return;
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); leave(); }
      if (event.key === 'Tab') {
        const nodes = root.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), summary');
        const first = nodes?.[0], last = nodes?.[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', keyboard);
    return () => document.removeEventListener('keydown', keyboard);
  }, [visible, selected, initialAgentId, showEvidence]);
  async function associate(agentId: string, detach: boolean) {
    if (busy) return;
    if (!detach && !candidates.some(item => item.agent_id === agentId && item.presence === 'CURRENT' && item.lifecycle !== 'UNKNOWN')) return;
    setBusy(true); setError('');
    const previous = pending.current;
    const command = previous?.agentId === agentId && previous.detach === detach ? previous : {
      agentId, detach,
      path: `/api/v1/works/${workId}/agents${detach ? `/${encodeURIComponent(agentId)}/detach` : ''}`,
      payload: { request_id: crypto.randomUUID(), expected_revision: currentRevision, ...(detach ? {} : { agent_id: agentId }) },
    };
    pending.current = command;
    try {
      const value = await client.post<{ revision: number }>(command.path, command.payload);
      if (!live.current) return;
      pending.current = null; setCurrentRevision(value.revision); callbacks.current.onRevision(value.revision);
      await load();
    } catch (err) {
      if (!live.current) return;
      const conflict = err instanceof ApiActionError && err.reasonCode === 'WORK_REVISION_CONFLICT';
      if (conflict) pending.current = null;
      setError(conflict ? (zh ? '工作已在别处变更。请刷新记录后重试。' : 'This work changed elsewhere. Reload records before retrying.') :
        (zh ? '关联操作未确认，可重试同一操作。' : 'The association was not confirmed. Retry the same action.'));
    } finally { if (live.current) setBusy(false); }
  }
  async function openEvidence(id: string) {
    const sequence = ++evidenceSequence.current;
    setShowEvidence(true); setEvidence(null); setEvidencePhase('loading');
    try { const detail = await getEvidence(id, client); if (live.current && sequence === evidenceSequence.current) { setEvidence(detail); setEvidencePhase('idle'); } }
    catch { if (live.current && sequence === evidenceSequence.current) setEvidencePhase('error'); }
  }
  const agent = [...items, ...candidates].find(item => item.agent_id === selected);
  const associatedIds = new Set(items.map(item => item.agent_id));
  const savedTaskIds = agent?.task_ids?.join(',') ?? '';
  useEffect(() => { setTaskIds(savedTaskIds ? savedTaskIds.split(',') : []); }, [selected, savedTaskIds]);
  async function saveTasks() {
    if (!agent || busy) return;
    setBusy(true); setError('');
    const signature = JSON.stringify([agent.agent_id, [...taskIds].sort()]);
    if (pendingTasks.current?.signature !== signature) pendingTasks.current = { signature,
      payload: { request_id: crypto.randomUUID(), expected_revision: currentRevision, task_ids: taskIds } };
    try {
      const result = await client.post<{ revision: number }>(`/api/v1/works/${workId}/agents/${encodeURIComponent(agent.agent_id)}/tasks`, pendingTasks.current.payload);
      if (!live.current) return;
      pendingTasks.current = null; setCurrentRevision(result.revision); callbacks.current.onRevision(result.revision); await load();
    } catch (err) {
      if (!live.current) return;
      if (err instanceof ApiActionError && err.reasonCode === 'WORK_REVISION_CONFLICT') {
        pendingTasks.current = null;
        setError(zh ? '工作已变更，请刷新记录后重新选择任务。' : 'This work changed. Reload records and select the tasks again.');
      } else setError(zh ? '任务关联未确认，请重试保存。' : 'Task links were not confirmed. Retry saving.');
    } finally { if (live.current) setBusy(false); }
  }
  async function configureSource(source: DiscoverySource, enabled: boolean) {
    if (sourceBusy || !source.configurable) return;
    setSourceBusy(source.source_id); setSourceError('');
    try {
      await client.post(`/api/v1/discovery/sources/${encodeURIComponent(source.source_id)}/configure`,
        { enabled, expected_revision: source.revision });
      if (live.current) await load();
    } catch (err) {
      if (!live.current) return;
      const conflict = err instanceof ApiActionError && err.reasonCode === 'DISCOVERY_SOURCE_REVISION_CONFLICT';
      setSourceError(conflict ? (zh ? '来源设置已变更，请刷新发现后重试。' : 'Source settings changed. Refresh discovery before retrying.') :
        (zh ? '未能更新观察来源。' : 'Could not update observation source.'));
    } finally { if (live.current) setSourceBusy(null); }
  }
  const available = candidates.filter(item => !associatedIds.has(item.agent_id));
  const canLink = (item: WorkAgent) => item.presence === 'CURRENT' && item.lifecycle !== 'UNKNOWN';
  const name = (item: WorkAgent) => agentIdentityLabel(item, zh);
  const state = (item: WorkAgent) => item.presence === 'NOT_CURRENT' ? (zh ? '本次未观测到 · 活动未知' : 'Not in current observation · activity unknown') :
    item.presence === 'STALE' ? (zh ? '观测已过期 · 活动未知' : 'Observation expired · activity unknown') :
    item.presence === 'UNKNOWN' || item.presence !== 'CURRENT' ? (zh ? '是否仍在运行未知 · 活动未知' : 'Presence unknown · activity unknown') :
    item.lifecycle === 'RUNNING' ? (zh ? '进程已观测 · 工作活动未知' : 'Process observed · work activity unknown') : (zh ? '已发现 · 工作活动未知' : 'Detected · work activity unknown');
  const sourceState = (value: string) => ({
    OBSERVED: zh ? '已观测' : 'Observed', PARTIAL: zh ? '部分观测' : 'Partial',
    EMPTY: zh ? '未发现' : 'Empty', UNREACHABLE: zh ? '无法访问' : 'Unreachable',
    DISABLED: zh ? '已暂停' : 'Disabled', NOT_PROBED: zh ? '未探测' : 'Not probed',
    STALE: zh ? '已过期' : 'Stale', AVAILABLE: zh ? '可用' : 'Available',
    NOT_PRESENT: zh ? '未发现' : 'Not present', UNSUPPORTED: zh ? '不支持' : 'Unsupported',
    DEGRADED: zh ? '部分可用' : 'Partial', ERROR: zh ? '出错' : 'Error',
    UNAVAILABLE: zh ? '不可用' : 'Unavailable', AUTH_REQUIRED: zh ? '需要授权' : 'Authorization required',
    PROTOCOL_NOT_CONNECTED: zh ? '尚未查询' : 'Not queried',
  }[value] ?? (zh ? '状态未知' : 'Unknown'));
  const row = (item: WorkAgent, associated: boolean) => <article className="work-agent-row" key={item.agent_id}>
    <button className="work-agent-identity" onClick={() => { setSelected(item.agent_id); setShowEvidence(false); }}>
      <span className="work-agent-dot" aria-hidden="true" /><span><strong><AgentIdentityLabel item={item} /></strong><small>{state(item)}</small>
      <small>{item.observed_at ? new Date(item.observed_at).toLocaleString(locale) : (zh ? '观测时间未知' : 'Observation time unknown')}</small></span>
    </button>
    <button disabled={busy || loading || (!associated && !canLink(item))} onClick={() => void associate(item.agent_id, associated)}>{associated ? (zh ? '解除关联' : 'Unlink') : (zh ? '关联到此工作' : 'Link to this work')}</button>
  </article>;
  return <section ref={root} className="work-agents-layer" role="dialog" aria-modal="true" aria-label={zh ? '实际 Agent' : 'Actual Agents'}>
    <header><button autoFocus onClick={leave}>← {zh ? '返回' : 'Back'}</button><h2>{agent ? name(agent) : (zh ? '实际 Agent' : 'Actual Agents')}</h2>
      {!showEvidence && <button disabled={busy || loading || Boolean(sourceBusy)} onClick={() => void load(true)}>{loading ? (zh ? '读取中…' : 'Loading…') : (zh ? '刷新发现' : 'Refresh discovery')}</button>}
    </header>
    <div className="work-agents-content">
      {error && <p role="alert">{error} <button disabled={busy || loading} onClick={() => void load()}>{zh ? '刷新记录' : 'Reload records'}</button></p>}
      {showEvidence ? <EvidenceDetailPanel detail={evidence} phase={evidencePhase} /> : agent ? <>
        <p className="work-agent-state">{state(agent)}</p>
        <dl className="work-agent-facts"><div><dt>{zh ? '此工作中的关系' : 'Relationship to this work'}</dt><dd>{associatedIds.has(agent.agent_id) ? (zh ? '用户关联' : 'User linked') : (zh ? '尚未关联' : 'Not linked')}</dd></div>
          <div><dt>{zh ? '执行事实' : 'Execution facts'}</dt><dd>{zh ? '尚无执行此工作或子任务的可验证记录。' : 'No verified execution record for this work or its tasks.'}</dd></div>
          <div><dt>{zh ? '最近观测' : 'Last observed'}</dt><dd>{agent.observed_at ? new Date(agent.observed_at).toLocaleString(locale) : '—'}</dd></div>
        </dl>
        <button disabled={busy || loading || (!associatedIds.has(agent.agent_id) && !canLink(agent))} onClick={() => void associate(agent.agent_id, associatedIds.has(agent.agent_id))}>{associatedIds.has(agent.agent_id) ? (zh ? '解除关联' : 'Unlink') : (zh ? '关联到此工作' : 'Link to this work')}</button>
        {associatedIds.has(agent.agent_id) && <fieldset className="work-agent-tasks"><legend>{zh ? '关联任务' : 'Linked tasks'}</legend>
          {!tasks.length && <p>{zh ? '这项工作还没有任务。' : 'This work has no tasks yet.'}</p>}
          {tasks.map(task => <label key={task.id}><input type="checkbox" disabled={busy || loading} checked={taskIds.includes(task.id)} onChange={event => setTaskIds(ids => event.target.checked ? [...ids, task.id] : ids.filter(id => id !== task.id))} />{task.title}</label>)}
          {!!tasks.length && <button disabled={busy || loading} onClick={() => void saveTasks()}>{zh ? '保存任务关联' : 'Save task links'}</button>}
        </fieldset>}
        <AgentIdentityDetails item={agent}>
          <p>{agent.lifecycle} · {agent.role} · {agent.reason_code}</p>
          <p>{zh ? '工作目录关联' : 'Workspace binding'}: {agent.workspace?.status ?? 'UNKNOWN'} · {agent.workspace?.binding_ref ?? '—'}</p>
          {agent.evidence_refs.map(ref => <button key={ref} onClick={() => void openEvidence(ref)}>{zh ? '查看证据' : 'View evidence'} · {ref.slice(0, 12)}</button>)}
        </AgentIdentityDetails>
      </> : <>
        <p className="work-agent-intro">{zh ? '选择当前观测到的 Agent 关联到这项工作。关联本身不会启动执行。' : 'Link a currently observed Agent to this work. Linking does not start execution.'}</p>
        <h3>{zh ? '已关联' : 'Linked'} · {items.length}</h3>
        {items.length ? items.map(item => row(item, true)) : !loading && <p>{zh ? '这项工作还没有关联实际 Agent。' : 'No actual Agents are linked to this work yet.'}</p>}
        <h3>{zh ? '发现的 Agent' : 'Discovered agents'}</h3>
        {available.map(item => row(item, false))}
        {!loading && !available.length && <p>{zh ? '没有其他可关联的 Agent。可刷新发现。' : 'No other Agents available. Refresh discovery to check again.'}</p>}
        {(discoveryState === 'UNKNOWN' || discoveryState === 'DEGRADED') && <p>{zh ? '观测不完整，不能据此判断 Agent 不存在。' : 'Observation is incomplete; this does not establish absence.'}</p>}
        <details className="work-agent-sources"><summary>{zh ? '观察来源' : 'Observation sources'} · {sources.length}</summary>
          {sourceError && <p role="alert">{sourceError}</p>}
          {sources.map(source => <div className="work-agent-source-row" key={source.source_id}>
            <div><strong>{source.label}{source.short_id && <span className="work-agent-source-id"> · {source.short_id}</span>}</strong>
              <small>{sourceState(source.state)} · {source.agent_count} {zh ? '个 Agent' : 'agents'}</small></div>
            {source.configurable && <label><input type="checkbox" checked={source.enabled} disabled={loading || Boolean(sourceBusy)}
              onChange={event => void configureSource(source, event.target.checked)} />{zh ? '观察' : 'Observe'}</label>}
            <DiscoveryCatalog client={client} source={source}
              coverage={inventoryCoverage.find(item => item.source_id === source.source_id)}
              visible={visible} refreshKey={catalogRefreshKey} zh={zh} locale={locale} />
          </div>)}
        </details>
      </>}
    </div>
  </section>;
}
