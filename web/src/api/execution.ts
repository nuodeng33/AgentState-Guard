import { ApiRequestError, type ApiClient } from './client';

export interface ManagedCapabilities {
  create: boolean; continue: boolean; send: boolean; stream: boolean;
  cancel: boolean; fork: boolean; write: boolean; responses?: boolean;
}

export interface WorkSession {
  id: string; work_id: string; provider: string; source_id: string;
  application_id: string; store_id: string; scope_id: string; scope_root?: string; task_id: string | null;
  status: 'CREATING' | 'CONNECTING' | 'READY' | 'DISCONNECTED' | 'UNCERTAIN' | 'UNAVAILABLE' | 'ARCHIVED';
  connection_state: string; native_session_id: string | null; read_only: boolean;
  revision: number; capabilities: ManagedCapabilities; reason_code?: string | null;
}

export type RunStatus = 'PREPARING' | 'QUEUED' | 'DISPATCHING' | 'RUNNING' | 'WAITING_APPROVAL' | 'WAITING_INPUT' |
  'CANCELLING' | 'FINALIZING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'INTERRUPTED' | 'UNCERTAIN';

export interface RunEvent {
  seq: number; kind: string; payload: Record<string, unknown>; created_at: string;
}

export interface PendingResponse {
  event_seq: number; action_ref: string; kind: string; payload: Record<string, unknown>;
}

export interface ChangeReference {
  scope_id?: string; file_id?: string; before_id?: string; after_id?: string;
  change_id?: string; path?: string; [key: string]: unknown;
}

export interface ExecutionRun {
  id: string; session_id: string; requirement_version: number; status: RunStatus;
  input_summary: string; last_seq: number; coverage: unknown;
  change_refs: (ChangeReference | string)[]; reason_code?: string | null;
  pending_responses: PendingResponse[]; created_at: string; ended_at?: string | null;
  revision?: number; history_status?: string;
  file_result?: { status: 'AVAILABLE' | 'UNKNOWN'; scope_id: string; version_id: string;
    baseline_id: string; reason_code: string | null; attribution: string };
}

export interface ProtectionScope {
  id: string; source_id: string; root: string; status: string;
  enabled: boolean | number; reason_code?: string; coverage?: unknown;
  revision?: number; retention_days?: number; quota_bytes?: number;
}

export interface ExecutionPage<T> { items: T[]; has_more: boolean; }

export interface CreateSessionCommand {
  request_id: string; expected_revision: number; application_id: string; store_id: string;
  scope_id: string; task_id: string | null; mode: 'NEW' | 'CONTINUE' | 'FORK';
  binding_id: string | null; read_only: boolean; control_consent: true;
}

export interface StartRunCommand {
  request_id: string; expected_revision: number; requirement_version: number;
  input: string; context_refs: unknown[];
}

export type ExecutionContextRef = { kind: 'requirement' | 'artifact' | 'file' | 'review'; ref_id: string;
  version?: number; version_id?: string; file_id?: string };

export type ProtectedVersion = { id: string; files: { file_id: string; relative_path: string;
  content_digest?: string; category?: string }[]; details_included?: boolean };
export async function readProtectionVersions(client: ApiClient, scopeId: string, ids: string[]) {
  const found = new Map<string, ProtectedVersion>();
  for (let offset = 0; offset < 500; offset += 50) {
    const page = await client.get<{ items: ProtectedVersion[] }>(
      `/api/v1/protection/scopes/${encodeURIComponent(scopeId)}/versions?limit=50&offset=${offset}&view=full`);
    for (const version of page.items) if (ids.includes(version.id)) found.set(version.id, version);
    if (ids.every(id => found.has(id)) || page.items.length < 50) break;
  }
  if (ids.some(id => !found.has(id) || found.get(id)?.details_included === false || !Array.isArray(found.get(id)?.files)))
    throw new ApiRequestError('File history unavailable', null, 'PROTECTION_CONTENT_UNAVAILABLE');
  return found;
}

const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === 'string' ? value : '';
const number = (value: unknown) => Number.isSafeInteger(value) ? value as number : 0;
const reason = (value: unknown) => typeof value === 'string' && /^[A-Z0-9_]{1,64}$/.test(value) ? value : null;

export function normalizeSession(value: unknown): WorkSession {
  const item = object(value), capabilities = object(item.capabilities);
  const id = text(item.id ?? item.session_id);
  if (!id) throw new ApiRequestError('Invalid session record');
  const status = text(item.status ?? item.state);
  return { id, work_id: text(item.work_id), provider: text(item.provider), source_id: text(item.source_id),
    application_id: text(item.application_id), store_id: text(item.store_id), scope_id: text(item.scope_id),
    scope_root: text(item.scope_root), task_id: text(item.task_id) || null, native_session_id: text(item.native_session_id) || null,
    status: ['CREATING', 'CONNECTING', 'READY', 'DISCONNECTED', 'UNCERTAIN', 'UNAVAILABLE', 'ARCHIVED'].includes(status) ? status as WorkSession['status'] : 'UNAVAILABLE',
    connection_state: text(item.connection_state), read_only: item.read_only !== false,
    revision: number(item.revision), reason_code: reason(item.reason_code ?? item.reason),
    capabilities: { create: capabilities.create === true, continue: capabilities.continue === true,
      send: capabilities.send === true, stream: capabilities.stream === true, cancel: capabilities.cancel === true,
      fork: capabilities.fork === true, write: capabilities.write === true,
      responses: typeof capabilities.responses === 'boolean' ? capabilities.responses : undefined } };
}

export function normalizeRun(value: unknown): ExecutionRun {
  const item = object(value), fileResult = object(item.file_result);
  const id = text(item.id ?? item.run_id);
  if (!id) throw new ApiRequestError('Invalid execution record');
  const status = text(item.status ?? item.state);
  const pending = item.pending_responses ?? item.pending_actions;
  const references = item.change_refs ?? fileResult.change_refs;
  return { id, session_id: text(item.session_id), requirement_version: number(item.requirement_version),
    status: ['PREPARING', 'QUEUED', 'DISPATCHING', 'RUNNING', 'WAITING_APPROVAL', 'WAITING_INPUT', 'CANCELLING', 'FINALIZING', 'COMPLETED', 'FAILED', 'CANCELLED', 'INTERRUPTED', 'UNCERTAIN'].includes(status) ? status as RunStatus : 'UNCERTAIN',
    input_summary: text(item.input_summary), last_seq: number(item.last_seq ?? item.seq),
    coverage: item.coverage ?? fileResult.coverage ?? null,
    change_refs: Array.isArray(references) ? references as ExecutionRun['change_refs'] : [],
    pending_responses: Array.isArray(pending) ? pending.map(value => {
      const action = object(value);
      return { event_seq: number(action.event_seq ?? action.seq), action_ref: text(action.action_ref),
        kind: text(action.kind ?? action.type), payload: object(action.payload) };
    }).filter(action => action.event_seq > 0 && action.action_ref) : [],
    reason_code: reason(item.reason_code ?? item.reason), created_at: text(item.created_at ?? item.observed_at),
    revision: number(item.revision), history_status: text(item.history_status),
    ended_at: text(item.ended_at) || null,
    file_result: item.file_result ? { status: fileResult.status === 'AVAILABLE' ? 'AVAILABLE' : 'UNKNOWN',
      scope_id: text(fileResult.scope_id), version_id: text(fileResult.version_id), baseline_id: text(fileResult.baseline_id),
      reason_code: reason(fileResult.reason_code), attribution: text(fileResult.attribution) } : undefined };
}

export const executionBase = (workId: string) => `/api/v1/works/${encodeURIComponent(workId)}`;
export const runIsActive = (status: RunStatus) =>
  ['PREPARING', 'QUEUED', 'DISPATCHING', 'RUNNING', 'WAITING_APPROVAL', 'WAITING_INPUT', 'CANCELLING', 'FINALIZING', 'UNCERTAIN'].includes(status);

export function normalizeRunEvent(value: unknown): RunEvent {
  const item = object(value);
  const event = { seq: item.seq, kind: item.kind ?? item.type, payload: item.payload,
    created_at: item.created_at ?? item.observed_at } as RunEvent;
  if (!Number.isSafeInteger(event.seq) || event.seq < 1 || typeof event.kind !== 'string' ||
    typeof event.created_at !== 'string' || !event.payload || typeof event.payload !== 'object' || Array.isArray(event.payload))
    throw new ApiRequestError('Invalid event stream');
  return event;
}

/** One replayable page. The caller owns reconnects and deduplication across pages. */
export async function readRunEvents(client: ApiClient, workId: string, runId: string, after: number,
  onEvent: (event: RunEvent) => void, signal: AbortSignal): Promise<number> {
  if (!client.stream) throw new ApiRequestError('Event stream unavailable');
  const response = await client.stream(`${executionBase(workId)}/runs/${encodeURIComponent(runId)}/events?after=${after}&limit=100`, signal);
  if (!response.headers.get('Content-Type')?.toLowerCase().includes('text/event-stream') || !response.body) {
    await response.body?.cancel();
    throw new ApiRequestError('Invalid event stream');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', last = after, finished = false;
  const abort = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener('abort', abort, { once: true });
  const dispatch = (frame: string) => {
    const data: string[] = []; let id = '', type = '';
    for (const line of frame.split(/\r\n|\n|\r/)) {
      if (line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
      if (field === 'data') data.push(value);
      if (field === 'id') id = value;
      if (field === 'event') type = value;
    }
    if (!data.length) return;
    let value: unknown;
    try { value = JSON.parse(data.join('\n')); } catch { throw new ApiRequestError('Invalid event stream'); }
    const item = object(value);
    if (type === 'done') { finished = true; return; }
    if (type === 'error') throw new ApiRequestError('Execution event stream failed', null, reason(item.reason_code));
    const event = normalizeRunEvent(item);
    if (id && id !== String(event.seq)) throw new ApiRequestError('Invalid event stream');
    if (event.seq <= last || signal.aborted) return;
    last = event.seq; onEvent(event);
  };
  try {
    while (!signal.aborted && !finished) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      if (buffer.length > 524288) throw new ApiRequestError('Event stream too large');
      let boundary = buffer.match(/\r\n\r\n|\n\n|\r\r/);
      while (boundary?.index !== undefined && !finished) {
        dispatch(buffer.slice(0, boundary.index));
        buffer = buffer.slice(boundary.index + boundary[0].length);
        boundary = buffer.match(/\r\n\r\n|\n\n|\r\r/);
      }
      if (done) break;
    }
    return last;
  } finally {
    signal.removeEventListener('abort', abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
