import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiActionError, type ApiClient } from '../api/client';
import type { AgentItem, DiscoveryApplication, DiscoveryInventoryCoverage, DiscoverySource, DiscoverySources, EvidenceDetail } from '../api/types';
import { DISCOVERY_POLL_INTERVAL_MS, getEvidence, refreshDiscovery } from '../api/product';
import { AgentIdentityDetails, AgentIdentityLabel, AgentProgramIcon, agentIdentityLabel, agentSourceLabel } from '../components/AgentIdentity';
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

function enrichLinked(linked: WorkAgent[], detected: WorkAgent[], freshObservation = false): WorkAgent[] {
  const byId = new Map(detected.map(item => [item.agent_id, item]));
  return linked.map(item => {
    const current = byId.get(item.agent_id);
    return current ? {
      ...item,
      ...(freshObservation ? { lifecycle: current.lifecycle, presence: current.presence, freshness: current.freshness,
        confidence: current.confidence, uncertainty: current.uncertainty, workspace: current.workspace,
        observed_at: current.observed_at, reason_code: current.reason_code } : {}),
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

type AgentRead = { generation: number; value?: unknown; pending?: Promise<unknown> };
const agentReads = new WeakMap<ApiClient, { id: number; reads: Map<string, AgentRead> }>();
let nextClientId = 0;
function cacheFor(client: ApiClient) {
  let cache = agentReads.get(client);
  if (!cache) { cache = { id: ++nextClientId, reads: new Map() }; agentReads.set(client, cache); }
  return cache;
}
function readEntry(client: ApiClient, path: string) {
  const reads = cacheFor(client).reads;
  let entry = reads.get(path);
  if (!entry) { entry = { generation: 0 }; reads.set(path, entry); }
  return entry;
}
function readAgentsOnce<T>(client: ApiClient, path: string): Promise<T> {
  const entry = readEntry(client, path);
  if (entry.pending) return entry.pending as Promise<T>;
  const generation = ++entry.generation;
  const request = client.get<T>(path).then(value => {
    if (generation !== entry.generation) throw new Error('Superseded Agent read');
    return value;
  }).finally(() => { if (entry.pending === request) entry.pending = undefined; });
  entry.pending = request; return request;
}
function knownItems<T>(client: ApiClient, path: string): T[] | undefined {
  const value = readEntry(client, path).value as { items?: T[] } | undefined;
  return Array.isArray(value?.items) ? value.items : undefined;
}
function invalidateRead(client: ApiClient, path: string) {
  const entry = readEntry(client, path); entry.generation++; entry.pending = undefined;
}

export function useWorkAgents(client: ApiClient, workId: string | undefined, visible: boolean) {
  const [result, setResult] = useState<{ client?: ApiClient; id?: string; items: WorkAgent[]; failed: boolean; loading: boolean }>({ items: [], failed: false, loading: true });
  const sequence = useRef(0);
  const path = `/api/v1/works/${workId}/agents?view=links`;
  const reload = useCallback(async () => {
    if (!workId) return;
    const seq = ++sequence.current;
    setResult(previous => ({ client, id: workId,
      items: previous.client === client && previous.id === workId ? previous.items : knownItems<WorkAgent>(client, path) ?? [],
      failed: false, loading: knownItems<WorkAgent>(client, path) === undefined }));
    try {
      const value = await readAgentsOnce<{ items: WorkAgent[] }>(client, path);
      if (!Array.isArray(value.items)) throw new Error('Missing agent list');
      if (seq === sequence.current) {
        readEntry(client, path).value = value;
        setResult({ client, id: workId, items: value.items, failed: false, loading: false });
      }
    } catch {
      if (seq === sequence.current) setResult(previous => ({ client, id: workId,
        items: previous.client === client && previous.id === workId ? previous.items : knownItems<WorkAgent>(client, path) ?? [], failed: true, loading: false }));
    }
  }, [client, workId, path]);
  useEffect(() => { if (visible) void reload(); return () => { sequence.current++; }; }, [reload, visible]);
  return { items: result.client === client && result.id === workId ? result.items : knownItems<WorkAgent>(client, path) ?? [],
    failed: result.client === client && result.id === workId && result.failed,
    loading: Boolean(workId) && knownItems<WorkAgent>(client, path) === undefined &&
      (result.client !== client || result.id !== workId || result.loading), reload,
    replace: (items: WorkAgent[]) => {
      sequence.current++; invalidateRead(client, path); readEntry(client, path).value = { items };
      setResult({ client, id: workId, items, failed: false, loading: false });
    } };
}

type WorkAgentsProps = {
  client: ApiClient; workId: string; revision: number; linked: WorkAgent[]; initialAgentId?: string; visible?: boolean;
  tasks?: { id: string; title: string }[];
  onClose: () => void; onLinkedChanged: (items: WorkAgent[]) => void; onRevision: (revision: number) => void;
};
export default function WorkAgents(props: WorkAgentsProps) {
  return <WorkAgentsBody key={`${cacheFor(props.client).id}:${props.workId}`} {...props} />;
}
function WorkAgentsBody({ client, workId, revision, linked, tasks = [], initialAgentId, visible = true, onClose, onLinkedChanged, onRevision }: WorkAgentsProps) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const linkedPath = `/api/v1/works/${workId}/agents?view=links`, workPath = `/api/v1/works/${workId}`;
  const detectedPath = '/api/v1/agents', sourcesPath = '/api/v1/discovery/sources';
  const [items, setItems] = useState(() => enrichLinked(knownItems<WorkAgent>(client, linkedPath) ?? linked, knownItems<WorkAgent>(client, detectedPath) ?? []));
  const [candidates, setCandidates] = useState<WorkAgent[]>(() => knownItems<WorkAgent>(client, detectedPath) ?? []);
  const [programs, setPrograms] = useState<DiscoveryApplication[]>([]);
  const [programsFailed, setProgramsFailed] = useState(false);
  const [ready, setReady] = useState({ linked: false, work: false, detected: false, sources: false });
  const [selected, setSelected] = useState<string | undefined>(initialAgentId);
  const [currentRevision, setCurrentRevision] = useState(revision);
  const [observing, setObserving] = useState(false);
  const lastReadFailed = useRef(false);
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [discoveryState, setDiscoveryState] = useState('UNKNOWN');
  const [sources, setSources] = useState<DiscoverySource[]>(() => knownItems<DiscoverySource>(client, sourcesPath) ?? []);
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
  const loadSequence = useRef(0);
  const revisionRef = useRef(revision);
  const callbacks = useRef({ onLinkedChanged, onRevision, onClose }); callbacks.current = { onLinkedChanged, onRevision, onClose };
  const pending = useRef<{ agentId: string; detach: boolean; path: string; payload: object } | null>(null);
  const failed = zh ? '未能读取 Agent 记录，请重试。' : 'Could not load Agent records. Retry.';
  useEffect(() => { setCurrentRevision(revision); revisionRef.current = revision; }, [revision]);
  useEffect(() => { live.current = true; return () => { live.current = false; loadSequence.current++; }; }, []);
  async function load(refresh = false, quiet = false, force = false) {
    if (loadInFlight.current && !refresh && !force) return loadInFlight.current;
    let readFailed = false;
    let currentDetected: WorkAgent[] | undefined;
    const generation = ++loadSequence.current;
    const current = () => live.current && generation === loadSequence.current;
    if (!quiet) { setLoading(true); setError(''); }
    if (force || refresh) {
      for (const path of [linkedPath, workPath, detectedPath, sourcesPath]) invalidateRead(client, path);
      setReady({ linked: false, work: false, detected: false, sources: false });
    }
    const receive = async <T,>(path: string, apply: (value: T) => void, kind: keyof typeof ready) => {
      try {
        const value = await readAgentsOnce<T>(client, path);
        if (!current()) return;
        apply(value); readEntry(client, path).value = value;
        setReady(previous => ({ ...previous, [kind]: true }));
      } catch {
        if (!current()) return;
        readFailed = true;
        setReady(previous => ({ ...previous, [kind]: false }));
        if (kind === 'sources') setSourceError(zh ? '未能读取观察来源。' : 'Could not load observation sources.');
        else { if (kind === 'detected') setDiscoveryState('UNAVAILABLE'); setError(failed); }
      }
    };
    const request = (async () => {
      try {
        if (refresh) await refreshDiscovery(client);
        if (!current()) return;
        // Optional observations publish independently; no timeout invents an
        // empty result, and slow sources never hold the linked Work records.
        setObserving(true);
        const detected = receive<{ items: WorkAgent[]; status: string }>(detectedPath, value => {
          if (!Array.isArray(value.items) || typeof value.status !== 'string') throw new Error('Missing discovery list');
          currentDetected = value.items;
          setCandidates(value.items.filter(agent => Boolean(agent.agent_id))); setDiscoveryState(value.status);
          setItems(previous => enrichLinked(previous, value.items, true));
        }, 'detected').finally(() => { if (current()) setObserving(false); });
        const sourceRead = receive<DiscoverySources>(sourcesPath, value => {
          if (!Array.isArray(value.items)) throw new Error('Missing sources');
          setSources(value.items); setInventoryCoverage(value.inventory_coverage ?? []); setSourceError('');
          if (!quiet) setCatalogRefreshKey(previous => previous + 1);
        }, 'sources');
        await Promise.all([
          receive<{ items: WorkAgent[] }>(linkedPath, value => {
            if (!Array.isArray(value.items)) throw new Error('Missing associations');
            const linkedItems = enrichLinked(value.items, currentDetected ?? knownItems<WorkAgent>(client, detectedPath) ?? [], currentDetected !== undefined);
            setItems(linkedItems);
            // A lost mutation reply is reconciled by fresh persisted relation
            // reads, never by silently resending the mutation with a new UUID.
            const uncertain = pending.current;
            if (uncertain && linkedItems.some(item => item.agent_id === uncertain.agentId) !== uncertain.detach) {
              pending.current = null; setError('');
            }
            const signature = JSON.stringify(linkedItems);
            if (signature !== linkedSignature.current) { linkedSignature.current = signature; callbacks.current.onLinkedChanged(linkedItems); }
          }, 'linked'),
          receive<{ revision: number }>(workPath, value => {
            if (!Number.isSafeInteger(value.revision) || value.revision < 1) throw new Error('Missing work revision');
            if (value.revision >= revisionRef.current) {
              setCurrentRevision(value.revision);
              if (value.revision !== revisionRef.current) { revisionRef.current = value.revision; callbacks.current.onRevision(value.revision); }
            }
          }, 'work'),
        ]);
        // Metadata can be used immediately. Retain the batch's in-flight guard
        // until optional observations settle so polling cannot spawn duplicates.
        if (current()) setLoading(false);
        await Promise.all([detected, sourceRead]);
      } catch { readFailed = true; if (current()) setError(failed); }
      finally { if (current()) { setLoading(false); lastReadFailed.current = readFailed; } }
    })();
    loadInFlight.current = request;
    try { await request; } finally { if (loadInFlight.current === request) loadInFlight.current = null; }
  }
  useEffect(() => { void load(); }, [client, workId]);
  useEffect(() => {
    let active = true;
    const path = '/api/v1/discovery/applications?limit=100&offset=0';
    void readAgentsOnce<{ items: DiscoveryApplication[] }>(client, path).then(value => {
      if (!Array.isArray(value.items)) throw new Error('Missing program inventory');
      if (active) { setPrograms(value.items); setProgramsFailed(false); }
    }).catch(() => { if (active) setProgramsFailed(true); });
    return () => { active = false; };
  }, [client, workId, catalogRefreshKey]);

  useEffect(() => {
    if (!visible) return;
    let stopped = false, failures = 0;
    let timer: ReturnType<typeof window.setTimeout>;
    const poll = async () => {
      if (document.visibilityState === 'visible') {
        await load(false, true);
        failures = lastReadFailed.current ? Math.min(failures + 1, 4) : 0;
      }
      if (!stopped) timer = window.setTimeout(poll, failures ? Math.min(30_000, 4_000 * 2 ** failures) : DISCOVERY_POLL_INTERVAL_MS);
    };
    timer = window.setTimeout(poll, DISCOVERY_POLL_INTERVAL_MS);
    return () => { stopped = true; window.clearTimeout(timer); };
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
    if (!ready.linked || !ready.work || !detach && !ready.detected) return;
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
      pending.current = null; setCurrentRevision(value.revision); revisionRef.current = value.revision; callbacks.current.onRevision(value.revision);
      await load(false, false, true);
    } catch (err) {
      if (!live.current) return;
      const conflict = err instanceof ApiActionError && err.reasonCode === 'WORK_REVISION_CONFLICT';
      if (conflict) pending.current = null;
      setError(conflict ? (zh ? '工作已在别处变更。请刷新记录后重试。' : 'This work changed elsewhere. Reload records before retrying.') :
        (zh ? '关联回执尚未收到，正在核对已保存记录；也可重试同一操作。' : 'The reply was not received. Checking saved links; you can also retry the same action.'));
      if (!conflict) await load(false, true, true);
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
    if (!agent || busy || !ready.linked || !ready.work) return;
    setBusy(true); setError('');
    const signature = JSON.stringify([agent.agent_id, [...taskIds].sort()]);
    if (pendingTasks.current?.signature !== signature) pendingTasks.current = { signature,
      payload: { request_id: crypto.randomUUID(), expected_revision: currentRevision, task_ids: taskIds } };
    try {
      const result = await client.post<{ revision: number }>(`/api/v1/works/${workId}/agents/${encodeURIComponent(agent.agent_id)}/tasks`, pendingTasks.current.payload);
      if (!live.current) return;
      pendingTasks.current = null; setCurrentRevision(result.revision); revisionRef.current = result.revision; callbacks.current.onRevision(result.revision); await load(false, false, true);
    } catch (err) {
      if (!live.current) return;
      if (err instanceof ApiActionError && err.reasonCode === 'WORK_REVISION_CONFLICT') {
        pendingTasks.current = null;
        setError(zh ? '工作已变更，请刷新记录后重新选择任务。' : 'This work changed. Reload records and select the tasks again.');
      } else setError(zh ? '任务关联未确认，请重试保存。' : 'Task links were not confirmed. Retry saving.');
    } finally { if (live.current) setBusy(false); }
  }
  async function configureSource(source: DiscoverySource, enabled: boolean) {
    if (sourceBusy || !source.configurable || !ready.sources) return;
    setSourceBusy(source.source_id); setSourceError('');
    try {
      await client.post(`/api/v1/discovery/sources/${encodeURIComponent(source.source_id)}/configure`,
        { enabled, expected_revision: source.revision });
      if (live.current) await load(false, false, true);
    } catch (err) {
      if (!live.current) return;
      const conflict = err instanceof ApiActionError && err.reasonCode === 'DISCOVERY_SOURCE_REVISION_CONFLICT';
      setSourceError(conflict ? (zh ? '来源设置已变更，请刷新发现后重试。' : 'Source settings changed. Refresh discovery before retrying.') :
        (zh ? '未能更新观察来源。' : 'Could not update observation source.'));
    } finally { if (live.current) setSourceBusy(null); }
  }
  const available = candidates.filter(item => !associatedIds.has(item.agent_id));
  const canLink = (item: WorkAgent) => ready.detected && item.presence === 'CURRENT' && item.lifecycle !== 'UNKNOWN';
  const name = (item: WorkAgent) => agentIdentityLabel(item, zh);
  const state = (item: WorkAgent) => !(associatedIds.has(item.agent_id) ? ready.linked : ready.detected) ?
    (zh ? '上次已知观测 · 当前正在核对' : 'Last known observation · awaiting refresh') : item.presence === 'NOT_CURRENT' ? (zh ? '本次未观测到 · 活动未知' : 'Not in current observation · activity unknown') :
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
      <AgentProgramIcon item={item} /><span className="work-agent-copy"><strong><AgentIdentityLabel item={item} /></strong>
      <span className="work-agent-context">{agentSourceLabel(item, zh)}</span>
      <span className="work-agent-observation"><small>{state(item)}</small>
        <time dateTime={item.observed_at || undefined}>{item.observed_at ? new Date(item.observed_at).toLocaleString(locale) : (zh ? '观测时间未知' : 'Observation time unknown')}</time></span></span>
    </button>
    <button disabled={busy || loading || !ready.linked || !ready.work || (!associated && !canLink(item))} onClick={() => void associate(item.agent_id, associated)}>{associated ? (zh ? '解除关联' : 'Unlink') : (zh ? '关联到此工作' : 'Link to this work')}</button>
  </article>;
  const groupedRows = (values: WorkAgent[], associated: boolean) => {
    const groups = new Map<string, WorkAgent[]>();
    for (const item of values) {
      const path = item.program_path?.replace(/\\/g, '/');
      const location = path && /^[a-z]:\//i.test(path) ? path.toLowerCase() : path;
      const key = `${item.source_id ?? ''}:${location || item.application_id || item.agent_id}`;
      groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    return [...groups.entries()].map(([key, members]) => members.length === 1 ? row(members[0], associated) :
      <details className="work-agent-group" key={key}>
        <summary><AgentProgramIcon item={members[0]} /><span className="work-agent-copy"><strong><AgentIdentityLabel item={members[0]} /></strong>
          <span className="work-agent-context">{members[0].source_id === 'local' ? (zh ? '本机' : 'This computer') : members[0].source_label} · {members.length} {zh ? '条进程观测' : 'process observations'}</span></span>
          <span className="work-agent-group-action">{zh ? '选择关联进程' : 'Choose an instance'}</span></summary>
        <div className="work-agent-instances">{members.map(item => row(item, associated))}</div>
      </details>);
  };
  const installedOnly = programs.filter(program => program.status === 'AVAILABLE' && !program.running && program.classification !== 'UNCLASSIFIED');
  return <section ref={root} className="work-agents-layer" role="dialog" aria-modal="true" aria-label={zh ? '实际 Agent' : 'Actual Agents'}>
    <header><button autoFocus onClick={leave}>← {zh ? '返回' : 'Back'}</button><h2>{agent ? name(agent) : (zh ? '实际 Agent' : 'Actual Agents')}</h2>
      {!showEvidence && <button disabled={busy || loading || observing || Boolean(sourceBusy)} onClick={() => void load(true)}>{zh ? '刷新发现' : 'Refresh discovery'}</button>}
    </header>
    <div className="work-agents-content">
      {loading && <div className="work-agent-loading" role="status"><span className="work-loading-spinner" aria-hidden="true" />{zh ? '正在读取 Agent 记录…' : 'Reading Agent records…'}</div>}
      {error && <p role="alert">{error} <button disabled={busy || loading} onClick={() => void load()}>{zh ? '刷新记录' : 'Reload records'}</button></p>}
      {showEvidence ? <EvidenceDetailPanel detail={evidence} phase={evidencePhase} /> : agent ? <>
        <p className="work-agent-state">{state(agent)}</p>
        <dl className="work-agent-facts"><div><dt>{zh ? '此工作中的关系' : 'Relationship to this work'}</dt><dd>{associatedIds.has(agent.agent_id) ? (zh ? '用户关联' : 'User linked') : (zh ? '尚未关联' : 'Not linked')}</dd></div>
          <div><dt>{zh ? '执行事实' : 'Execution facts'}</dt><dd>{zh ? '尚无执行此工作或子任务的可验证记录。' : 'No verified execution record for this work or its tasks.'}</dd></div>
          <div><dt>{zh ? '最近观测' : 'Last observed'}</dt><dd>{agent.observed_at ? new Date(agent.observed_at).toLocaleString(locale) : '—'}</dd></div>
        </dl>
        <button disabled={busy || loading || !ready.linked || !ready.work || (!associatedIds.has(agent.agent_id) && !canLink(agent))} onClick={() => void associate(agent.agent_id, associatedIds.has(agent.agent_id))}>{associatedIds.has(agent.agent_id) ? (zh ? '解除关联' : 'Unlink') : (zh ? '关联到此工作' : 'Link to this work')}</button>
        {associatedIds.has(agent.agent_id) && <fieldset className="work-agent-tasks"><legend>{zh ? '关联任务' : 'Linked tasks'}</legend>
          {!tasks.length && <p>{zh ? '这项工作还没有任务。' : 'This work has no tasks yet.'}</p>}
          {tasks.map(task => <label key={task.id}><input type="checkbox" disabled={busy || loading || !ready.linked || !ready.work} checked={taskIds.includes(task.id)} onChange={event => setTaskIds(ids => event.target.checked ? [...ids, task.id] : ids.filter(id => id !== task.id))} />{task.title}</label>)}
          {!!tasks.length && <button disabled={busy || loading || !ready.linked || !ready.work} onClick={() => void saveTasks()}>{zh ? '保存任务关联' : 'Save task links'}</button>}
        </fieldset>}
        <AgentIdentityDetails item={agent}>
          <p>{agent.lifecycle} · {agent.role} · {agent.reason_code}</p>
          <p>{zh ? '工作目录关联' : 'Workspace binding'}: {agent.workspace?.status ?? 'UNKNOWN'} · {agent.workspace?.binding_ref ?? '—'}</p>
          {agent.evidence_refs.map(ref => <button key={ref} onClick={() => void openEvidence(ref)}>{zh ? '查看证据' : 'View evidence'} · {ref.slice(0, 12)}</button>)}
        </AgentIdentityDetails>
      </> : <>
        <p className="work-agent-intro">{zh ? '选择当前观测到的 Agent 关联到这项工作。关联本身不会启动执行。' : 'Link a currently observed Agent to this work. Linking does not start execution.'}</p>
        <h3>{zh ? '已关联' : 'Linked'} · {items.length}</h3>
        {items.length ? groupedRows(items, true) : !loading && ready.linked && <p>{zh ? '这项工作还没有关联实际 Agent。' : 'No actual Agents are linked to this work yet.'}</p>}
        <h3>{zh ? '发现的 Agent' : 'Discovered agents'}</h3>
        {groupedRows(available, false)}
        {!loading && ready.detected && !available.length && <p>{zh ? '没有其他可关联的 Agent。可刷新发现。' : 'No other Agents available. Refresh discovery to check again.'}</p>}
        {(!ready.detected || discoveryState === 'UNKNOWN' || discoveryState === 'DEGRADED' || discoveryState === 'UNAVAILABLE') && <p>{zh ? '观测不完整，不能据此判断 Agent 不存在。' : 'Observation is incomplete; this does not establish absence.'}</p>}
        {!!installedOnly.length && <section className="work-installed-programs"><h3>{zh ? '其他已安装工具' : 'Other installed tools'}</h3>
          {installedOnly.map(program => <div className="work-agent-row" key={program.id}>
            <div className="work-installed-identity"><AgentProgramIcon item={{ detected_identity: program.provider, program_path: program.path }} /><span className="work-agent-copy">
              <strong>{agentIdentityLabel({ detected_identity: program.provider, program_path: program.path })}</strong>
              <span className="work-agent-context">{zh ? '已安装，当前未观测到进程。启动后刷新发现。' : 'Installed; no process currently observed. Open the app, then refresh discovery.'}</span></span></div>
          </div>)}
        </section>}
        {programsFailed && <p className="work-agent-context">{zh ? '已安装工具读取失败；运行进程观测单独显示。' : 'Installed tools could not be read; process observations are shown separately.'}</p>}
        <details className="work-agent-sources"><summary>{zh ? '观察来源' : 'Observation sources'} · {sources.length}</summary>
          {sourceError && <p role="alert">{sourceError}</p>}
          {sources.map(source => <div className="work-agent-source-row" key={source.source_id}>
            <div><strong>{source.label}{source.short_id && <span className="work-agent-source-id"> · {source.short_id}</span>}</strong>
              <small>{sourceState(source.state)} · {source.agent_count} {zh ? '个 Agent' : 'agents'}</small></div>
            {source.configurable && <label><input type="checkbox" checked={source.enabled} disabled={loading || !ready.sources || Boolean(sourceBusy)}
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
