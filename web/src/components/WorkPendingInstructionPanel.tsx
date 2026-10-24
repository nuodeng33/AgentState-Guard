import { useEffect, useRef, useState } from 'react';
import { ApiActionError, type ApiClient } from '../api/client';
import { executionBase } from '../api/execution';
import { useI18n } from '../i18n/I18nProvider';

export type PendingInstruction = {
  id: string; work_id: string; session_id: string; request_id: string; predecessor_run_id: string;
  state: 'PENDING' | 'HELD' | 'PROMOTED' | 'CANCELLED'; input: string; requirement_version: number;
  context_refs: unknown[]; reason_code: string | null; revision: number;
  created_at: string; updated_at: string; promoted_run_id: string | null;
};
type Receipt = { pending_id: string; session_id: string; request_id: string;
  state: string; revision: number; predecessor_run_id: string };
type Snapshot = { input: string; requirement_version: number; context_refs: unknown[] };
type Command = { kind: 'enqueue'; payload: Snapshot & { request_id: string; expected_revision: number; predecessor_run_id: string };
  receipt?: Receipt } | { kind: 'cancel'; id: string; lookupRequestId: string; payload: { request_id: string; expected_revision: number }; receipt?: Receipt };
export type PendingInstructionMemory = Map<string, Command>;
export type PendingInstructionBlock = { occupied: boolean; uncertain: boolean; unavailable: boolean };
type Props = {
  client: ApiClient; workId: string; sessionId: string; sessionRevision: number;
  predecessorRunId?: string; canEnqueue: boolean; draft: Snapshot; visible: boolean; disabled?: boolean;
  memory: PendingInstructionMemory;
  onBlockChange: (block: PendingInstructionBlock) => void;
  onEnqueued: (snapshot: Snapshot) => void; onPromoted: (runId: string) => void;
};
const isActive = (item: PendingInstruction | null) => Boolean(item && ['PENDING', 'HELD'].includes(item.state));
export function pendingContextKey(refs: unknown[]): string {
  return JSON.stringify(refs.map(ref => {
    if (!ref || typeof ref !== 'object' || Array.isArray(ref)) return ref;
    return Object.fromEntries(Object.entries(ref).filter(([key, value]) =>
      !(['version', 'version_id', 'file_id'].includes(key) && value == null)).sort(([a], [b]) => a.localeCompare(b)));
  }));
}
const reason = (value: unknown) => typeof value === 'string' && /^[A-Z0-9_]{1,64}$/.test(value) ? value : '';
function itemFrom(value: unknown, workId: string, sessionId: string): PendingInstruction | null {
  if (!value || typeof value !== 'object' || !Object.prototype.hasOwnProperty.call(value, 'item')) throw new Error();
  const item = (value as { item: PendingInstruction | null }).item;
  if (item === null) return null;
  if (!item || typeof item.id !== 'string' || !item.id || item.work_id !== workId || item.session_id !== sessionId ||
    typeof item.request_id !== 'string' || !item.request_id || typeof item.predecessor_run_id !== 'string' ||
    !['PENDING', 'HELD', 'PROMOTED', 'CANCELLED'].includes(item.state) || typeof item.input !== 'string' ||
    !Number.isSafeInteger(item.revision) || item.revision < 1 || !Number.isSafeInteger(item.requirement_version) ||
    !Array.isArray(item.context_refs) || item.state === 'PROMOTED' && !item.promoted_run_id) throw new Error();
  return item;
}
export default function WorkPendingInstructionPanel({ client, workId, sessionId, sessionRevision,
  predecessorRunId, canEnqueue, draft, visible, disabled = false, memory,
  onBlockChange, onEnqueued, onPromoted }: Props) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const label = (en: string, cn: string) => zh ? cn : en;
  const key = `${workId}:${sessionId}`;
  const base = `${executionBase(workId)}/sessions/${encodeURIComponent(sessionId)}/pending-instruction`;
  const [item, setItem] = useState<PendingInstruction | null>(null);
  const [command, setCommand] = useState<Command | null>(memory.get(key) ?? null);
  const [loaded, setLoaded] = useState(false), [readFailed, setReadFailed] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const live = useRef(true), inFlight = useRef(false), epoch = useRef(0);
  const promoted = useRef<string | null>(null);
  const callbacks = useRef({ onBlockChange, onEnqueued, onPromoted }); callbacks.current = { onBlockChange, onEnqueued, onPromoted };
  useEffect(() => { live.current = true; return () => { live.current = false; epoch.current++; }; }, []);
  useEffect(() => {
    callbacks.current.onBlockChange({ occupied: isActive(item), uncertain: Boolean(command) || busy,
      unavailable: !loaded || readFailed });
  }, [item, command, busy, loaded, readFailed]);
  function remember(next: Command | null) {
    if (next) memory.set(key, next); else memory.delete(key);
    if (live.current) setCommand(next);
  }
  function publish(next: PendingInstruction | null) {
    if (!live.current) return;
    setItem(next); setLoaded(true); setReadFailed(false);
    if (next?.state === 'PROMOTED' && next.promoted_run_id && promoted.current !== next.promoted_run_id) {
      promoted.current = next.promoted_run_id; callbacks.current.onPromoted(next.promoted_run_id);
    }
  }
  async function read(requestId?: string) {
    return itemFrom(await client.get<unknown>(base + (requestId ? '?request_id=' + encodeURIComponent(requestId) : '')), workId, sessionId);
  }
  function confirmFrom(next: PendingInstruction | null, current: Command): boolean {
    if (!next) return false;
    if (current.kind === 'enqueue') {
      if (next.request_id !== current.payload.request_id || next.predecessor_run_id !== current.payload.predecessor_run_id ||
        next.input !== current.payload.input || next.requirement_version !== current.payload.requirement_version ||
        pendingContextKey(next.context_refs) !== pendingContextKey(current.payload.context_refs) ||
        current.receipt && next.id !== current.receipt.pending_id) return false;
      remember(null);
      if (live.current) callbacks.current.onEnqueued(current.payload);
      return true;
    }
    if (current.receipt && next.id === current.id && next.state === 'CANCELLED' && next.revision >= current.receipt.revision) {
      remember(null); return true;
    }
    return false;
  }
  async function refresh() {
    if (inFlight.current) return;
    inFlight.current = true;
    const seq = ++epoch.current;
    try {
      const current = memory.get(key);
      const next = await read(current ? current.kind === 'enqueue' ? current.payload.request_id : current.lookupRequestId : undefined);
      if (!live.current || seq !== epoch.current) return;
      if (current) confirmFrom(next, current);
      publish(next); setError('');
    } catch { if (live.current && seq === epoch.current) { setReadFailed(true); setError('READ_FAILED'); } }
    finally { inFlight.current = false; }
  }
  useEffect(() => {
    if (!visible) { epoch.current++; return; }
    let active = true; let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (!active) return;
      if (document.visibilityState === 'visible') await refresh();
      if (active) timer = setTimeout(() => void poll(), 2000);
    };
    void poll();
    return () => { active = false; epoch.current++; if (timer) clearTimeout(timer); };
  }, [client, workId, sessionId, visible]);
  async function submit(current: Command) {
    if (inFlight.current || disabled) return;
    inFlight.current = true; setBusy(true); setError(''); remember(current); epoch.current++;
    let confirmed = Boolean(current.receipt);
    try {
      if (!current.receipt) {
        const response = await client.post<Receipt>(current.kind === 'enqueue' ? base : base + '/' + encodeURIComponent(current.id) + '/cancel', current.payload);
        const expectedState = current.kind === 'enqueue' ? 'PENDING' : 'CANCELLED';
        if (!response || response.session_id !== sessionId || response.request_id !== current.payload.request_id ||
          typeof response.pending_id !== 'string' || !response.pending_id || response.state !== expectedState ||
          !Number.isSafeInteger(response.revision) || response.revision !== (current.kind === 'enqueue' ? 1 : current.payload.expected_revision + 1) ||
          current.kind === 'enqueue' && response.predecessor_run_id !== current.payload.predecessor_run_id ||
          current.kind === 'cancel' && response.pending_id !== current.id) throw new Error();
        current = { ...current, receipt: response }; confirmed = true; remember(current);
      }
      const next = await read(current.kind === 'enqueue' ? current.payload.request_id : current.lookupRequestId);
      if (!confirmFrom(next, current)) throw new Error();
      publish(next);
    } catch (err) {
      if (!confirmed && err instanceof ApiActionError && err.status >= 400 && err.status < 500) {
        remember(null);
        if (live.current) { setError(reason(err.reasonCode) || 'REJECTED'); setReadFailed(true); }
      } else if (live.current) setError('UNCONFIRMED');
    } finally { inFlight.current = false; if (live.current) setBusy(false); }
  }
  const occupied = isActive(item);
  const locked = disabled || busy || Boolean(command);
  function enqueue() {
    if (locked || !loaded || readFailed || occupied || !canEnqueue || !predecessorRunId || !draft.input.trim()) return;
    // Snapshot before dispatch: future draft edits never change a queued command.
    const snapshot = JSON.parse(JSON.stringify(draft)) as Snapshot;
    void submit({ kind: 'enqueue', payload: { ...snapshot, request_id: crypto.randomUUID(),
      expected_revision: sessionRevision, predecessor_run_id: predecessorRunId } });
  }
  const status = (state: string) => ({
    PENDING: label('Queued for next execution', '已排为下一条'),
    HELD: label('Held for manual review', '已保留，需人工核对'),
    PROMOTED: label('Started as an execution', '已转为执行'),
    CANCELLED: label('Cancelled', '已取消'),
  }[state] ?? state);
  return <section className="work-pending-instruction" aria-label={label('Next instruction', '下一条指令')}>
    <h4>{label('Next instruction', '下一条指令')}</h4>
    <p>{label('One pending instruction per session. It starts only after the previous execution and file results finish, following server checks.', '每个会话最多一条待执行指令。仅当前次执行与文件结果收尾完成并通过服务器检查后，才会开始。')}</p>
    {error && <p role="alert">{error === 'UNCONFIRMED'
      ? label('Submission is unconfirmed. Check records or retry the same submission.', '提交尚未确认，请核对记录或重试同一提交。')
      : error === 'READ_FAILED' ? label('Pending instruction records are unavailable.', '未能读取待执行记录。')
        : label('Action rejected. Review current records before submitting again.', '操作被拒绝，请核对当前记录后再提交。')} {reason(error)}</p>}
    <button type="button" disabled={disabled || busy} onClick={() => void refresh()}>{label('Refresh next instruction', '刷新下一条指令')}</button>
    {command && <div role="status">
      <p>{label('Pending confirmation', '回执待确认')}: {command.kind === 'enqueue' ? command.payload.input : command.id}</p>
      <button type="button" disabled={disabled || busy} onClick={() => void submit(command)}>{command.receipt
        ? label('Refresh confirmed submission', '刷新已确认提交') : label('Retry same submission', '重试同一提交')}</button>
    </div>}
    {item && <article>
      <strong>{status(item.state)}</strong>
      <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{item.input}</p>
      <p>{label('Requirement', '要求')} v{item.requirement_version} · {label('Previous execution', '前次执行')} <code>{item.predecessor_run_id}</code></p>
      {item.reason_code && <p>{reason(item.reason_code)}</p>}
      {item.state === 'HELD' && <p>{label('This instruction will not start automatically. Cancel it, review the session, requirement and context, then submit explicitly.', '此指令不会自动开始。请先取消，核对会话、要求和上下文后再明确提交。')}</p>}
      {occupied && <button type="button" disabled={locked} onClick={() => void submit({ kind: 'cancel', id: item.id, lookupRequestId: item.request_id,
        payload: { request_id: crypto.randomUUID(), expected_revision: item.revision } })}>{label('Cancel next instruction', '取消下一条指令')}</button>}
    </article>}
    {canEnqueue && <button type="button" disabled={locked || !loaded || readFailed || occupied || !draft.input.trim()} onClick={enqueue}>{label('Queue next instruction', '排为下一条')}</button>}
  </section>;
}
