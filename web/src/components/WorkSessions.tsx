import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiActionError, ApiRequestError, SessionUnavailableError, type ApiClient } from '../api/client';
import {
  executionBase, normalizeRun, normalizeSession, readProtectionVersions, readRunEvents, runIsActive,
  type ChangeReference, type ExecutionPage, type ExecutionRun, type ProtectionScope,
  type RunEvent, type WorkSession,
} from '../api/execution';
import type { DiscoveryApplication, DiscoveryPage, DiscoverySession, DiscoverySessionStore, DiscoverySessionsPage } from '../api/types';
import { useI18n } from '../i18n/I18nProvider';
import './WorkSessions.css';
import WorkRunContext, { type RunContextChoice } from './WorkRunContext';
import type { ExecutionTarget } from './WorkExecutionStatus';

type PendingCommand = { signature: string; payload: Record<string, unknown> };
export type WorkSessionMemory = {
  drafts: Map<string, string>;
  commands: Map<string, PendingCommand>;
  contexts: Map<string, RunContextChoice[]>;
  requirements: Map<string, number>;
};
export const createWorkSessionMemory = (): WorkSessionMemory => ({ drafts: new Map(), commands: new Map(), contexts: new Map(), requirements: new Map() });

type Props = {
  client: ApiClient; workId: string; revision: number; requirementVersion: number;
  tasks: { id: string; title: string }[]; visible?: boolean; memory: WorkSessionMemory;
  onClose: () => void; onRevision: (revision: number) => void;
  onOpenRequirement: (version: number) => void;
  onDelivery?: (output: WorkRunOutput) => void;
  target?: ExecutionTarget | null;
  artifacts?: { id: string; title: string; version: number }[];
};

export type WorkRunOutput = { sessionId: string; run: ExecutionRun; events: RunEvent[]; omitted: number };
type EventHistory = { after: number; events: RunEvent[]; omitted: number };
type CreateDraft = { application_id: string; store_id: string; scope_id: string; task_id: string;
  mode: 'NEW' | 'CONTINUE'; binding_id: string; read_only: boolean; control_consent: boolean };
const emptyCreate: CreateDraft = { application_id: '', store_id: '', scope_id: '', task_id: '',
  mode: 'NEW', binding_id: '', read_only: true, control_consent: false };
type Question = { id: string; header: string; question: string; secret: boolean;
  options: { label: string; description: string }[] };
const displayCode = (value: unknown) => typeof value === 'string' && /^[A-Z0-9_]{1,64}$/.test(value) ? value : '';
const eventText = (event: { payload: Record<string, unknown> }) =>
  typeof event.payload.text === 'string' ? event.payload.text :
    typeof event.payload.question === 'string' ? event.payload.question :
      typeof event.payload.summary === 'string' ? event.payload.summary :
        typeof event.payload.title === 'string' ? event.payload.title : '';
function questionsFor(payload: Record<string, unknown>): Question[] {
  if (!Array.isArray(payload.questions)) return [];
  return payload.questions.filter((item): item is Question => Boolean(item && typeof item === 'object' &&
    typeof item.id === 'string' && typeof item.question === 'string')).map(item => ({ ...item,
    secret: item.secret === true, options: Array.isArray(item.options) ? item.options.filter(option => typeof option?.label === 'string') : [] }));
}

export default function WorkSessions({ client, workId, revision, requirementVersion, tasks, visible = true,
  memory, onClose, onRevision, onOpenRequirement, onDelivery, target, artifacts = [] }: Props) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const label = (en: string, cn: string) => zh ? cn : en;
  const [items, setItems] = useState<WorkSession[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [session, setSession] = useState<WorkSession | null>(null);
  const [runs, setRuns] = useState<ExecutionRun[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<ExecutionRun | null>(null);
  const [history, setHistory] = useState<EventHistory>({ after: 0, events: [], omitted: 0 });
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false); const busyRef = useRef(false);
  const [error, setError] = useState(''); const [streamState, setStreamState] = useState('');
  const [refresh, setRefresh] = useState(0); const [hasMore, setHasMore] = useState(false);
  const [runsMore, setRunsMore] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [creation, setCreation] = useState<CreateDraft>(emptyCreate);
  const [applications, setApplications] = useState<DiscoveryApplication[]>([]);
  const [stores, setStores] = useState<DiscoverySessionStore[]>([]);
  const [scopes, setScopes] = useState<ProtectionScope[]>([]);
  const [bindings, setBindings] = useState<DiscoverySession[]>([]);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [appMore, setAppMore] = useState(false);
  const [storeMore, setStoreMore] = useState(false);
  const [bindingMore, setBindingMore] = useState(false);
  const [input, setInput] = useState(''); const [answers, setAnswers] = useState<Record<string, string>>({});
  const [contexts, setContexts] = useState<RunContextChoice[]>([]);
  const [inputRequirement, setInputRequirement] = useState(requirementVersion);
  const [diff, setDiff] = useState<{ title: string; body: string; error: string; loading: boolean; truncated?: boolean } | null>(null);
  const [observedChanges, setObservedChanges] = useState<ChangeReference[]>([]);
  const [changesBusy, setChangesBusy] = useState(false); const [changesError, setChangesError] = useState('');
  const [changesLoaded, setChangesLoaded] = useState(false);
  const root = useRef<HTMLElement>(null); const live = useRef(true);
  const loadSequence = useRef(0); const diffSequence = useRef(0);
  const changesSequence = useRef(0);
  const histories = useRef(new Map<string, EventHistory>());
  const revealedRun = useRef<string | null>(null);
  const revealedAction = useRef<string | null>(null);
  const openedTarget = useRef<ExecutionTarget | null>(null);
  const rememberedRuns = useRef(new Map<string, string>());
  const selection = useRef({ selected, runId }); selection.current = { selected, runId };
  const currentRevision = useRef(revision); currentRevision.current = revision;
  const creationRef = useRef(creation); creationRef.current = creation;
  const catalogOffsets = useRef({ applications: 0, stores: 0, bindings: 0 });
  const callbacks = useRef({ onClose, onRevision, onOpenRequirement });
  callbacks.current = { onClose, onRevision, onOpenRequirement };
  const draftKey = `${workId}:${selected ?? ''}`;
  const safeError = (err: unknown) => {
    const reason = err instanceof ApiActionError || err instanceof ApiRequestError ? displayCode(err.reasonCode) : '';
    return `${label('The action was not confirmed. Retry the same action.', '操作尚未确认，可重试同一操作。')}${reason ? ` ${reason}` : ''}`;
  };
  const statusLabel = (status: string) => ({
    CREATING: label('Preparing session', '正在准备会话'), READY: label('Ready', '可用'),
    CONNECTING: label('Connecting', '正在连接'), DISCONNECTED: label('Disconnected', '已断开'),
    UNAVAILABLE: label('Unavailable', '不可用'), ARCHIVED: label('Archived', '已归档'),
    PREPARING: label('Preparing', '正在准备'), RUNNING: label('Running', '执行中'),
    QUEUED: label('Queued', '排队中'), DISPATCHING: label('Dispatching', '正在发送'),
    FINALIZING: label('Collecting file results', '正在收集文件结果'), INTERRUPTED: label('Interrupted', '已中断'),
    WAITING_APPROVAL: label('Needs approval', '等待审批'), WAITING_INPUT: label('Needs input', '等待输入'),
    CANCELLING: label('Stopping', '正在停止'), COMPLETED: label('Execution ended', '执行已结束'),
    FAILED: label('Failed', '执行失败'), CANCELLED: label('Stopped', '已停止'), UNCERTAIN: label('State unknown', '状态待确认'),
  }[status] ?? label('State unknown', '状态待确认'));
  const eventBody = (event: RunEvent) => {
    const text = eventText(event); if (text) return text;
    const tool = ({ commandExecution: label('Command execution', '命令执行'), fileChange: label('File change', '文件修改'),
      mcpToolCall: label('External tool', '外部工具'), dynamicToolCall: label('Tool call', '工具调用'),
      webSearch: label('Web search', '网页检索') } as Record<string, string>)[String(event.payload.item_type)];
    const status = ({ inProgress: label('Running', '执行中'), completed: label('Completed', '已完成'),
      failed: label('Failed', '失败'), declined: label('Declined', '已拒绝') } as Record<string, string>)[String(event.payload.status)];
    if (tool) return [tool, status].filter(Boolean).join(' · ');
    return displayCode(event.payload.reason_code ?? event.payload.status) ||
      (event.kind === 'OUTPUT_TRUNCATED' ? label('Output truncated', '输出已截断') : '');
  };

  useEffect(() => {
    if (!visible) { revealedAction.current = null; return; }
    const action = run?.pending_responses[0];
    const id = action && `${run!.id}:${action.action_ref}`;
    if (!id) { revealedAction.current = null; return; }
    if (id === revealedAction.current) return;
    const editing = root.current?.contains(document.activeElement) && document.activeElement?.matches('input,textarea,select');
    if (!editing) {
      root.current?.querySelector('.work-run-response')?.scrollIntoView?.({ block: 'nearest' });
      revealedAction.current = id;
    }
  }, [visible, run?.id, run?.pending_responses]);

  useEffect(() => {
    if (!visible) { revealedRun.current = null; return; }
    if (run?.pending_responses.length) return;
    if (run && revealedRun.current !== run.id && history.events.some(event => event.kind === 'TEXT')) {
      root.current?.querySelector('.work-run-events')?.scrollIntoView?.({ block: 'nearest' });
      revealedRun.current = run.id;
    }
  }, [visible, run?.id, history.events]);

  useEffect(() => { live.current = true; return () => { live.current = false; loadSequence.current++; diffSequence.current++; changesSequence.current++; }; }, []);
  useEffect(() => {
    if (!visible) return;
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); callbacks.current.onClose(); }
    };
    document.addEventListener('keydown', keyboard);
    return () => document.removeEventListener('keydown', keyboard);
  }, [visible]);

  const loadSessions = useCallback(async (append = false) => {
    const sequence = ++loadSequence.current;
    setLoading(true); setError('');
    try {
      const page = await client.get<ExecutionPage<unknown>>(`${executionBase(workId)}/sessions?limit=50&offset=${append ? items.length : 0}`);
      const next = page.items.map(normalizeSession);
      if (next.some(item => item.work_id && item.work_id !== workId)) throw new ApiRequestError('Invalid session record');
      if (!live.current || sequence !== loadSequence.current) return;
      setItems(old => append ? [...old, ...next.filter(item => !old.some(previous => previous.id === item.id))] : next);
      setHasMore(page.has_more); setLoading(false);
    } catch (err) { if (live.current && sequence === loadSequence.current) { setError(safeError(err)); setLoading(false); } }
  }, [client, workId, items.length, zh]);
  useEffect(() => {
    if (visible) void loadSessions();
    return () => { loadSequence.current++; };
  }, [client, workId, visible, refresh]);

  useEffect(() => {
    setInput(memory.drafts.get(draftKey) ?? ''); setAnswers({}); setDiff(null); diffSequence.current++;
    setContexts(memory.contexts.get(draftKey) ?? []);
    const version = memory.requirements.get(draftKey) ?? requirementVersion;
    memory.requirements.set(draftKey, version); setInputRequirement(version);
  }, [draftKey, memory]);
  useEffect(() => {
    if (visible && target && target !== openedTarget.current) { openedTarget.current = target; rememberedRuns.current.set(target.sessionId, target.runId); setSelected(target.sessionId); setRunId(target.runId); }
  }, [target, visible]);
  useEffect(() => {
    if (!visible || !selected) return;
    let active = true; setLoading(true); setError(''); setSession(null); setRuns([]); setRun(null);
    const load = async () => {
      try {
        const [detail, page] = await Promise.all([
          client.get<unknown>(`${executionBase(workId)}/sessions/${encodeURIComponent(selected)}`),
          client.get<ExecutionPage<unknown>>(`${executionBase(workId)}/sessions/${encodeURIComponent(selected)}/runs?limit=50&offset=0`),
        ]);
        const next = normalizeSession(detail), executions = page.items.map(normalizeRun);
        if (next.id !== selected || (next.work_id && next.work_id !== workId) || executions.some(item => item.session_id !== selected)) {
          throw new ApiRequestError('Invalid execution record');
        }
        if (!active || !live.current) return;
        setSession(next); setRuns(executions); setRunsMore(page.has_more);
        const remembered = rememberedRuns.current.get(selected);
        setRunId(remembered ?? executions[0]?.id ?? null);
      } catch (err) { if (active && live.current) setError(safeError(err)); }
      finally { if (active && live.current) setLoading(false); }
    };
    void load(); return () => { active = false; };
  }, [client, workId, selected, visible, refresh]);

  useEffect(() => {
    if (!visible || !selected || !session || !['CREATING', 'CONNECTING'].includes(session.status)) return;
    let active = true;
    const timer = setTimeout(() => {
      void client.get<unknown>(`${executionBase(workId)}/sessions/${encodeURIComponent(selected)}`).then(value => {
        const next = normalizeSession(value);
        if (next.id !== selected || next.work_id && next.work_id !== workId) throw new ApiRequestError('Invalid session record');
        if (active && live.current) { setSession(next); setItems(old => old.map(item => item.id === next.id ? next : item)); }
      }).catch(err => { if (active && live.current) setError(safeError(err)); });
    }, 2000);
    return () => { active = false; clearTimeout(timer); };
  }, [client, workId, selected, session, visible]);

  useEffect(() => {
    if (!visible || !selected || !runId || !session) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    const cacheKey = `${workId}:${runId}`;
    const current = histories.current.get(cacheKey) ?? { after: 0, events: [], omitted: 0 };
    histories.current.set(cacheKey, current); setHistory({ ...current }); setRun(null); setDiff(null); diffSequence.current++;
    let stalled = false, failures = 0, detailInFlight = false, detailQueued = false;
    const refreshDetail = async () => {
      if (detailInFlight) { detailQueued = true; return; }
      detailInFlight = true;
      try {
        do {
          detailQueued = false;
          const detail = normalizeRun(await client.get<unknown>(`${executionBase(workId)}/runs/${encodeURIComponent(runId)}`));
          if (controller.signal.aborted || !live.current) return;
          if (detail.id !== runId || detail.session_id !== selected) throw new ApiRequestError('Invalid execution record');
          setRun(detail); setRuns(old => old.map(item => item.id === detail.id ? detail : item));
        } while (detailQueued && !controller.signal.aborted);
      } catch (err) {
        if (!controller.signal.aborted && live.current) setError(safeError(err));
      } finally { detailInFlight = false; }
    };
    const monitor = async () => {
      try {
        const detail = normalizeRun(await client.get<unknown>(`${executionBase(workId)}/runs/${encodeURIComponent(runId)}`));
        if (controller.signal.aborted || !live.current) return;
        if (detail.id !== runId || detail.session_id !== selected) throw new ApiRequestError('Invalid execution record');
        setRun(detail); setRuns(old => old.map(item => item.id === detail.id ? detail : item));
        if (session.capabilities.stream && client.stream) {
          setStreamState(stalled ? 'RECONNECTING' : 'CONNECTED');
          await readRunEvents(client, workId, runId, current.after, event => {
            if (controller.signal.aborted || !live.current || event.seq <= current.after) return;
            current.after = event.seq; current.events.push(event);
            if (current.events.length > 500) { current.events.shift(); current.omitted++; }
            setHistory({ ...current, events: [...current.events] });
            if (['APPROVAL', 'QUESTION', 'STATUS', 'FILE_CHANGE'].includes(event.kind)) void refreshDetail();
          }, controller.signal);
          if (controller.signal.aborted) return;
          stalled = false; failures = 0;
        } else setStreamState('UNAVAILABLE');
        if (runIsActive(detail.status) || (session.capabilities.stream && current.after < detail.last_seq)) {
          timer = setTimeout(() => void monitor(), current.after < detail.last_seq ? 0 : 2000);
        } else setStreamState('CURRENT');
      } catch (err) {
        if (controller.signal.aborted || !live.current) return;
        stalled = true; setStreamState('DISCONNECTED');
        if (err instanceof SessionUnavailableError || err instanceof ApiRequestError &&
          (err.status !== null && err.status < 500 || err.status === null && err.message !== 'API unreachable')) setError(safeError(err));
        else { failures++; timer = setTimeout(() => void monitor(), Math.min(30000, 2000 * 2 ** Math.min(failures, 4))); }
      }
    };
    void monitor();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [client, workId, selected, runId, session?.id, session?.capabilities.stream, visible, refresh]);
  useEffect(() => {
    changesSequence.current++; setObservedChanges([]); setChangesBusy(false); setChangesError(''); setChangesLoaded(false);
  }, [runId, run?.file_result?.baseline_id, run?.file_result?.version_id]);

  const application = applications.find(item => item.id === creation.application_id);
  const store = stores.find(item => item.id === creation.store_id);
  const scope = scopes.find(item => item.id === creation.scope_id);
  useEffect(() => {
    if (!visible || !createOpen) return;
    let active = true; setCatalogBusy(true); setCatalogError('');
    void Promise.all([
      client.get<DiscoveryPage<DiscoveryApplication>>('/api/v1/discovery/applications?limit=50&offset=0'),
      client.get<{ items: ProtectionScope[] }>('/api/v1/protection/scopes'),
    ]).then(([apps, protection]) => {
      if (!active || !live.current) return;
      setApplications(apps.items); setScopes(protection.items); setAppMore(apps.has_more);
      catalogOffsets.current.applications = apps.next_offset ?? apps.items.length;
    }).catch(err => { if (active && live.current) setCatalogError(safeError(err)); })
      .finally(() => { if (active && live.current) setCatalogBusy(false); });
    return () => { active = false; };
  }, [client, visible, createOpen, refresh]);
  useEffect(() => {
    setStores([]); setBindings([]); setStoreMore(false); setBindingMore(false);
    catalogOffsets.current.stores = 0; catalogOffsets.current.bindings = 0;
    if (!visible || !createOpen || !application) return;
    let active = true; setCatalogBusy(true);
    void client.get<DiscoveryPage<DiscoverySessionStore>>(`/api/v1/discovery/sources/${encodeURIComponent(application.source_id)}/session-stores?limit=50&offset=0`)
      .then(page => { if (active && live.current) { setStores(page.items.filter(item => item.provider === application.provider)); setStoreMore(page.has_more); catalogOffsets.current.stores = page.next_offset ?? page.items.length; } })
      .catch(err => { if (active && live.current) setCatalogError(safeError(err)); })
      .finally(() => { if (active && live.current) setCatalogBusy(false); });
    return () => { active = false; };
  }, [client, visible, createOpen, application?.id, application?.source_id, application?.provider, refresh]);
  useEffect(() => {
    setBindings([]); setBindingMore(false);
    catalogOffsets.current.bindings = 0;
    if (!visible || !createOpen || creation.mode !== 'CONTINUE' || !application || !store) return;
    let active = true; setCatalogBusy(true);
    void client.get<DiscoverySessionsPage>(`/api/v1/discovery/sources/${encodeURIComponent(application.source_id)}/sessions?store_id=${encodeURIComponent(store.id)}&limit=50&offset=0`)
      .then(page => { if (active && live.current) { setBindings(page.sessions.filter(item => item.binding_id && (!item.store_id || item.store_id === store.id))); setBindingMore(page.has_more); catalogOffsets.current.bindings = page.next_offset ?? page.sessions.length; } })
      .catch(err => { if (active && live.current) setCatalogError(safeError(err)); })
      .finally(() => { if (active && live.current) setCatalogBusy(false); });
    return () => { active = false; };
  }, [client, visible, createOpen, application?.source_id, store?.id, creation.mode, refresh]);

  async function loadMoreCatalog(kind: 'applications' | 'stores' | 'bindings') {
    if (catalogBusy || kind !== 'applications' && !application || kind === 'bindings' && !store) return;
    const selectedApp = application?.id, selectedStore = store?.id;
    const offset = catalogOffsets.current[kind];
    setCatalogBusy(true); setCatalogError('');
    try {
      if (kind === 'applications') {
        const page = await client.get<DiscoveryPage<DiscoveryApplication>>(`/api/v1/discovery/applications?limit=50&offset=${offset}`);
        if (live.current) { setApplications(old => [...old, ...page.items.filter(item => !old.some(previous => previous.id === item.id))]); setAppMore(page.has_more); catalogOffsets.current.applications = page.next_offset ?? offset + page.items.length; }
      } else if (kind === 'stores') {
        const page = await client.get<DiscoveryPage<DiscoverySessionStore>>(`/api/v1/discovery/sources/${encodeURIComponent(application!.source_id)}/session-stores?limit=50&offset=${offset}`);
        if (live.current && creationRef.current.application_id === selectedApp) {
          setStores(old => [...old, ...page.items.filter(item => item.provider === application!.provider && !old.some(previous => previous.id === item.id))]); setStoreMore(page.has_more);
          catalogOffsets.current.stores = page.next_offset ?? offset + page.items.length;
        }
      } else {
        const page = await client.get<DiscoverySessionsPage>(`/api/v1/discovery/sources/${encodeURIComponent(application!.source_id)}/sessions?store_id=${encodeURIComponent(store!.id)}&limit=50&offset=${offset}`);
        if (live.current && creationRef.current.application_id === selectedApp && creationRef.current.store_id === selectedStore) {
          setBindings(old => [...old, ...page.sessions.filter(item => item.binding_id && (!item.store_id || item.store_id === selectedStore) && !old.some(previous => previous.binding_id === item.binding_id))]); setBindingMore(page.has_more);
          catalogOffsets.current.bindings = page.next_offset ?? offset + page.sessions.length;
        }
      }
    } catch (err) { if (live.current) setCatalogError(safeError(err)); }
    finally { if (live.current) setCatalogBusy(false); }
  }

  async function perform(key: string, fields: Record<string, unknown>, path: string,
    completed: (result: Record<string, unknown>) => Promise<void> | void) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError('');
    const signature = JSON.stringify(Object.fromEntries(Object.entries(fields).filter(([name]) => name !== 'expected_revision')));
    let command = memory.commands.get(key);
    if (!command || command.signature !== signature) {
      command = { signature, payload: { ...fields, request_id: crypto.randomUUID() } };
      memory.commands.set(key, command);
    }
    try {
      const result = await client.post<Record<string, unknown>>(path, command.payload);
      memory.commands.delete(key);
      if (live.current) await completed(result);
    } catch (err) {
      if (err instanceof ApiActionError && err.reasonCode?.endsWith('REVISION_CONFLICT')) memory.commands.delete(key);
      if (live.current) setError(safeError(err));
    } finally { busyRef.current = false; if (live.current) setBusy(false); }
  }
  async function createSession(event: FormEvent) {
    event.preventDefault();
    if (!application || !store || !scope || !creation.control_consent ||
      (creation.mode === 'CONTINUE' && !bindings.some(item => item.binding_id === creation.binding_id))) return;
    const fields = { expected_revision: currentRevision.current, application_id: application.id, store_id: store.id,
      scope_id: scope.id, task_id: creation.task_id || null, mode: creation.mode,
      binding_id: creation.mode === 'CONTINUE' ? creation.binding_id : null,
      read_only: creation.read_only, control_consent: true };
    await perform(`${workId}:create`, fields, `${executionBase(workId)}/sessions`, result => {
      if (typeof result.revision === 'number') callbacks.current.onRevision(result.revision);
      setCreateOpen(false); setCreation(emptyCreate);
      if (typeof result.session_id === 'string') setSelected(result.session_id);
      setRefresh(value => value + 1);
    });
  }
  async function startRun(event: FormEvent) {
    event.preventDefault();
    if (!session || session.status !== 'READY' || !session.capabilities.send || !input.trim() ||
      inputRequirement !== requirementVersion || runs.some(item => runIsActive(item.status)) || run && runIsActive(run.status)) return;
    const target = session.id;
    await perform(`${workId}:${target}:run`, { expected_revision: session.revision,
      requirement_version: inputRequirement, input, context_refs: contexts.map(choice => choice.ref) },
    `${executionBase(workId)}/sessions/${encodeURIComponent(target)}/runs`, result => {
      memory.drafts.delete(`${workId}:${target}`);
      if (selection.current.selected === target) {
        setInput('');
        if (typeof result.run_id === 'string') rememberedRuns.current.set(target, result.run_id);
        setRefresh(value => value + 1);
      }
    });
  }
  async function cancelRun() {
    if (!run || !session?.capabilities.cancel || !runIsActive(run.status) || ['UNCERTAIN', 'FINALIZING'].includes(run.status)) return;
    const target = run.id;
    await perform(`${workId}:${target}:cancel`, {}, `${executionBase(workId)}/runs/${encodeURIComponent(target)}/cancel`, () => {
      if (selection.current.runId === target) setRefresh(value => value + 1);
    });
  }
  async function respond(eventSeq: number, actionRef: string, response: Record<string, unknown>) {
    if (!run || session?.capabilities.responses === false) return;
    const target = run.id;
    await perform(`${workId}:${target}:response:${eventSeq}`, { event_seq: eventSeq, action_ref: actionRef, response },
      `${executionBase(workId)}/runs/${encodeURIComponent(target)}/responses`, () => {
        for (const key of memory.drafts.keys()) if (key.startsWith(`${workId}:${target}:${eventSeq}:`)) memory.drafts.delete(key);
        if (selection.current.runId === target) { setAnswers(old => Object.fromEntries(Object.entries(old).filter(([key]) => !key.startsWith(`${workId}:${target}:${eventSeq}:`)))); setRefresh(value => value + 1); }
    });
  }
  async function loadObservedChanges() {
    const file = run?.file_result;
    if (!file || file.status !== 'AVAILABLE' || !file.scope_id || !file.baseline_id || !file.version_id || changesBusy) return;
    const seq = ++changesSequence.current;
    setChangesBusy(true); setChangesError('');
    try {
      const found = await readProtectionVersions(client, file.scope_id, [file.baseline_id, file.version_id]);
      if (!live.current || seq !== changesSequence.current) return;
      const before = found.get(file.baseline_id), after = found.get(file.version_id);
      if (!before || !after || before.details_included === false || after.details_included === false ||
        !Array.isArray(before.files) || !Array.isArray(after.files)) throw new ApiRequestError('File history unavailable', null, 'PROTECTION_CONTENT_UNAVAILABLE');
      const old = new Map(before.files.map(entry => [entry.file_id, entry]));
      const next = new Map(after.files.map(entry => [entry.file_id, entry]));
      const references = [...new Set([...old.keys(), ...next.keys()])].filter(id => {
        const a = old.get(id), b = next.get(id);
        return !a || !b || a.content_digest !== b.content_digest || a.category !== b.category;
      }).map(id => ({ scope_id: file.scope_id, file_id: id, before_id: file.baseline_id, after_id: file.version_id,
        path: (next.get(id) || old.get(id))?.relative_path,
        content_available: [old.get(id), next.get(id)].every(entry => !entry || entry.category === 'restorable') }));
      if (live.current && seq === changesSequence.current) { setObservedChanges(references); setChangesLoaded(true); }
    } catch (err) { if (live.current && seq === changesSequence.current) setChangesError(safeError(err)); }
    finally { if (live.current && seq === changesSequence.current) setChangesBusy(false); }
  }
  async function openDiff(reference: ChangeReference) {
    const seq = ++diffSequence.current;
    const title = reference.path || reference.file_id || label('Recorded change', '已记录差异');
    setDiff({ title, body: '', error: '', loading: true });
    try {
      const value = await client.get<{ text_diff: string | null; binary?: boolean; truncated?: boolean }>(
        `/api/v1/protection/scopes/${encodeURIComponent(reference.scope_id || session!.scope_id)}/files/${encodeURIComponent(reference.file_id!)}/diff?before=${encodeURIComponent(reference.before_id!)}&after=${encodeURIComponent(reference.after_id!)}`);
      if (live.current && seq === diffSequence.current) setDiff({ title, body: value.text_diff ?? label('Text diff unavailable', '无可用文本差异'), error: '', loading: false, truncated: value.truncated });
    } catch (err) { if (live.current && seq === diffSequence.current) setDiff({ title, body: '', error: safeError(err), loading: false }); }
  }
  async function loadMoreRuns() {
    if (!selected || busy) return;
    const target = selected; setLoading(true);
    try {
      const page = await client.get<ExecutionPage<unknown>>(`${executionBase(workId)}/sessions/${encodeURIComponent(target)}/runs?limit=50&offset=${runs.length}`);
      const next = page.items.map(normalizeRun);
      if (next.some(item => item.session_id !== target)) throw new ApiRequestError('Invalid execution record');
      if (live.current && selection.current.selected === target) { setRuns(old => [...old, ...next.filter(item => !old.some(previous => previous.id === item.id))]); setRunsMore(page.has_more); }
    } catch (err) { if (live.current && selection.current.selected === target) setError(safeError(err)); }
    finally { if (live.current) setLoading(false); }
  }

  if (!visible) return null;
  const sourceScopes = scopes.filter(item => item.source_id === application?.source_id);
  const createReady = application && ['AVAILABLE', 'RUNNING'].includes(application.status) && store?.status === 'AVAILABLE' &&
    scope && Boolean(scope.enabled) && scope.status !== 'RETIRED' && creation.control_consent &&
    (creation.mode === 'NEW' || bindings.some(item => item.binding_id === creation.binding_id));
  const active = runs.some(item => runIsActive(item.status)) || Boolean(run && runIsActive(run.status));
  const pending = run?.pending_responses ?? [];
  const coverage = typeof run?.coverage === 'string' ? displayCode(run.coverage) :
    run?.coverage && typeof run.coverage === 'object' ? displayCode((run.coverage as { status?: unknown }).status) : '';
  return <section className="work-sessions" ref={root} role="region" aria-label={label('Session management', '会话管理')}>
    <header className="work-sessions-header">
      {selected && <button type="button" title={label('Session list', '会话列表')} aria-label={label('Session list', '会话列表')}
        onClick={() => { setSelected(null); setRunId(null); setSession(null); setRun(null); setError(''); }}>←</button>}
      <h2>{label('Session management', '会话管理')}</h2>
      <button type="button" title={label('Reload sessions', '刷新会话')} aria-label={label('Reload sessions', '刷新会话')} onClick={() => setRefresh(value => value + 1)}>↻</button>
      <button type="button" autoFocus title={label('Close session management', '关闭会话管理')} aria-label={label('Close session management', '关闭会话管理')} onClick={onClose}>×</button>
    </header>
    <div className="work-sessions-content">
      {error && <p role="alert" className="work-sessions-error">{error}</p>}
      {loading && <p role="status">{label('Loading records', '正在读取记录')}</p>}
      {!selected && <>
        <div className="work-sessions-actions"><button type="button" onClick={() => setCreateOpen(value => !value)} aria-expanded={createOpen}>{label('Create session', '创建会话')}</button></div>
        {createOpen && <form className="work-session-create" onSubmit={createSession}>
          <h3>{label('Create session', '创建会话')}</h3>
          {catalogError && <p role="alert">{catalogError}</p>}
          <label>{label('Application', '应用')}<select required value={creation.application_id} onChange={event => setCreation(old => ({ ...old, application_id: event.target.value, store_id: '', scope_id: '', binding_id: '' }))}>
            <option value="">{label('Select application', '选择应用')}</option>
            {applications.map(item => <option key={item.id} value={item.id}>{item.label || item.provider} · {item.source_id} · {item.path} · {item.status}</option>)}
          </select></label>
          {appMore && <button type="button" disabled={catalogBusy} onClick={() => void loadMoreCatalog('applications')}>{label('Load more applications', '加载更多应用')}</button>}
          {application && <small className="work-session-identity">{application.path}<br />{displayCode(application.reason_code)}</small>}
          <label>{label('Session store', '会话库')}<select required value={creation.store_id} onChange={event => setCreation(old => ({ ...old, store_id: event.target.value, binding_id: '' }))}>
            <option value="">{label('Select session store', '选择会话库')}</option>
            {stores.map(item => <option key={item.id} value={item.id}>{item.path} · {item.status}</option>)}
          </select></label>
          {storeMore && <button type="button" disabled={catalogBusy} onClick={() => void loadMoreCatalog('stores')}>{label('Load more stores', '加载更多会话库')}</button>}
          {store && <small>{displayCode(store.reason_code)}</small>}
          <label>{label('Protected directory', '保护目录')}<select required value={creation.scope_id} onChange={event => setCreation(old => ({ ...old, scope_id: event.target.value }))}>
            <option value="">{label('Select protected directory', '选择保护目录')}</option>
            {sourceScopes.map(item => <option key={item.id} value={item.id}>{item.root} · {item.status}</option>)}
          </select></label>
          {scope && <small>{displayCode(scope.reason_code)}</small>}
          <label>{label('Task (optional)', '任务（可选）')}<select value={creation.task_id} onChange={event => setCreation(old => ({ ...old, task_id: event.target.value }))}>
            <option value="">{label('Whole work', '整个工作')}</option>{tasks.map(task => <option key={task.id} value={task.id}>{task.title}</option>)}
          </select></label>
          <fieldset className="work-session-modes"><legend>{label('Context', '上下文')}</legend>
            {(['NEW', 'CONTINUE'] as const).map(mode => <label key={mode}><input type="radio" name={`session-mode-${workId}`} checked={creation.mode === mode}
              onChange={() => setCreation(old => ({ ...old, mode, binding_id: '' }))} />{mode === 'NEW' ? label('New', '全新') : label('Continue existing', '继续已有会话')}</label>)}
          </fieldset>
          {creation.mode === 'CONTINUE' && <label>{label('Existing session', '已有会话')}<select required value={creation.binding_id} onChange={event => setCreation(old => ({ ...old, binding_id: event.target.value }))}>
            <option value="">{label('Select exact session', '选择具体会话')}</option>
            {bindings.map(binding => <option key={binding.binding_id} value={binding.binding_id}>{binding.title || binding.provider || ''} · {binding.native_session_id || binding.session_id} · {binding.cwd || ''}</option>)}
          </select></label>}
          {creation.mode === 'CONTINUE' && bindingMore && <button type="button" disabled={catalogBusy} onClick={() => void loadMoreCatalog('bindings')}>{label('Load more existing sessions', '加载更多已有会话')}</button>}
          <label className="work-session-check"><input type="checkbox" checked={creation.read_only} onChange={event => setCreation(old => ({ ...old, read_only: event.target.checked }))} />{label('Read only', '只读')}</label>
          <label className="work-session-check"><input type="checkbox" required checked={creation.control_consent} onChange={event => setCreation(old => ({ ...old, control_consent: event.target.checked }))} />{label('Allow ASG to manage this session', '允许 ASG 管理此会话')}</label>
          <footer><button type="button" onClick={() => setCreateOpen(false)}>{label('Close', '关闭')}</button><button disabled={busy || catalogBusy || !createReady}>{label('Create', '创建')}</button></footer>
        </form>}
        {!loading && !items.length && <p>{label('No managed sessions', '暂无受管会话')}</p>}
        <div className="work-session-list">{items.map(item => <button type="button" key={item.id} onClick={() => { setSelected(item.id); setCreateOpen(false); setRunId(null); }}>
          <strong>{item.provider} · {item.native_session_id || item.id}</strong><span>{statusLabel(item.status)}{item.read_only ? ` · ${label('Read only', '只读')}` : ''}</span>
          {item.reason_code && <small>{displayCode(item.reason_code)}</small>}
        </button>)}</div>
        {hasMore && <button type="button" disabled={loading} onClick={() => void loadSessions(true)}>{label('Load more sessions', '加载更多会话')}</button>}
      </>}
      {selected && session && <>
        <div className="work-session-meta"><strong>{session.provider}</strong><span>{statusLabel(session.status)}</span><span>{session.read_only ? label('Read only', '只读') : session.capabilities.write ? label('Writes allowed by policy', '写入受策略控制') : label('Writes unavailable', '写入不可用')}</span>
          {session.connection_state && <span>{displayCode(session.connection_state)}</span>}
          <code>{session.native_session_id || session.id}</code>{session.reason_code && <small>{displayCode(session.reason_code)}</small>}
          {session.status === 'DISCONNECTED' && <button type="button" disabled={busy} onClick={() => void perform(`${workId}:${session.id}:connect`, { expected_revision: session.revision, control_consent: true }, `${executionBase(workId)}/sessions/${encodeURIComponent(session.id)}/connect`, () => { setRefresh(value => value + 1); })}>{label('Connect session', '连接会话')}</button>}
          <button type="button" disabled={busy || active || session.status === 'ARCHIVED'} onClick={() => void perform(`${workId}:${session.id}:archive`, { expected_revision: session.revision }, `${executionBase(workId)}/sessions/${encodeURIComponent(session.id)}/archive`, () => { setSelected(null); setRunId(null); setRefresh(value => value + 1); })}>{label('Archive session', '归档会话')}</button>
        </div>
        <form className="work-run-input" onSubmit={startRun}>
          <div className="work-run-input-heading"><label htmlFor={`run-input-${session.id}`}>{label('Execution input', '本次输入')}</label><button type="button" onClick={() => onOpenRequirement(inputRequirement)}>{label('Requirement', '要求')} v{inputRequirement}</button></div>
          {inputRequirement !== requirementVersion && <div className="work-requirement-warning" role="status">
            {label(`Requirement changed: this draft uses v${inputRequirement}; current is v${requirementVersion}.`, `要求已变更：草稿依据 v${inputRequirement}，当前为 v${requirementVersion}。`)}
            <button type="button" disabled={busy} onClick={() => { setInputRequirement(requirementVersion); memory.requirements.set(draftKey, requirementVersion); }}>{label('Use current requirement for next execution', '下一次执行采用当前要求')}</button>
          </div>}
          {run && runIsActive(run.status) && run.requirement_version !== requirementVersion && <p role="status">{label(`The active execution still uses requirement v${run.requirement_version}. Stop and confirm its end before restarting with new requirements.`, `正在执行的运行仍依据要求 v${run.requirement_version}。如需按新要求重做，先请求停止并确认结束。`)}</p>}
          <p className="work-run-send-scope">{session.provider} · {session.source_id} · {tasks.find(task => task.id === session.task_id)?.title || label('Entire work', '整个工作')} · {label('Protected directory', '保护目录')} {session.scope_root || session.scope_id} · {session.read_only ? label('Read only', '只读') : label('Writes within the selected scope', '所选范围内写入')}</p>
          <WorkRunContext key={draftKey} client={client} workId={workId} artifacts={artifacts} fileResult={run?.file_result} selected={contexts} disabled={busy}
            onChange={choices => { setContexts(choices); memory.contexts.set(draftKey, choices); }} />
          <textarea id={`run-input-${session.id}`} value={input} maxLength={100000} rows={4} onChange={event => { setInput(event.target.value); memory.drafts.set(draftKey, event.target.value); }} />
          <button disabled={busy || loading || active || inputRequirement !== requirementVersion || session.status !== 'READY' || !session.capabilities.send || !input.trim()}>{label('Start execution', '开始执行')}</button>
          {active && <small>{label('This draft has not been sent. Send it after the current execution ends, or request a stop and wait for confirmation.', '此草稿尚未发送。当前执行结束后再发送，或请求停止并等待确认。')}</small>}
          {!session.capabilities.send && <small>{label('Input unavailable', '输入不可用')} {displayCode(session.reason_code)}</small>}
        </form>
        <h3>{label('Execution records', '执行记录')}</h3>
        {!runs.length && !loading && <p>{label('No executions', '暂无执行')}</p>}
        <div className="work-run-list">{runs.map(item => <button type="button" key={item.id} aria-pressed={runId === item.id} onClick={() => { rememberedRuns.current.set(session.id, item.id); setRunId(item.id); setRun(null); }}>
          <span>{statusLabel(item.status)} · {item.requirement_version ? `${label('Requirement', '要求')} v${item.requirement_version}` : item.id}</span><small>{item.input_summary || item.created_at || item.id}</small>
        </button>)}</div>
        {runsMore && <button type="button" disabled={loading} onClick={() => void loadMoreRuns()}>{label('Load more executions', '加载更多执行')}</button>}
        {run && <section className="work-run-detail" aria-label={label('Execution detail', '执行详情')}>
          <header><strong>{statusLabel(run.status)}</strong><span>{streamState === 'DISCONNECTED' ? label('Connection interrupted', '连接中断') : streamState === 'RECONNECTING' ? label('Reconnecting', '正在重连') : streamState === 'UNAVAILABLE' ? label('Live output unavailable', '实时输出不可用') : ''}</span>
            {runIsActive(run.status) && !['UNCERTAIN', 'FINALIZING'].includes(run.status) && <button type="button" aria-label={label('Stop execution', '停止执行')} title={label('Stop execution', '停止执行')} disabled={busy || !session.capabilities.cancel || run.status === 'CANCELLING'} onClick={() => void cancelRun()}>■</button>}
          </header>
          {run.reason_code && <p>{displayCode(run.reason_code)}</p>}
          {run.requirement_version > 0 && <button type="button" onClick={() => onOpenRequirement(run.requirement_version)}>{label('Requirement', '要求')} v{run.requirement_version}</button>}
          {run.status === 'UNCERTAIN' && <button type="button" disabled={busy} onClick={() => void perform(`${workId}:${run.id}:reconcile`, {}, `${executionBase(workId)}/runs/${encodeURIComponent(run.id)}/reconcile`, () => { setRefresh(value => value + 1); })}>{label('Reconcile execution', '核对执行结果')}</button>}
          {onDelivery && !runIsActive(run.status) && <button type="button" onClick={() => onDelivery({ sessionId: session.id, run, events: [...history.events], omitted: history.omitted })}>{label('Prepare delivery', '准备交付')}</button>}
          {pending.map(action => {
            const approval = action.kind === 'APPROVAL' || action.kind === 'TOOL_APPROVAL';
            const responseKey = `${workId}:${run.id}:${action.event_seq}`;
            const questions = questionsFor(action.payload);
            const answersFor = (question: Question) => answers[`${responseKey}:${question.id}`] ?? memory.drafts.get(`${responseKey}:${question.id}`) ?? '';
            const ready = questions.length > 0 && questions.every(question => answersFor(question).trim());
            const allowed = Array.isArray(action.payload.allowed_decisions) ? action.payload.allowed_decisions : [];
            return <form key={`${run.id}:${action.event_seq}`} className="work-run-response" onSubmit={event => { event.preventDefault(); if (ready) void respond(action.event_seq, action.action_ref,
              { answers: Object.fromEntries(questions.map(question => [question.id, { answers: [answersFor(question)] }])) }); }}>
              <strong>{approval ? label('Operation approval', '操作审批') : label('Question', '问题')}</strong>{eventText(action) && <p>{eventText(action)}</p>}
              {approval ? <div className="work-sessions-actions">{(['ALLOW_ONCE', 'DENY', 'CANCEL'] as const).map(decision => <button type="button" key={decision} disabled={busy || session.capabilities.responses === false || !allowed.includes(decision)} onClick={() => void respond(action.event_seq, action.action_ref, { decision })}>{decision === 'ALLOW_ONCE' ? label('Allow once', '允许本次') : decision === 'DENY' ? label('Deny', '拒绝') : label('Cancel operation', '取消操作')}</button>)}</div> : <>
                {questions.map(question => {
                  const value = answersFor(question), key = `${responseKey}:${question.id}`;
                  const update = (value: string) => { setAnswers(old => ({ ...old, [key]: value })); memory.drafts.set(key, value); };
                  return <fieldset className="work-run-question" key={question.id}><legend>{question.header || question.question}</legend><p>{question.question}</p>
                    {!question.secret && question.options.length > 0 && <select aria-label={question.header || question.question} value={question.options.some(option => option.label === value) ? value : ''} onChange={event => update(event.target.value)}><option value="">{label('Select answer', '选择回答')}</option>{question.options.map(option => <option key={option.label} value={option.label}>{option.label}{option.description ? ` · ${option.description}` : ''}</option>)}</select>}
                    <input type={question.secret ? 'password' : 'text'} aria-label={question.options.length > 0 && !question.secret ? label('Custom answer', '自定义回答') : question.question} autoComplete="off" required maxLength={10000} value={value} onChange={event => update(event.target.value)} />
                  </fieldset>;
                })}<button disabled={busy || session.capabilities.responses === false || !ready}>{label('Submit answer', '提交回答')}</button>
              </>}
            </form>;
          })}
          <div className="work-run-events" role="log" aria-label={label('Execution output', '执行输出')} aria-live="off">
            {history.omitted > 0 && <small>{label('Earlier recorded entries', '更早的记录')} · {history.omitted}</small>}
            {history.events.filter(event => ['TEXT', 'MESSAGE', 'TOOL', 'APPROVAL', 'QUESTION', 'STATUS', 'FILE_CHANGE', 'OUTPUT_TRUNCATED', 'ERROR'].includes(event.kind) && eventBody(event)).map(event => <div key={event.seq} className={`work-run-event event-${event.kind.toLowerCase()}`}>
              <small>{event.seq} · {event.kind}</small><div>{eventBody(event)}</div>
            </div>)}
          </div>
          <h4>{label('Observed changes', '观察到的差异')}</h4><small>{label('Coverage', '覆盖范围')} · {coverage || label('Unknown', '未知')}</small>
          {run.file_result && <p>{label('Attribution unconfirmed', '归因未确认')} · {run.file_result.status} {displayCode(run.file_result.reason_code)}</p>}
          {run.file_result?.status === 'AVAILABLE' && <button type="button" disabled={changesBusy} onClick={() => void loadObservedChanges()}>{changesBusy ? label('Loading changes', '正在读取差异') : label('View recorded changes', '查看已记录差异')}</button>}
          {changesError && <p role="alert">{changesError}</p>}
          {!run.change_refs.length && !observedChanges.length && <p>{changesLoaded ? label('No observed content changes', '未观察到内容差异') : label('No change references loaded', '尚未读取差异引用')}</p>}
          {[...run.change_refs, ...observedChanges].map((reference, index) => typeof reference === 'string' ? <code className="work-change-ref" key={reference}>{reference}</code> :
            <button className="work-change-ref" type="button" key={`${reference.scope_id}:${reference.file_id}:${index}`} disabled={!reference.file_id || !reference.before_id || !reference.after_id || reference.content_available === false} onClick={() => void openDiff(reference)}>{reference.path || reference.file_id || reference.change_id || label('Recorded change', '已记录差异')}</button>)}
          {diff && <div className="work-run-diff"><header><strong>{diff.title}</strong><button type="button" title={label('Close diff', '关闭差异')} aria-label={label('Close diff', '关闭差异')} onClick={() => { diffSequence.current++; setDiff(null); }}>×</button></header>{diff.loading ? <p role="status">{label('Loading diff', '正在读取差异')}</p> : diff.error ? <p role="alert">{diff.error}</p> : <pre>{diff.body}</pre>}{diff.truncated && <small>{label('Diff truncated', '差异已截断')}</small>}</div>}
        </section>}
      </>}
    </div>
  </section>;
}
