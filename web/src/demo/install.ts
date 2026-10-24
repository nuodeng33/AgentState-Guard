/**
 * Demo mode: answers /api/* from an in-memory store so the UI can be explored
 * without a Core. Only imported when VITE_ASG_DEMO=1.
 */
import { createStore, MAIN } from './fixtures';
import { AGENT_COLOR_OPTIONS } from '../pages/workAppearance';

const store = createStore();
// Demo-only memory; durable restart behavior is verified against real Core/SQLite.
const appearances = new Map<string, { color: string | null; revision: number; updated_at: string }>();
const now = () => new Date().toISOString();
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const fail = (status: number, reason_code: string) => json({ reason_code }, status);
const wait = () => new Promise(resolve => setTimeout(resolve, 80 + Math.random() * 160));

const summary = (w: (typeof store.works)[number]) => ({ id: w.id, title: w.title, owner: w.owner, revision: w.revision,
  requirement_version: w.requirement_version, updated_at: w.updated_at });
const detail = (w: (typeof store.works)[number]) => ({ ...summary(w), requirement: w.requirements[w.requirement_version - 1],
  tasks: w.tasks.map(t => ({ id: t.id, title: t.title })),
  artifacts: w.artifacts.map(({ id, title, version, requirement_version, created_at }) => ({ id, title, version, requirement_version, created_at })) });

function sse(items: { seq: number; kind: string; payload: unknown; created_at: string }[]) {
  const body = items.map(e => `id: ${e.seq}\ndata: ${JSON.stringify(e)}\n\n`).join('') + 'event: done\ndata: {}\n\n';
  return new Response(new Blob([body]).stream(), { headers: { 'Content-Type': 'text/event-stream' } });
}

function finishRun(runId: string, text: string) {
  const run = store.runs.find(r => r.id === runId); if (!run) return;
  const list = store.events[runId] ??= [];
  setTimeout(() => {
    list.push({ seq: list.length + 1, kind: 'TEXT', payload: { text }, created_at: now() });
    run.status = 'COMPLETED'; run.last_seq = list.length; (run as { ended_at?: string }).ended_at = now();
  }, 2500);
}

const EMPTY_VIEW = (view: string, items: unknown[] = []) => ({ schema_version: 'r4-p8-1', view, status: items.length ? 'AVAILABLE' : 'EMPTY',
  reason_code: items.length ? 'OK' : 'NO_ITEMS', evidence_refs: [], items, observed_at: now() });

function guard(path: string): Response | null {
  if (path === '/api/status') return json({ timestamp_utc: now(), checks: { core: true, database: true, protection: true, discovery: 'PARTIAL' },
    versions: { asg: '0.9.0.dev0', schema: '21', python: '3.11.9' } });
  if (path === '/api/doctor') return json([
    { check: 'core', status: 'OK', message: 'Core 正在运行' },
    { check: 'database', status: 'OK', message: 'SQLite 迁移已是最新' },
    { check: 'discovery', status: 'WARN', message: '会话库只读取了最近 200 条' },
    { check: 'wsl', status: 'SKIP', message: '未检测到 WSL' }]);
  if (path === '/api/readiness') return json({ ready: true });
  if (path.startsWith('/api/v1/runtime')) return json(EMPTY_VIEW('runtime', [
    { runtime_type: 'WINDOWS_LOCAL', execution_domain_id: 'local', availability: 'AVAILABLE', capabilities: ['MANAGED_SESSION', 'PROTECTION'], reason_code: 'OK', uncertainty: false, observed_at: now(), evidence_refs: [] },
    { runtime_type: 'DOCKER', execution_domain_id: null, availability: 'UNKNOWN', capabilities: [], reason_code: 'SANDBOX_LIMITED', uncertainty: true, observed_at: now(), evidence_refs: [] }]));
  if (path === '/api/v1/agents') return json(EMPTY_VIEW('agents', store.agents));
  if (path === '/api/v1/changes') return json(EMPTY_VIEW('changes'));
  if (path === '/api/v1/supervision') return json({ ...EMPTY_VIEW('supervision'), observed_agents: store.agents, recent_verified_activities: [] });
  if (path === '/api/v1/recovery') return json({ ...EMPTY_VIEW('recovery'), checkpoint_count: 0, latest_checkpoint: null, actual_restore_status: 'NOT_RUN',
    recovery_verified: false, verified_at: null, capabilities: { create_checkpoint: true, test_restore: true, restore: false }, limitations: [],
    r1_verified: false, r2_verified: false, r3_verified: false, test_restore_status: 'NOT_RUN', trusted_baseline_status: 'NONE', trusted_baseline_id: null });
  if (path === '/api/v1/devices') return json({ schema_version: 'device-link-1', enabled: false, status: 'DISABLED', endpoint: null, address: null,
    subnet: null, reason_code: 'DISABLED', desktop_uuid: null, desktop_signing_fingerprint: null, tls_spki_fingerprint: null, bound_devices: [], active_pair_sessions: 0 });
  if (path === '/api/v1/protection/scopes') return json({ items: [{ id: 'scope-sandbox', source_id: 'src-codex', root: 'C:/Work/asg-sandbox', status: 'PROTECTED', enabled: true }] });
  if (path.startsWith('/api/v1/discovery/sources')) return json({ items: [], has_more: false, status: 'AVAILABLE' });
  if (path.startsWith('/api/v1/discovery/applications')) return json({ items: [], has_more: false });
  if (path === '/api/ai/models') return json({ models: [] });
  return null;
}

async function handle(method: string, url: URL, body: Record<string, unknown>, stream: boolean): Promise<Response> {
  const path = url.pathname;
  if (path === '/api/session') return json({ token: 'demo-session' });
  if (method === 'GET') { const g = guard(path); if (g) return g; }

  if (path === '/api/v1/works' && method === 'GET') {
    const offset = Number(url.searchParams.get('offset') ?? 0);
    return json({ items: [...store.works].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(offset, offset + 50).map(summary) });
  }
  if (path === '/api/v1/works' && method === 'POST') {
    const id = crypto.randomUUID();
    store.works.push({ id, title: String(body.title), owner: String(body.owner), revision: 1, requirement_version: 1, updated_at: now(),
      requirements: [String(body.requirement)], tasks: [], artifacts: [], view: { revision: 0, state: null } });
    return json({ work_id: id });
  }
  const m = path.match(/^\/api\/v1\/works\/([^/]+)(?:\/(.*))?$/);
  if (!m) return fail(404, 'DEMO_UNHANDLED');
  const work = store.works.find(w => w.id === decodeURIComponent(m[1]));
  if (!work) return fail(404, 'WORK_NOT_FOUND');
  const rest = m[2] ?? '';
  const touch = () => { work.revision += 1; work.updated_at = now(); };

  if (!rest) return json(detail(work));
  if (rest === 'view') {
    if (method === 'GET') return json(work.view);
    if (body.expected_revision !== work.view.revision) return fail(409, 'WORK_VIEW_CONFLICT');
    work.view = { revision: work.view.revision + 1, state: body.state };
    return json({ revision: work.view.revision });
  }
  let r = rest.match(/^requirements\/(\d+)$/);
  if (r) { const body = work.requirements[Number(r[1]) - 1]; return body ? json({ title: `${work.title}`, body, requirement_version: Number(r[1]), source: 'MANUAL' }) : fail(404, 'NOT_FOUND'); }
  r = rest.match(/^tasks\/([^/]+)$/);
  if (r) { const t = work.tasks.find(x => x.id === r![1]); return t ? json({ title: t.title, body: t.body }) : fail(404, 'NOT_FOUND'); }
  r = rest.match(/^artifacts\/([^/]+)\/(\d+)$/);
  if (r) { const a = work.artifacts.find(x => x.id === r![1] && x.version === Number(r![2]));
    return a ? json({ title: a.title, body: a.body, requirement_version: a.requirement_version, source: a.source, source_run_id: a.source_run_id }) : fail(404, 'NOT_FOUND'); }
  if (rest === 'tasks' && method === 'POST') {
    const id = `t-${crypto.randomUUID().slice(0, 8)}`; work.tasks.push({ id, title: String(body.title), body: String(body.body ?? ''), assignee: null }); touch();
    return json({ work_id: work.id, task_id: id });
  }
  if (rest === 'artifacts' && method === 'POST') {
    const id = String(body.artifact_id || `a-${crypto.randomUUID().slice(0, 8)}`);
    const version = Math.max(0, ...work.artifacts.filter(a => a.id === id).map(a => a.version)) + 1;
    work.artifacts.push({ id, title: String(body.title), version, requirement_version: work.requirement_version, created_at: now(), body: String(body.body), source: 'MANUAL', source_run_id: null }); touch();
    return json({ work_id: work.id, artifact_id: id, version });
  }
  if (/^sessions\/[^/]+\/pending-instruction(?:\/.*)?$/.test(rest)) return fail(503, 'DEMO_PENDING_INSTRUCTION_UNAVAILABLE');
  const linked = work.id === MAIN ? store.agents : [];
  if (rest === 'agent-appearances' && method === 'GET') return json({ work_id: work.id, items: linked.map(agent => ({
    agent_id: agent.agent_id, color: null, revision: 0, updated_at: null, ...appearances.get(`${work.id}:${agent.agent_id}`),
  })) });
  const colorTarget = rest.match(/^agents\/([^/]+)\/appearance$/);
  if (colorTarget && method === 'POST') {
    const agentId = decodeURIComponent(colorTarget[1]);
    if (!linked.some(agent => agent.agent_id === agentId)) return fail(404, 'WORK_AGENT_LINK_NOT_FOUND');
    if (body.color !== null && !AGENT_COLOR_OPTIONS.some(option => option.tint === body.color)) return fail(422, 'WORK_APPEARANCE_INVALID');
    const key = `${work.id}:${agentId}`, previous = appearances.get(key);
    if (body.expected_revision !== (previous?.revision ?? 0)) return fail(409, 'WORK_APPEARANCE_CONFLICT');
    const saved = { color: body.color as string | null, revision: (previous?.revision ?? 0) + 1, updated_at: now() };
    appearances.set(key, saved);
    return json({ work_id: work.id, agent_id: agentId, ...saved });
  }
  if (rest === 'agents' && method === 'GET') return json({ items: linked });
  if (rest.startsWith('agents') && method === 'POST') {
    const t = rest.match(/^agents\/([^/]+)\/tasks$/);
    const agent = t && store.agents.find(a => a.agent_id === decodeURIComponent(t[1]));
    if (agent && Array.isArray(body.task_ids)) agent.task_ids = body.task_ids as string[];
    touch(); return json({ revision: work.revision });
  }
  const sessions = store.sessions.filter(s => s.work_id === work.id);
  const runsOf = (ids: string[]) => store.runs.filter(x => ids.includes(x.session_id));
  if (rest === 'execution-summary') {
    const runs = runsOf(sessions.map(s => s.id)).filter(x => !['COMPLETED', 'CANCELLED', 'FAILED'].includes(x.status));
    const counts: Record<string, number> = {}; for (const x of runs) counts[x.status] = (counts[x.status] ?? 0) + 1;
    return json({ work_id: work.id, requirement_version: work.requirement_version, work_revision: work.revision, observed_at: now(), has_more: false, counts,
      items: runs.map(x => ({ id: x.id, session_id: x.session_id, provider: sessions.find(s => s.id === x.session_id)!.provider, state: x.status, requirement_version: x.requirement_version, updated_at: x.created_at })) });
  }
  if (rest === 'sessions' && method === 'GET') return json({ items: sessions, has_more: false });
  r = rest.match(/^sessions\/([^/]+)(\/runs)?$/);
  if (r && method === 'GET') {
    const s = sessions.find(x => x.id === decodeURIComponent(r![1])); if (!s) return fail(404, 'NOT_FOUND');
    return r[2] ? json({ items: runsOf([s.id]).sort((a, b) => b.created_at.localeCompare(a.created_at)), has_more: false }) : json(s);
  }
  if (r && r[2] && method === 'POST') {
    const id = `run-${crypto.randomUUID().slice(0, 8)}`;
    store.runs.push({ id, session_id: decodeURIComponent(r[1]), requirement_version: Number(body.requirement_version ?? work.requirement_version), status: 'RUNNING',
      input_summary: String(body.input ?? '').slice(0, 120), last_seq: 1, coverage: null, change_refs: [], created_at: now(), pending_responses: [] });
    store.events[id] = [{ seq: 1, kind: 'TEXT', payload: { text: '已收到输入，开始执行（演示数据）。' }, created_at: now() }];
    finishRun(id, '演示执行已结束。真实环境中这里会显示 Agent 的输出。');
    return json({ run_id: id, session_id: decodeURIComponent(r[1]) });
  }
  r = rest.match(/^runs\/([^/]+)(?:\/(events|responses|cancel))?$/);
  if (r) {
    const run = store.runs.find(x => x.id === decodeURIComponent(r![1])); if (!run) return fail(404, 'NOT_FOUND');
    if (!r[2]) return json(run);
    if (r[2] === 'events') {
      const after = Number(url.searchParams.get('after') ?? 0);
      const list = (store.events[run.id] ?? []).filter(e => e.seq > after);
      return stream ? sse(list) : json({ items: list, has_more: false });
    }
    if (r[2] === 'responses') {
      run.pending_responses = []; run.status = 'RUNNING';
      const list = store.events[run.id] ??= [];
      list.push({ seq: list.length + 1, kind: 'TEXT', payload: { text: '收到回答，继续执行。' }, created_at: now() }); run.last_seq = list.length;
      finishRun(run.id, '重连退避已实现：首次 1 秒，指数增长，上限 30 秒；未确认输入按你的选择处理。');
      return json({ revision: work.revision });
    }
    run.status = 'CANCELLED'; return json({ revision: work.revision });
  }
  if (rest.startsWith('submissions')) {
    if (method === 'GET') return json({ items: work.id === MAIN ? store.submissions : [], has_more: false });
    touch(); return json({ revision: work.revision });
  }
  if (rest === 'protection-links') return json({ items: [] });
  return fail(404, 'DEMO_UNHANDLED');
}

const realFetch = window.fetch.bind(window);
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const request = new Request(input, init);
  const url = new URL(request.url, location.href);
  if (!url.pathname.startsWith('/api/')) return realFetch(input, init);
  await wait();
  let body: Record<string, unknown> = {};
  if (request.method === 'POST') { try { body = await request.json(); } catch { body = {}; } }
  const key = typeof body.request_id === 'string' ? `${url.pathname}:${body.request_id}` : null;
  if (key && store.requests.has(key)) return json(store.requests.get(key));
  const response = await handle(request.method, url, body, (request.headers.get('Accept') ?? '').includes('text/event-stream'));
  if (response.status === 404) console.warn('[asg-demo] unhandled', request.method, url.pathname);
  if (key && response.ok) store.requests.set(key, await response.clone().json());
  return response;
};
