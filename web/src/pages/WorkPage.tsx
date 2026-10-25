import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState, type CSSProperties, type FormEvent, type PointerEvent, type ButtonHTMLAttributes } from 'react';
import { apiClient, ApiActionError, ApiRequestError, SessionUnavailableError, type ApiClient } from '../api/client';
import { WorkMenuBar } from '../components/WorkMenuBar';
import { WorkSidebar, type PageKey } from '../components/AppShell';
import { useI18n } from '../i18n/I18nProvider';
import { formatDateTime } from '../i18n/format';
import { workspaceCopy } from './workspaceCopy';
import WorkAgents, { useWorkAgents, type WorkAgent } from './WorkAgents';
import WorkSessions, { createWorkSessionMemory, type WorkRunOutput } from '../components/WorkSessions';
import WorkDelivery from '../components/WorkDelivery';
import WorkRecovery from '../components/WorkRecovery';
import WorkEditor from '../components/WorkEditor';
import WorkProtectionLinks from '../components/WorkProtectionLinks';
import WorkBubbleGroup from '../components/WorkBubbleGroup';
import { agentTint } from './workAppearance';
import { useAgentAppearances } from '../components/useAgentAppearances';
import { AgentAppearancePicker } from '../components/AgentAppearancePicker';
import type { ProtectionScopeCommand } from '../api/protection';
import WorkExecutionStatus, { type ExecutionTarget } from '../components/WorkExecutionStatus';
import { AgentFilm, Icon, Prose, WorkCore, goalOf } from './WorkVisuals';
import './work.css';

type Summary = { id: string; title: string; owner: string; revision: number; requirement_version: number; updated_at?: string };
type Artifact = { id: string; title: string; version: number; requirement_version: number; created_at?: string };
type Work = Summary & { requirement: string; tasks: { id: string; title: string }[]; artifacts: Artifact[] };
export type Card = { kind: 'requirement' | 'task' | 'artifact'; ref_id: string; version: number | null;
  x: number; y: number; width: number; height: number; scroll: number };
type SpaceNode = Pick<Card, 'kind' | 'ref_id' | 'version' | 'x' | 'y'>;
type Viewport = { x: number; y: number; zoom: number };
export type Board = { schema_version: 1; viewport: Viewport; cards: Card[];
  surface?: 'space' | 'board'; space?: { viewport: Viewport; nodes: SpaceNode[] } };
type View = { revision: number; state: Board | null };
type Content = { title?: string; body: string; requirement_version?: number; source?: string; source_run_id?: string | null };
type SaveCommand = { request_id: string; expected_revision: number; state: Board; edit: number };
type Session = { id: string; board: Board; revision: number; edits: number; saved: number;
  pending: SaveCommand | null; saving: Promise<void> | null; status: 'saved' | 'dirty' | 'saving' | 'failed' | 'conflict'; blocked: boolean };
const WorkRestScene = lazy(() => import('./WorkRestScene'));
const key = (card: Pick<Card, 'kind' | 'ref_id' | 'version'>) => `${card.kind}:${card.ref_id}:${card.version ?? ''}`;
const bound = (n: number, min: number, max: number) => Math.min(max, Math.max(min, n));
/** Soft S-curve between two space points; reads as a relation, not a wire. */
const curve = (a: { x: number; y: number }, b: { x: number; y: number }) => {
  const mid = (a.x + b.x) / 2;
  return `M${a.x} ${a.y}C${mid} ${a.y} ${mid} ${b.y} ${b.x} ${b.y}`;
};
/** Default seat on a shallow arc beside the core: tasks to the left, artifacts to the right. */
function arc(index: number, count: number, side: -1 | 1) {
  const step = count > 1 ? Math.min(0.42, 2.1 / (count - 1)) : 0, angle = (index - (count - 1) / 2) * step;
  return { x: Math.round(side * (300 + 90 * Math.cos(angle))), y: Math.round(Math.sin(angle) * 430) };
}
function availableNodes(work: Work): SpaceNode[] {
  return [{ kind: 'requirement' as const, ref_id: work.id, version: work.requirement_version, x: 0, y: 0 },
    ...work.tasks.map((task, index) => ({ kind: 'task' as const, ref_id: task.id, version: null, ...arc(index, work.tasks.length, -1) })),
    ...work.artifacts.map((artifact, index) => ({ kind: 'artifact' as const, ref_id: artifact.id, version: artifact.version, ...arc(index, work.artifacts.length, 1) }))].slice(0, 1000);
}
function withSpace(state: Board, work: Work): Board {
  const available = availableNodes(work);
  if (!state.space) return { ...state, surface: state.surface ?? 'space', space: { viewport: { x: 0, y: 0, zoom: 1 }, nodes: available } };
  const existing = new Set(state.space.nodes.map(key));
  const nodes = [...state.space.nodes];
  for (const node of available) if (!existing.has(key(node)) && nodes.length < 1000) {
    const occupied = nodes.some(current => Math.abs(current.x - node.x) < 160 && Math.abs(current.y - node.y) < 120);
    const sameKind = nodes.filter(current => current.kind === node.kind);
    nodes.push(occupied ? { ...node, y: bound(Math.max(...sameKind.map(current => current.y), node.y) + 170, -100000, 100000) } : node);
  }
  return { ...state, surface: state.surface ?? 'space', space: { ...state.space, nodes } };
}
const initialBoard = (id: string, version: number): Board => ({ schema_version: 1,
  viewport: { x: 24, y: 24, zoom: 1 }, cards: [{ kind: 'requirement', ref_id: id, version,
    x: 0, y: 0, width: 400, height: 430, scroll: 0 }] });

export default function WorkPage({ client = apiClient, onUnsavedChange, onNavigate, visible = true }: { client?: ApiClient; onUnsavedChange?: (value: boolean) => void; onNavigate?: (page: PageKey) => void; visible?: boolean }) {
  const { locale } = useI18n(); const t = workspaceCopy[locale];
  const [items, setItems] = useState<Summary[]>([]); const [more, setMore] = useState(false);
  const [work, setWork] = useState<Work | null>(null); const workRef = useRef<Work | null>(null);
  const agents = useWorkAgents(client, work?.id, visible);
  const appearances = useAgentAppearances(client, work?.id, agents.items.map(agent => agent.agent_id).join('\0'));
  const [appearancePanel, setAppearancePanel] = useState<{ selected?: string } | null>(null);
  const appearanceOpener = useRef<HTMLElement | null>(null);
  function closeAppearance() { setAppearancePanel(null); requestAnimationFrame(() => { if (appearanceOpener.current?.isConnected) appearanceOpener.current.focus(); else menuButton.current?.focus(); }); }
  function openAppearance(selected?: string) { appearanceOpener.current = document.activeElement as HTMLElement | null; setAppearancePanel({ selected }); setAgentPanel(null); setRecoveryPanel(false); setSessionPanel(false); setDeliveryPanel(false); setMenuOpen(false); }
  const [agentPanel, setAgentPanel] = useState<{ selected?: string } | null>(null);
  const [sessionPanel, setSessionPanel] = useState(false);
  const [executionTarget, setExecutionTarget] = useState<ExecutionTarget | null>(null);
  const [deliveryPanel, setDeliveryPanel] = useState(false);
  const [recoveryPanel, setRecoveryPanel] = useState(false);
  const [editor, setEditor] = useState<{ mode: 'work' | 'requirement' | 'task'; taskId?: string } | null>(null);
  const [linksPanel, setLinksPanel] = useState(false);
  const [deliveryOutput, setDeliveryOutput] = useState<WorkRunOutput | null>(null);
  const deliveryOpener = useRef<HTMLElement | null>(null); const deliveryReturnsToSession = useRef(false);
  const sessionMemory = useRef(createWorkSessionMemory());
  const [protectionCommand, setProtectionCommand] = useState<ProtectionScopeCommand | null>(null);
  const sessionOpener = useRef<HTMLElement | null>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  function openSessions(target?: ExecutionTarget) { sessionOpener.current = target ? document.activeElement as HTMLElement | null : menuButton.current; setExecutionTarget(target ?? null); setRecoveryPanel(false); setDeliveryPanel(false); setSessionPanel(true); setMenuOpen(false); }
  function closeSessions() { setSessionPanel(false); requestAnimationFrame(() => { if (sessionOpener.current?.isConnected) sessionOpener.current.focus(); else menuButton.current?.focus(); }); }
  function openDelivery(output: WorkRunOutput | null = null) {
    deliveryOpener.current = output ? document.activeElement as HTMLElement | null : menuButton.current;
    deliveryReturnsToSession.current = Boolean(output && sessionPanel);
    setDeliveryOutput(output); setRecoveryPanel(false); setDeliveryPanel(true); setSessionPanel(false); setMenuOpen(false);
  }
  function closeDelivery() {
    setDeliveryPanel(false); if (deliveryReturnsToSession.current) setSessionPanel(true);
    requestAnimationFrame(() => { if (deliveryOpener.current?.isConnected) deliveryOpener.current.focus(); else menuButton.current?.focus(); });
  }
  function openRecovery() {
    setRecoveryPanel(true); setSessionPanel(false); setDeliveryPanel(false);
    deliveryReturnsToSession.current = false;
    setAgentPanel(null); setRestOpen(false); setForm(null); setFocus(null); setMenuOpen(false);
  }
  function closeRecovery() {
    setRecoveryPanel(false);
    requestAnimationFrame(() => menuButton.current?.focus());
  }
  const agentOpener = useRef<HTMLElement | null>(null);
  function openAgents(selected?: string) { setAppearancePanel(null); agentOpener.current = document.activeElement as HTMLElement | null; setRecoveryPanel(false); setAgentPanel({ selected }); }
  function closeAgents() { setAgentPanel(null); requestAnimationFrame(() => agentOpener.current?.focus()); }
  const [board, setBoard] = useState<Board | null>(null); const boardRef = useRef<Board | null>(null);
  const [status, setStatus] = useState(''); const [error, setError] = useState('');
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<'work' | 'artifact' | 'task' | null>(null);
  const [draft, setDraft] = useState({ title: '', owner: '', body: '', artifact: '' });
  const [focus, setFocus] = useState<string | null>(null); const [requirementVersion, setRequirementVersion] = useState(1);
  const readingOpener = useRef<HTMLElement | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [relationshipsOpen, setRelationshipsOpen] = useState(false);
  const [relationsRetained, setRelationsRetained] = useState(false);
  useEffect(() => {
    if (relationshipsOpen) { setRelationsRetained(true); return; }
    const timer = setTimeout(() => setRelationsRetained(false), 250);
    return () => clearTimeout(timer);
  }, [relationshipsOpen]);
  const [relationLimit, setRelationLimit] = useState(12);
  const [menuOpen, setMenuOpen] = useState(false);
  const [restOpen, setRestOpen] = useState(false);
  const sessions = useRef(new Map<string, Session>()); const mounted = useRef(true);
  const request = useRef<{ signature: string; id: string } | null>(null);
  const canvas = useRef<HTMLDivElement>(null); const loadSequence = useRef(0);
  const fitOnBoardOpen = useRef(false);
  const message = (err: unknown) => err instanceof ApiActionError ?
    (err.reasonCode === 'WORK_REVISION_CONFLICT' ? t.conflict : err.reasonCode === 'WORK_CONTENT_SENSITIVE' ? t.sensitive : `${t.failed} ${err.reasonCode ?? ''}`) : t.failed;

  const publishStatus = useCallback((session: Session) => {
    if (mounted.current && workRef.current?.id === session.id && sessions.current.get(session.id) === session) setStatus(session.status);
  }, []);
  const saveView = useCallback((session?: Session) => {
    const target = session ?? (workRef.current && sessions.current.get(workRef.current.id));
    if (!target || target.saving || target.blocked || target.saved === target.edits) return target?.saving ?? Promise.resolve();
    target.status = 'saving'; publishStatus(target);
    const run = async () => {
      try {
        while (target.saved !== target.edits) {
          const command = target.pending ?? { request_id: crypto.randomUUID(), expected_revision: target.revision,
            state: target.board, edit: target.edits };
          target.pending = command;
          const { edit, ...payload } = command;
          const result = await client.post<{ revision: number }>(`/api/v1/works/${target.id}/view`, payload);
          target.revision = result.revision; target.saved = edit; target.pending = null;
        }
        target.status = 'saved'; publishStatus(target);
      } catch (err) {
        target.status = err instanceof ApiActionError && err.reasonCode === 'WORK_VIEW_CONFLICT' ? 'conflict' : 'failed';
        target.blocked = target.status === 'conflict'; publishStatus(target);
      } finally { target.saving = null; }
    };
    target.saving = run(); return target.saving;
  }, [client, publishStatus]);

  function changeBoard(next: Board) {
    const session = workRef.current && sessions.current.get(workRef.current.id); if (!session) return;
    session.board = next; session.edits += 1; boardRef.current = next; setBoard(next);
    if (!session.blocked) { session.status = 'dirty'; setStatus('dirty'); }
  }
  function updateCard(id: string, values: Partial<Card>) {
    const current = boardRef.current; if (!current) return;
    changeBoard({ ...current, cards: current.cards.map(card => key(card) === id ? { ...card, ...values } : card) });
  }
  async function list(append = false) {
    const result = await client.get<{ items: Summary[] }>(`/api/v1/works?limit=50&offset=${append ? items.length : 0}`);
    if (!Array.isArray(result.items)) throw new Error('WORK_LIST_INVALID');
    setItems(old => append ? [...old, ...result.items] : result.items); setMore(result.items.length === 50);
  }
  async function openWork(id: string) {
    const previous = workRef.current && sessions.current.get(workRef.current.id);
    if (previous && previous.id !== id) void saveView(previous);
    const seq = ++loadSequence.current; setLoading(true); setError('');
    try {
      const cached = sessions.current.get(id);
      const preserve = cached && cached.edits !== cached.saved;
      const [detail, view] = await Promise.all([client.get<Work>(`/api/v1/works/${id}`),
        preserve ? Promise.resolve(null) : client.get<View>(`/api/v1/works/${id}/view`)]);
      if (seq !== loadSequence.current || !mounted.current) return;
      workRef.current = detail; setWork(detail); setRequirementVersion(detail.requirement_version);
      let session = preserve ? cached : null;
      if (!session) {
        const next = withSpace(view!.state ?? initialBoard(id, detail.requirement_version), detail);
        const dirty = !view!.state || JSON.stringify(next) !== JSON.stringify(view!.state);
        session = { id, board: next, revision: view!.revision, edits: dirty ? 1 : 0, saved: 0,
          pending: null, saving: null, status: dirty ? 'dirty' : 'saved', blocked: false };
        sessions.current.set(id, session);
      }
      boardRef.current = session.board; setBoard(session.board); setStatus(session.status);
      setAppearancePanel(null); setFocus(null); setLibraryOpen(false); setRelationshipsOpen(false); setRelationLimit(12); setMenuOpen(false); setRestOpen(false); setAgentPanel(null); setSessionPanel(false); setDeliveryPanel(false); setDeliveryOutput(null);
      const url = new URL(location.href); url.searchParams.set('work', id); history.replaceState(null, '', url);
    } catch (err) { if (seq === loadSequence.current) { workRef.current = null; boardRef.current = null; setWork(null); setBoard(null); setError(message(err));
      const url = new URL(location.href); url.searchParams.delete('work'); history.replaceState(null, '', url);
      await list().catch(() => {}); } }
    finally { if (seq === loadSequence.current) setLoading(false); }
  }
  useEffect(() => {
    mounted.current = true;
    const selected = new URLSearchParams(location.search).get('work');
    if (selected) { void openWork(selected); void list().catch(() => {}); }
    else void list().catch(() => setError(t.failed)).finally(() => setLoading(false));
    return () => { mounted.current = false; loadSequence.current += 1; for (const session of sessions.current.values()) void saveView(session); };
  }, [client]); // Locale changes do not discard an active editing session.
  useEffect(() => { if (!board || status !== 'dirty') return; const timer = setTimeout(() => void saveView(), 500); return () => clearTimeout(timer); }, [board, status, saveView]);
  useEffect(() => { if (!visible) setRestOpen(false); }, [visible]);
  useEffect(() => {
    if (!visible || !restOpen || agentPanel || appearancePanel) return;
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); setRestOpen(false); } };
    document.addEventListener('keydown', escape); return () => document.removeEventListener('keydown', escape);
  }, [visible, restOpen, agentPanel, appearancePanel]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if ([...sessions.current.values()].some(s => s.saved !== s.edits) || form || editor || linksPanel || appearances.hasPending) { e.preventDefault(); e.returnValue = ''; } };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [form, editor, linksPanel, appearances.hasPending]);
  useEffect(() => { onUnsavedChange?.([...sessions.current.values()].some(s => s.saved !== s.edits) || form !== null || editor !== null || linksPanel || appearances.hasPending); }, [status, form, editor, linksPanel, onUnsavedChange, appearances.hasPending]);
  useEffect(() => {
    if (!visible || (!form && !focus)) return;
    const previous = focus ? readingOpener.current : document.activeElement as HTMLElement | null;
    const keyboard = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) { e.preventDefault(); if (focus) setFocus(null); else setForm(null); }
      if (e.key !== 'Tab') return;
      const nodes = Array.from(document.querySelectorAll<HTMLElement>('.work-overlay button:not(:disabled), .work-overlay input, .work-overlay textarea, .work-overlay select'));
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keyboard);
    return () => { document.removeEventListener('keydown', keyboard); previous?.focus(); };
  }, [form, focus, busy, visible]);
  useEffect(() => {
    if (!visible || !work || editor || linksPanel || form || focus || restOpen || agentPanel || sessionPanel || deliveryPanel || recoveryPanel || appearancePanel) return;
    const keyboard = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      e.preventDefault();
      if (libraryOpen) setLibraryOpen(false);
      else if (menuOpen) setMenuOpen(false);
      else if (boardRef.current?.surface === 'board') returnToSpace();
      else void back();
    };
    document.addEventListener('keydown', keyboard); return () => document.removeEventListener('keydown', keyboard);
  }, [work, form, focus, visible, restOpen, libraryOpen, menuOpen, agentPanel, sessionPanel, deliveryPanel, recoveryPanel, appearancePanel, editor, linksPanel]);
  function addCard(kind: Card['kind'], ref: string, version: number | null) {
    const current = boardRef.current; if (!current) return;
    const existing = current.cards.find(card => card.kind === kind && card.ref_id === ref && card.version === version);
    const card = existing ?? { kind, ref_id: ref, version, x: current.cards.length * 430, y: 0, width: kind === 'task' ? 320 : 400, height: kind === 'task' ? 260 : 430, scroll: 0 };
    if (!existing && current.cards.length >= 40) return;
    const rect = canvas.current?.getBoundingClientRect();
    const cardZoom = current.cards.length ? current.viewport.zoom : 1;
    changeBoard({ ...current, cards: existing ? current.cards : [...current.cards, card], viewport: {
      zoom: cardZoom, x: (rect?.width ?? 900) / 2 - (card.x + card.width / 2) * cardZoom,
      y: 24 - card.y * cardZoom,
    } });
  }
  function zoom(factor: number, px?: number, py?: number) {
    const b = boardRef.current; if (!b) return;
    const x = px ?? (canvas.current?.clientWidth ?? 900) / 2; const y = py ?? 240;
    const z = bound(b.viewport.zoom * factor, .25, 2); const ratio = z / b.viewport.zoom;
    changeBoard({ ...b, viewport: { x: bound(x - (x - b.viewport.x) * ratio, -100000, 100000),
      y: bound(y - (y - b.viewport.y) * ratio, -100000, 100000), zoom: z } });
  }
  function fit() {
    const b = boardRef.current; if (!b?.cards.length || !canvas.current) return;
    const left = Math.min(...b.cards.map(c => c.x)); const top = Math.min(...b.cards.map(c => c.y));
    const width = Math.max(...b.cards.map(c => c.x + c.width)) - left;
    const height = Math.max(...b.cards.map(c => c.y + c.height)) - top;
    const z = bound(Math.min((canvas.current.clientWidth - 48) / width, (canvas.current.clientHeight - 48) / height, 1), .25, 2);
    changeBoard({ ...b, viewport: { x: 24 - left * z, y: 24 - top * z, zoom: z } });
  }
  useEffect(() => { if (board?.surface === 'board' && fitOnBoardOpen.current && canvas.current) {
    fitOnBoardOpen.current = false; fit();
  } }, [board]);
  useEffect(() => {
    const el = canvas.current; if (!el) return;
    const wheel = (e: WheelEvent) => {
      if ((e.target as Element).closest('.work-card') && !e.ctrlKey && !e.metaKey) return;
      e.preventDefault(); const r = el.getBoundingClientRect(); zoom(Math.exp(-e.deltaY * .001), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener('wheel', wheel, { passive: false }); return () => el.removeEventListener('wheel', wheel);
  }, [work, loading, board?.surface]);
  const drag = useRef<{ x: number; y: number; startX: number; startY: number; card?: Card; resize?: boolean } | null>(null);
  function startDrag(e: PointerEvent, card?: Card, resize = false) {
    if (e.button !== 0 || (!resize && (e.target as Element).closest('button'))) return;
    if (!card && (e.target as Element).closest('.work-card')) return;
    e.preventDefault(); e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId);
    const v = boardRef.current!.viewport;
    drag.current = { x: e.clientX, y: e.clientY, startX: v.x, startY: v.y, card, resize };
  }
  function move(e: PointerEvent) {
    const d = drag.current; const b = boardRef.current; if (!d || !b) return;
    const dx = e.clientX - d.x; const dy = e.clientY - d.y;
    if (d.card) updateCard(key(d.card), d.resize ? { width: bound(d.card.width + dx / b.viewport.zoom, 300, 1400), height: bound(d.card.height + dy / b.viewport.zoom, 220, 1400) } :
      { x: bound(d.card.x + dx / b.viewport.zoom, -100000, 100000), y: bound(d.card.y + dy / b.viewport.zoom, -100000, 100000) });
    else changeBoard({ ...b, viewport: { ...b.viewport, x: bound(d.startX + dx, -100000, 100000), y: bound(d.startY + dy, -100000, 100000) } });
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); if (busy) return; setBusy(true); setError('');
    const submittedWorkId = workRef.current?.id;
    const path = form === 'work' ? '/api/v1/works' : `/api/v1/works/${work!.id}/${form === 'task' ? 'tasks' : 'artifacts'}`;
    const fields = form === 'work' ? { title: draft.title, owner: draft.owner, requirement: draft.body } : form === 'task' ?
      { expected_revision: work!.revision, title: draft.title, body: draft.body, assignee: draft.owner || null } :
      { expected_revision: work!.revision, title: draft.title, body: draft.body, requirement_version: work!.requirement_version, artifact_id: draft.artifact || null };
    const signature = JSON.stringify([path, fields]);
    if (request.current?.signature !== signature) request.current = { signature, id: crypto.randomUUID() };
    try {
      const result = await client.post<{ work_id: string; artifact_id?: string; task_id?: string; version?: number }>(path, { ...fields, request_id: request.current.id });
      if (form === 'work') { await openWork(result.work_id); void list().catch(() => {}); }
      else {
        const detail = await client.get<Work>(`/api/v1/works/${result.work_id}`);
        if (workRef.current?.id === submittedWorkId) {
          workRef.current = detail; setWork(detail);
          if (boardRef.current) changeBoard(withSpace(boardRef.current, detail));
          if (result.artifact_id) addCard('artifact', result.artifact_id, result.version!);
          if (result.task_id) addCard('task', result.task_id, null);
        }
      }
      setForm(null); setDraft({ title: '', owner: '', body: '', artifact: '' }); request.current = null;
    } catch (err) { setError(message(err)); }
    finally { setBusy(false); }
  }
  function back() {
    const session = workRef.current && sessions.current.get(workRef.current.id);
    if (session) void saveView(session);
    ++loadSequence.current;
    workRef.current = null; setWork(null); setBoard(null); boardRef.current = null;
    setAppearancePanel(null); setFocus(null); setLibraryOpen(false); setMenuOpen(false); setRelationshipsOpen(false); setRestOpen(false); setAgentPanel(null); setSessionPanel(false); setDeliveryPanel(false); setDeliveryOutput(null);
    const url = new URL(location.href); url.searchParams.delete('work'); history.replaceState(null, '', url);
    setLoading(true); void list().catch(() => setError(t.failed)).finally(() => setLoading(false));
  }
  function returnToSpace() { const current = boardRef.current; if (current) changeBoard({ ...current, surface: 'space' }); }
  function continueBoard() {
    const current = boardRef.current; if (!current || !workRef.current) return;
    if (!current.cards.length) {
      const detail = workRef.current;
      const latest = [...detail.artifacts].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '') || b.version - a.version).slice(0, 1);
      const cards: Card[] = [initialBoard(detail.id, detail.requirement_version).cards[0], ...latest.map((a, index) => ({
        kind: 'artifact' as const, ref_id: a.id, version: a.version, x: 440 + index * 440, y: 0, width: 400, height: 430, scroll: 0 }))];
      fitOnBoardOpen.current = true;
      changeBoard({ ...current, surface: 'board', cards, viewport: { x: 24, y: 24, zoom: 1 } });
    } else changeBoard({ ...current, surface: 'board' });
  }
  const active = board?.cards.find(c => key(c) === focus);
  const space = board?.space; const inSpace = board?.surface !== 'board';
  const relatedNodes = space?.nodes.filter(node => node.kind !== 'requirement') ?? [];
  const visibleNodes = [...(space?.nodes.filter(node => node.kind === 'requirement' && node.version === work?.requirement_version) ?? []),
    ...((relationshipsOpen || relationsRetained) ? relatedNodes.slice(0, relationLimit) : [])];
  const coreNode = visibleNodes.find(node => node.kind === 'requirement');
  // Agents sit just outside the tasks they carry; unlinked agents wait above the core.
  const agentNodes = agents.items.slice(0, 4).reduce<{ agent: WorkAgent; x: number; y: number }[]>((placed, agent, index) => {
    const linked = visibleNodes.filter(node => node.kind === 'task' && agent.task_ids?.includes(node.ref_id));
    let x = linked.length ? Math.min(...linked.map(node => node.x)) - 330 : (index % 2 ? 1 : -1) * 120;
    let y = linked.length ? linked.reduce((sum, node) => sum + node.y, 0) / linked.length : -360;
    while (placed.some(other => Math.abs(other.x - x) < 150 && Math.abs(other.y - y) < 150)) y += 160;
    return [...placed, { agent, x: Math.round(x), y: Math.round(y) }];
  }, []);
  const edges = visibleNodes.flatMap(node => {
    if (node.kind === 'requirement') return [];
    const artifact = node.kind === 'artifact' ? work?.artifacts.find(a => a.id === node.ref_id && a.version === node.version) : null;
    const origin = artifact ? visibleNodes.find(other => other.kind === 'requirement' && other.version === artifact.requirement_version) : visibleNodes.find(other => other.kind === 'requirement' && other.ref_id === work?.id);
    return origin ? [[key(origin), key(node)] as const] : [];
  }) ?? [];
  const agentEdges = relationshipsOpen ? agentNodes.flatMap(node => (node.agent.task_ids ?? []).map(id => [`agent:${node.agent.agent_id}`, `task:${id}:`] as const)) : [];
  const highlighted = hover ? new Set([hover, ...[...edges, ...agentEdges].flatMap(([a, b]) => a === hover ? [b] : b === hover ? [a] : [])]) : null;
  function showOnBoard(node: SpaceNode) { addCard(node.kind, node.ref_id, node.version); const current = boardRef.current; if (current) changeBoard({ ...current, surface: 'board' }); }
  async function refreshDeliveryWork(id: string) {
    const sequence = loadSequence.current;
    try {
      const detail = await client.get<Work>(`/api/v1/works/${id}`);
      if (!mounted.current || workRef.current?.id !== id || sequence !== loadSequence.current || detail.revision < workRef.current.revision) return;
      workRef.current = detail; setWork(detail);
      if (boardRef.current) changeBoard(withSpace(boardRef.current, detail));
    } catch (err) { if (mounted.current && workRef.current?.id === id && sequence === loadSequence.current) setError(message(err)); }
  }
  function compareOnBoard(kind: Card['kind'], refId: string, version: number) {
    showOnBoard({ kind, ref_id: refId, version, x: 0, y: 0 });
    const current = boardRef.current, card = current?.cards.find(item => item.kind === kind && item.ref_id === refId && item.version === version);
    const width = (canvas.current ?? spaceCanvas.current)?.clientWidth ?? window.innerWidth;
    const panelWidth = document.querySelector('.work-sessions, .work-delivery')?.getBoundingClientRect().width ?? 640;
    const available = width - panelWidth - 32;
    if (!current || !card) return;
    if (available < 300) { if (deliveryPanel) { setDeliveryPanel(false); deliveryReturnsToSession.current = false; } else closeSessions(); return; }
    const zoom = bound(Math.min(current.viewport.zoom, available / card.width), .25, 2);
    changeBoard({ ...current, viewport: { x: 12 - card.x * zoom, y: 24 - card.y * zoom, zoom } });
  }
  function compareRequirement(version: number) { compareOnBoard('requirement', work!.id, version); }
  function fitSpace() { const current = boardRef.current, el = spaceCanvas.current; if (!current?.space?.nodes.length || !el) return;
    const bounds = [...visibleNodes, ...agentNodes].map(node => {
      const kind = 'kind' in node ? node.kind : 'agent';
      const halfWidth = kind === 'requirement' ? 150 : kind === 'task' ? 115 : kind === 'artifact' ? 130 : 75;
      const halfHeight = kind === 'requirement' ? 150 : kind === 'task' ? 21 : kind === 'artifact' ? 18 : 75;
      return { left: node.x - halfWidth, right: node.x + halfWidth, top: node.y - halfHeight, bottom: node.y + halfHeight };
    });
    const left = Math.min(...bounds.map(n => n.left)), right = Math.max(...bounds.map(n => n.right));
    const top = Math.min(...bounds.map(n => n.top)), bottom = Math.max(...bounds.map(n => n.bottom));
    const zoom = bound(Math.min(Math.max(1, el.clientWidth - 32) / (right - left), Math.max(1, el.clientHeight - 32 - 80) / (bottom - top), 1.4), .25, 2);
    changeBoard({ ...current, space: { ...current.space, viewport: { x: -(left + right) / 2 * zoom, y: -(top + bottom) / 2 * zoom - 40, zoom } } });
  }
  function spaceZoom(factor: number) { const current = boardRef.current; if (!current?.space) return;
    changeBoard({ ...current, space: { ...current.space, viewport: { ...current.space.viewport, zoom: bound(current.space.viewport.zoom * factor, .25, 2) } } }); }
  // First reveal of relations frames everything once; a viewport the user set is left alone.
  const fitOnReveal = useRef(false);
  useEffect(() => { if (relationshipsOpen && fitOnReveal.current) { fitOnReveal.current = false; fitSpace(); } }, [relationshipsOpen]);
  const spaceDrag = useRef<{ x: number; y: number; startX: number; startY: number; node?: SpaceNode; moved: boolean } | null>(null);
  const spaceCanvas = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = spaceCanvas.current; if (!el) return;
    const wheel = (e: WheelEvent) => { if (!relationshipsOpen) return; e.preventDefault(); const current = boardRef.current; if (!current?.space) return;
      const rect = el.getBoundingClientRect(), x = e.clientX - rect.left - rect.width / 2, y = e.clientY - rect.top - rect.height / 2;
      const old = current.space.viewport, z = bound(old.zoom * Math.exp(-e.deltaY * .001), .25, 2), ratio = z / old.zoom;
      changeBoard({ ...current, space: { ...current.space, viewport: { x: bound(x - (x - old.x) * ratio, -100000, 100000), y: bound(y - (y - old.y) * ratio, -100000, 100000), zoom: z } } });
    };
    el.addEventListener('wheel', wheel, { passive: false }); return () => el.removeEventListener('wheel', wheel);
  }, [work, board?.surface, relationshipsOpen]);
  function spaceMove(e: PointerEvent) { const d = spaceDrag.current; const current = boardRef.current; if (!d || !current?.space) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y; if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
    if (!d.moved) return;
    const { viewport, nodes } = current.space;
    changeBoard({ ...current, space: d.node ? { viewport, nodes: nodes.map(n => key(n) === key(d.node!) ? { ...n, x: bound(d.startX + dx / viewport.zoom, -100000, 100000), y: bound(d.startY + dy / viewport.zoom, -100000, 100000) } : n) } :
      { nodes, viewport: { ...viewport, x: bound(d.startX + dx, -100000, 100000), y: bound(d.startY + dy, -100000, 100000) } } });
  }
  function spaceStart(e: PointerEvent, node?: SpaceNode) { if (!relationshipsOpen || e.button !== 0 || !boardRef.current?.space) return;
    e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId);
    const viewport = boardRef.current.space.viewport;
    spaceDrag.current = { x: e.clientX, y: e.clientY, startX: node?.x ?? viewport.x, startY: node?.y ?? viewport.y, node, moved: false };
  }
  const suppressSpaceClick = useRef(false);
  function spaceEnd(e: PointerEvent) { suppressSpaceClick.current = !!spaceDrag.current?.moved; spaceDrag.current = null; e.stopPropagation(); }
  const renderContent = (card: Card, reading = false) => <CardContent client={client} work={work!} card={card} onScroll={scroll => { if (!reading) updateCard(key(card), { scroll }); }} />;
  return <div className="work-layout" aria-hidden={!visible}>
    <WorkMenuBar onNewWork={() => setForm('work')} disabled={busy || Boolean(editor || linksPanel || form || active || agentPanel || appearancePanel || restOpen || sessionPanel || deliveryPanel || recoveryPanel)}
      onSave={work && status !== 'conflict' && status !== 'saving' ? () => void saveView() : undefined}
      onAllWork={work ? back : undefined}
      onNewTask={work ? () => setForm('task') : undefined} onNewArtifact={work ? () => setForm('artifact') : undefined}
      onEditWork={work ? () => setEditor({ mode: 'work' }) : undefined}
      onReviseRequirement={work ? () => setEditor({ mode: 'requirement' }) : undefined}
      onEditTask={work?.tasks.length ? () => setEditor({ mode: 'task' }) : undefined}
      onSessions={work ? () => openSessions() : undefined} onDelivery={work ? () => openDelivery() : undefined}
      onRecovery={work ? openRecovery : undefined} onProtectionLinks={work ? () => setLinksPanel(true) : undefined}
      onAgents={work ? () => openAgents() : undefined} onAppearance={work ? () => openAppearance() : undefined}
      onBoard={work ? continueBoard : undefined} onSpace={work ? returnToSpace : undefined} on3d={work ? () => setRestOpen(true) : undefined} />
    <div className="work-layout-content">
    <WorkSidebar page="work" items={items} activeId={work?.id} onOpen={id => { if (id !== work?.id) void openWork(id); }}
      onCreate={() => setForm('work')} onNavigate={next => { if (next === 'work') back(); else onNavigate?.(next); }}
      hasMore={more} onMore={() => void list(true).catch(() => setError(t.failed))}
      navigationLocked={busy || Boolean(editor || linksPanel || form || active || agentPanel || appearancePanel || restOpen || sessionPanel || deliveryPanel || recoveryPanel)} />
    <section className={`work-page ${restOpen ? 'is-resting' : ''} ${active ? 'is-reading' : ''} ${agentPanel ? 'is-agents' : ''} ${appearancePanel ? 'is-appearance' : ''}`} aria-hidden={!visible}>
    {error && <div role="alert" className="work-error">{error} <button onClick={() => { if (work) void client.get<Work>(`/api/v1/works/${work.id}`).then(w => { workRef.current = w; setWork(w); setError(''); }).catch(() => setError(t.failed)); else void list().then(() => setError('')).catch(() => setError(t.failed)); }}>{t.reload}</button></div>}
    {loading ? <p role="status">{t.load}</p> : !work ? <>
      <div className="work-overview">
        <header className="work-overview-head"><div><h1>{t.title}</h1><p>{items.length ? t.overviewCount(items.length) : t.intro}</p></div>
          </header>
        {!items.length && <div className="work-empty">{t.empty}</div>}
        {items.length > 0 && <div className="work-empty">{t.selectWork}</div>}
      </div>
    </> : <>
      <header className="work-toolbar"><button className="work-back" onClick={back}><Icon name="back" />{t.back}</button><div className="work-heading"><strong>{work.title}</strong><span className="work-version">{t.requirement} v{work.requirement_version}</span></div>
        <button ref={menuButton} className="work-icon-button" aria-label={t.actions} title={t.actions} aria-expanded={menuOpen} onClick={() => setMenuOpen(open => !open)}><Icon name="more" /></button>
        {menuOpen && <div className="work-menu"><button onClick={() => openSessions()}>{t.sessions}</button><button onClick={() => openDelivery()}>{t.delivery}</button><button onClick={openRecovery}>{t.recovery}</button><button onClick={() => { setRecoveryPanel(false); setForm('task'); setMenuOpen(false); }}>{t.newTask}</button><button onClick={() => { setRecoveryPanel(false); setForm('artifact'); setMenuOpen(false); }}>{t.newArtifact}</button></div>}</header>
      <WorkExecutionStatus key={work.id} client={client} workId={work.id} visible={visible} onOpen={openSessions}
        onRequirement={version => { if (workRef.current?.id === work.id && workRef.current.requirement_version < version) void refreshDeliveryWork(work.id); }} />
      {!inSpace && <div className="work-tools"><button className="work-quiet" onClick={returnToSpace}><Icon name="back" />{t.returnSpace}</button><button className="work-quiet" onClick={() => setLibraryOpen(open => !open)} aria-expanded={libraryOpen}><Icon name="library" />{t.addRelated}</button><div className="work-zoom"><button className="work-icon-button" aria-label={t.zoomOut} onClick={() => zoom(1 / 1.2)}><Icon name="minus" /></button><span>{Math.round((board?.viewport.zoom ?? 1) * 100)}%</span><button className="work-icon-button" aria-label={t.zoomIn} onClick={() => zoom(1.2)}><Icon name="plus" /></button><button className="work-quiet" onClick={fit}><Icon name="fit" />{t.fit}</button></div></div>}
      {inSpace && space && <div className="work-space" ref={spaceCanvas} onPointerDown={e => { if (e.target === e.currentTarget || (e.target as Element).closest('.work-space-world') && !(e.target as Element).closest('.work-space-node')) spaceStart(e); }} onPointerMove={spaceMove} onPointerUp={e => { if (spaceDrag.current && !spaceDrag.current.node) spaceEnd(e); }} onPointerCancel={() => { spaceDrag.current = null; }}>
        <div className="work-space-world" style={{ transform: relationshipsOpen ? `translate(calc(50% + ${space.viewport.x}px), calc(50% + ${space.viewport.y}px)) scale(${space.viewport.zoom})` : `translate(calc(50% - ${coreNode?.x ?? 0}px), calc(50% - ${coreNode?.y ?? 0}px)) scale(1)` }}>
          <svg className="work-space-links" aria-hidden="true">{edges.map(([a, b]) => { const origin = space.nodes.find(n => key(n) === a)!, node = space.nodes.find(n => key(n) === b)!;
            return <path key={`${a}:${b}`} d={curve(origin, node)} className={highlighted ? (highlighted.has(a) && highlighted.has(b) ? 'lit' : 'dim') : ''} />; })}
            {relationshipsOpen && agentNodes.flatMap(node => (node.agent.task_ids ?? []).flatMap(id => {
              const task = visibleNodes.find(item => item.kind === 'task' && item.ref_id === id);
              return task ? <path key={`${node.agent.agent_id}:${id}`} data-agent-task={id} d={curve(node, task)} className={`agent-link ${highlighted ? (highlighted.has(`agent:${node.agent.agent_id}`) && highlighted.has(key(task)) ? 'lit' : 'dim') : ''}`}><title>{locale === 'zh-CN' ? '用户任务关联；执行活动未知' : 'User task link; execution activity unknown'}</title></path> : [];
            }))}</svg>
          {(relationshipsOpen || relationsRetained) && agentNodes.map(({ agent, x, y }, index) => <WorkBubbleGroup key={agent.agent_id}
            label={`${locale === 'zh-CN' ? 'Agent 子项' : 'Agent children'} ${agent.agent_id}`} width={150} height={150} style={{ left: x, top: y }} side={x < 0 ? 1 : -1} verticalSide={y < 0 ? -1 : 1}
            forceClosed={!relationshipsOpen} onHover={open => setHover(open ? `agent:${agent.agent_id}` : null)}
            actions={[{ id: 'details', label: locale === 'zh-CN' ? '管理 Agent' : 'Manage Agent', onSelect: () => openAgents(agent.agent_id) },
              { id: 'color', label: locale === 'zh-CN' ? '设置颜色' : 'Choose color', onSelect: () => openAppearance(agent.agent_id) },
              ...work.tasks.filter(task => agent.task_ids?.includes(task.id)).slice(0, 3).map(task => ({ id: task.id, label: task.title, onSelect: () => showOnBoard({ kind: 'task', ref_id: task.id, version: null, x: 0, y: 0 }) }))]}
            renderTrigger={triggerProps => <AgentBubble agent={agent} selectedTints={appearances.tints} index={index} size={150} tasks={work.tasks} copy={t} triggerProps={triggerProps}
              className={highlighted && !highlighted.has(`agent:${agent.agent_id}`) ? 'dim' : highlighted ? 'lit' : ''} style={{}} onClick={() => openAgents(agent.agent_id)} />} />)}
          {visibleNodes.map(node => { const item = node.kind === 'task' ? work.tasks.find(x => x.id === node.ref_id) : node.kind === 'artifact' ? work.artifacts.find(x => x.id === node.ref_id && x.version === node.version) : null;
            if (node.kind !== 'requirement' && !item) return null;
            const state = highlighted ? (highlighted.has(key(node)) ? 'lit' : 'dim') : '';
            const trigger = (triggerProps?: ButtonHTMLAttributes<HTMLButtonElement>) => <button key={key(node)} {...triggerProps} className={`work-space-node ${node.kind} ${state} ${triggerProps?.className ?? ''}`} style={node.kind === 'task' ? {} : { left: node.x, top: node.y }} onPointerDown={e => { e.stopPropagation(); spaceStart(e, node); }} onPointerUp={spaceEnd} onPointerEnter={() => setHover(key(node))} onPointerLeave={() => setHover(null)} onFocus={() => setHover(key(node))} onBlur={() => setHover(null)} onClick={() => { if (!suppressSpaceClick.current) showOnBoard(node); suppressSpaceClick.current = false; }} aria-label={`${t.open} ${item?.title ?? work.title}`}>
              {node.kind === 'requirement' ? <><WorkCore size={300} /><span className="work-core-text"><strong>{work.title}</strong>{goalOf(work.requirement) && <span className="work-core-goal">{goalOf(work.requirement)}</span>}<span className="work-core-counts"><span>{t.requirement} v{node.version}</span><span>{t.taskCount(work.tasks.length)}</span><span>{t.artifactCount(work.artifacts.length)}</span></span></span></> :
                <><span className="work-anchor-icon"><Icon name={node.kind} size={16} /></span><strong>{item?.title ?? work.title}</strong>{node.version ? <em className="work-version">v{node.version}</em> : <small>{t[node.kind]}</small>}</>}
            </button>;
            return node.kind === 'task' ? <WorkBubbleGroup key={key(node)} label={`${locale === 'zh-CN' ? '任务关联' : 'Task links'} ${item?.title ?? node.ref_id}`}
              width={230} height={42} style={{ left: node.x, top: node.y }} side={node.x < 0 ? 1 : -1} verticalSide={node.y < 0 ? -1 : 1} pinOnClick={false} forceClosed={!relationshipsOpen}
              pinLabel={locale === 'zh-CN' ? '固定子项' : 'Pin children'} unpinLabel={locale === 'zh-CN' ? '取消固定' : 'Unpin children'}
              onHover={open => setHover(open ? key(node) : null)} renderTrigger={trigger}
              actions={[{ id: 'open-task', label: `${t.open} ${item?.title ?? node.ref_id}`, onSelect: () => showOnBoard(node) },
                ...agents.items.filter(agent => agent.task_ids?.includes(node.ref_id)).slice(0, 3).map(agent => ({ id: agent.agent_id,
                  label: `${AGENT_NAMES[agent.detected_identity] ?? agent.detected_identity} · ${agent.agent_id.slice(-6)}`, onSelect: () => openAgents(agent.agent_id) }))]} /> : trigger(); })}
        </div>{!relationshipsOpen && !relationsRetained && <><div className="work-agent-bubbles">
          <svg width="100%" height="100%" aria-hidden="true">{agents.items.slice(0, 4).map((agent, index) => {
            const [x, y] = [[22, 30], [78, 67], [22, 67], [78, 30]][index];
            return <line key={agent.agent_id} x1="50%" y1="50%" x2={`${x}%`} y2={`${y}%`} className="work-agent-tether" />;
          })}</svg>
          {agents.items.slice(0, 4).map((agent, index) => { const [x, y] = [[22, 30], [78, 67], [22, 67], [78, 30]][index];
            return <WorkBubbleGroup key={agent.agent_id} label={`${locale === 'zh-CN' ? 'Agent 子项' : 'Agent children'} ${agent.agent_id}`}
              width={170} height={170} style={{ left: `${x}%`, top: `${y}%` }} side={x < 50 ? 1 : -1} verticalSide={y < 50 ? -1 : 1}
              actions={[{ id: 'details', label: locale === 'zh-CN' ? '管理 Agent' : 'Manage Agent', onSelect: () => openAgents(agent.agent_id) },
              { id: 'color', label: locale === 'zh-CN' ? '设置颜色' : 'Choose color', onSelect: () => openAppearance(agent.agent_id) },
                ...work.tasks.filter(task => agent.task_ids?.includes(task.id)).slice(0, 3).map(task => ({ id: task.id, label: task.title, onSelect: () => showOnBoard({ kind: 'task', ref_id: task.id, version: null, x: 0, y: 0 }) }))]}
              renderTrigger={triggerProps => <AgentBubble agent={agent} selectedTints={appearances.tints} index={index} size={170} tasks={work.tasks} copy={t} style={{}} triggerProps={triggerProps} onClick={() => openAgents(agent.agent_id)} />} />;
          })}</div></>}</div>}
      {!inSpace && <div className="work-area">{libraryOpen && <aside className="work-library"><div className="work-library-head"><h2>{t.library}</h2><button className="work-icon-button" onClick={() => setLibraryOpen(false)} aria-label={t.close}><Icon name="close" size={16} /></button></div>
        <label>{t.requirement}<input aria-label={t.showRequirement} type="number" min={1} max={work.requirement_version} value={requirementVersion} onChange={e => setRequirementVersion(bound(Number(e.target.value), 1, work.requirement_version))} /></label>
        <button onClick={() => addCard('requirement', work.id, requirementVersion)}>{t.open} · {t.requirement} v{requirementVersion}</button>
        <h3>{t.task} · {work.tasks.length}</h3>{work.tasks.map(task => <button key={task.id} onClick={() => addCard('task', task.id, null)}>{task.title}</button>)}
        <h3>{t.artifact} · {work.artifacts.length}</h3>{!work.artifacts.length && <p>{t.none}</p>}
        {work.artifacts.map(a => <button key={`${a.id}:${a.version}`} onClick={() => addCard('artifact', a.id, a.version)}>{a.title}<small>v{a.version} · {t.basedOn} v{a.requirement_version}{a.created_at ? ` · ${new Date(a.created_at).toLocaleString(locale)}` : ''}</small></button>)}
      </aside>}<div className="work-canvas" ref={canvas} onPointerDown={e => startDrag(e)} onPointerMove={move} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
        {!board?.cards.length && <div className="work-canvas-empty"><p>{t.noCards}</p><button onClick={() => addCard('requirement', work.id, work.requirement_version)}>{t.showRequirement}</button><button onClick={() => setLibraryOpen(true)}>{t.addRelated}</button></div>}
        <div className="work-world" style={{ transform: `translate(${board?.viewport.x}px, ${board?.viewport.y}px) scale(${board?.viewport.zoom})` }}>
          <svg className="work-links" width="1" height="1" aria-hidden="true">{board?.cards.filter(c => c.kind === 'artifact').map(a => {
            const version = work.artifacts.find(item => item.id === a.ref_id && item.version === a.version)?.requirement_version;
            const from = board.cards.find(c => c.kind === 'requirement' && c.version === version);
            return from ? <path key={key(a)} d={`M${from.x + from.width},${from.y + 80} C${from.x + from.width + 50},${from.y + 80} ${a.x - 50},${a.y + 80} ${a.x},${a.y + 80}`} /> : null;
          })}</svg>
          {board?.cards.map(card => <article className={`work-card${card.kind === 'task' ? ' work-paper-note work-card-task' : card.kind === 'requirement' ? ' work-paper-note work-card-requirement' : ''}`} key={key(card)} data-card={key(card)} style={{ left: card.x, top: card.y, width: card.width, height: card.height }}>
            <header onPointerDown={e => startDrag(e, card)}>{card.kind !== 'artifact' && <TaskPin />}<span className="work-card-kind"><Icon name={card.kind} size={16} />{t[card.kind]}{card.version ? <em className="work-version">v{card.version}</em> : null}</span><button className="work-icon-button" title={t.focus} aria-label={`${t.focus} ${t[card.kind]} ${card.version ?? ''}`} onClick={e => { readingOpener.current = e.currentTarget; setFocus(key(card)); }}><Icon name="expand" size={16} /></button><button className="work-icon-button" title={t.close} aria-label={`${t.close} ${t[card.kind]} ${card.version ?? ''}`} onClick={() => changeBoard({ ...boardRef.current!, cards: boardRef.current!.cards.filter(c => key(c) !== key(card)) })}><Icon name="close" size={16} /></button></header>
            {renderContent(card)}<button className="work-resize" aria-label={t.resize(t[card.kind])} onPointerDown={e => startDrag(e, card, true)} />
          </article>)}
        </div></div></div>}
      <nav className="work-dock" aria-label={t.workspaceControls}>
        {inSpace && <div className="work-dock-group"><button className="work-primary" onClick={continueBoard}><Icon name="board" />{t.continueBoard}</button><button className={`work-toggle${relationshipsOpen ? ' is-on' : ''}`} onClick={() => { const v = boardRef.current?.space?.viewport; fitOnReveal.current = !relationshipsOpen && !!v && v.x === 0 && v.y === 0 && v.zoom === 1; setRelationshipsOpen(open => !open); }} aria-expanded={relationshipsOpen}><Icon name="link" />{relationshipsOpen ? t.hideRelations : t.showRelations}</button>{relationshipsOpen && relatedNodes.length > relationLimit && <button className="work-quiet" onClick={() => setRelationLimit(n => n + 12)}>{t.moreRelations}</button>}</div>}
        {inSpace && relationshipsOpen && <div className="work-zoom"><button className="work-icon-button" aria-label={t.zoomOut} onClick={() => spaceZoom(1 / 1.2)}><Icon name="minus" /></button><span>{Math.round((space?.viewport.zoom ?? 1) * 100)}%</span><button className="work-icon-button" aria-label={t.zoomIn} onClick={() => spaceZoom(1.2)}><Icon name="plus" /></button><button className="work-icon-button" aria-label={t.fit} title={t.fit} onClick={fitSpace}><Icon name="fit" /></button></div>}
        <div className="work-dock-group">
          <span role="status" className={`work-save is-${status}`} title={status === 'conflict' ? t.viewConflict : undefined}>{status === 'saved' ? t.saved : status === 'saving' ? t.saving : status === 'failed' ? t.saveFailed : status === 'conflict' ? t.viewConflict : t.dirty}</span>
          <button className="work-icon-button" title={t.saveView} aria-label={t.saveView} disabled={status === 'saving' || status === 'conflict'} onClick={() => void saveView()}><Icon name="save" /></button>
          {(status === 'failed' || status === 'conflict') && <button className="work-quiet" onClick={() => { sessions.current.delete(work.id); void openWork(work.id); }}>{t.discardView}</button>}
        </div>
        <div className="work-dock-group">
          <button className="work-quiet work-dock-agents" title={t.agents} aria-label={t.agents} onClick={() => openAgents()}><Icon name="agents" /><span className="work-count">{agents.failed ? '!' : agents.items.length}</span></button>
          <button className="work-icon-button" title={locale === 'zh-CN' ? 'Agent 颜色' : 'Agent colors'} aria-label={locale === 'zh-CN' ? 'Agent 颜色' : 'Agent colors'} onClick={() => openAppearance()}><Icon name="palette" /></button>
          <button className="work-icon-button" title={t.restScene} aria-label={t.restScene} onClick={() => setRestOpen(true)}><Icon name="orbit" /></button>
        </div></nav>
    </>}
    {work && visible && editor && <WorkEditor key={`${work.id}:${editor.mode}:${editor.taskId ?? ''}`} client={client} work={work} mode={editor.mode} taskId={editor.taskId}
      onClose={() => setEditor(null)} onChanged={async () => {
        const id = work.id;
        const detail = await client.get<Work>(`/api/v1/works/${id}`);
        if (workRef.current?.id !== id) return;
        workRef.current = detail; setWork(detail);
        if (boardRef.current) changeBoard(withSpace(boardRef.current, detail));
        await list();
      }} />}
    {work && <WorkProtectionLinks key={work.id} client={client} workId={work.id} visible={visible && linksPanel}
      onClose={() => setLinksPanel(false)} onChanged={() => void refreshDeliveryWork(work.id)} />}
    <WorkRecovery protectionCommand={protectionCommand} onProtectionCommandChange={setProtectionCommand} client={client} visible={recoveryPanel && visible && Boolean(work) && !loading && !restOpen && !agentPanel && !form && !active} onClose={closeRecovery} />
    {work && <WorkSessions protectionCommand={protectionCommand} onProtectionCommandChange={setProtectionCommand} key={work.id} client={client} workId={work.id} revision={work.revision} requirementVersion={work.requirement_version} artifacts={work.artifacts} target={executionTarget} tasks={work.tasks} visible={sessionPanel && !deliveryPanel && !recoveryPanel && visible && !restOpen && !agentPanel && !form && !active} memory={sessionMemory.current} onClose={closeSessions} onOpenRequirement={compareRequirement} onDelivery={openDelivery} onRevision={revision => {
      const current = workRef.current; if (current?.id === work.id) { const next = { ...current, revision }; workRef.current = next; setWork(next); }
    }} />}
    {work && <WorkDelivery key={`delivery:${work.id}`} client={client} workId={work.id} revision={work.revision} requirementVersion={work.requirement_version} owner={work.owner} artifacts={work.artifacts} output={deliveryOutput} visible={deliveryPanel && !recoveryPanel && visible && !restOpen && !agentPanel && !form && !active} memory={sessionMemory.current} onClose={closeDelivery} onOpenRequirement={compareRequirement} onChanged={() => refreshDeliveryWork(work.id)} onShowArtifact={(id, version) => {
      if (workRef.current?.id !== work.id) return;
      compareOnBoard('artifact', id, version);
    }} onRevision={revision => {
      const current = workRef.current; if (current?.id === work.id) { const next = { ...current, revision: Math.max(current.revision, revision) }; workRef.current = next; setWork(next); }
    }} />}
    {restOpen && work && visible && <div className="work-rest-layer"><Suspense fallback={<div className="work-rest-loading"><button autoFocus onClick={() => setRestOpen(false)}>← {t.returnBoard}</button><p role="status">{t.load}</p></div>}><WorkRestScene title={work.title} requirementVersion={work.requirement_version} taskCount={work.tasks.length} artifactCount={work.artifacts.length} updatedAt={work.updated_at} agentTints={appearances.tints} agents={agents.items} tasks={work.tasks.map(task => ({ ...task, assignee_agent_ids: agents.items.filter(agent => agent.task_ids?.includes(task.id)).map(agent => agent.agent_id) }))} onTaskSelect={id => { setRestOpen(false); showOnBoard({ kind: 'task', ref_id: id, version: null, x: 0, y: 0 }); }} onAgentSelect={openAgents} onManageAgents={() => openAgents()} onReturn={() => setRestOpen(false)} /></Suspense></div>}
    {agentPanel && work && <WorkAgents key={work.id} client={client} workId={work.id} revision={work.revision} linked={agents.items} tasks={work.tasks} initialAgentId={agentPanel.selected} visible={visible} onClose={closeAgents} onLinkedChanged={agents.replace} onRevision={revision => {
      const current = workRef.current; if (current?.id === work.id) { const next = { ...current, revision }; workRef.current = next; setWork(next); }
    }} />}
    {appearancePanel && work && <AgentAppearancePicker agents={agents.items} state={appearances} selected={appearancePanel.selected} onClose={closeAppearance} />}
    {active && work && <div className="work-overlay work-reading-overlay" role="dialog" aria-modal="true" aria-label={t.focus}><section className="work-reader"><header><button className="work-quiet" autoFocus onClick={() => setFocus(null)}><Icon name="back" />{t.returnBoard}</button><span className="work-card-kind"><Icon name={active.kind} size={16} />{t[active.kind]}{active.version ? <em className="work-version">v{active.version}</em> : null}</span></header>{renderContent(active, true)}</section></div>}
    {form && <div className="work-overlay" role="dialog" aria-modal="true" aria-label={form === 'work' ? t.create : form === 'task' ? t.newTask : t.newArtifact}><form className="work-form" onSubmit={submit}>
      <h2>{form === 'work' ? t.create : form === 'task' ? t.newTask : t.newArtifact}</h2>
      {error && <p role="alert">{error}</p>}
      <label>{form === 'work' ? t.name : t.titleField}<input autoFocus required maxLength={200} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
      {form !== 'artifact' && <label>{form === 'work' ? t.owner : t.assignee}<input required={form === 'work'} maxLength={120} value={draft.owner} onChange={e => setDraft({ ...draft, owner: e.target.value })} /></label>}
      {form === 'artifact' && <label>{t.versionOf}<select value={draft.artifact} onChange={e => setDraft({ ...draft, artifact: e.target.value })}><option value="">{t.standalone}</option>{work?.artifacts.filter((a, i, all) => all.findIndex(x => x.id === a.id) === i).map(a => <option value={a.id} key={a.id}>{a.title}</option>)}</select></label>}
      <label>{form === 'work' ? t.requirement : t.body}<textarea required={form !== 'task'} maxLength={100000} rows={12} value={draft.body} onChange={e => setDraft({ ...draft, body: e.target.value })} /></label>
      {form === 'artifact' && <small>{t.manual} · {t.basedOn} v{work?.requirement_version}</small>}
      <footer><button type="button" disabled={busy} onClick={() => { setForm(null); setError(''); }}>{t.cancel}</button><button className="work-primary" disabled={busy}>{busy ? t.load : t.save}</button></footer>
    </form></div>}
  </section></div></div>;
}

/** Decorative plastic cap and steel needle; never changes layout/pinning state. */
function TaskPin() {
  const id = useId().replace(/:/g, '');
  return <span className="work-task-pin" aria-hidden="true"><svg viewBox="0 0 54 64" width="42" height="52" focusable="false">
    <defs>
      <radialGradient id={`${id}cap`} cx="30%" cy="20%" r="85%"><stop stopColor="#ef9083" /><stop offset=".42" stopColor="#bf5044" /><stop offset="1" stopColor="#7c2b26" /></radialGradient>
      <linearGradient id={`${id}body`}><stop stopColor="#7b2c27" /><stop offset=".35" stopColor="#d56857" /><stop offset=".72" stopColor="#a33c32" /><stop offset="1" stopColor="#61221f" /></linearGradient>
      <linearGradient id={`${id}steel`}><stop stopColor="#59615e" /><stop offset=".45" stopColor="#f6f8f2" /><stop offset="1" stopColor="#777f7b" /></linearGradient>
      <filter id={`${id}shadow`} x="-50%" y="-100%" width="200%" height="300%"><feGaussianBlur stdDeviation="2" /></filter>
    </defs>
    <ellipse cx="32" cy="49" rx="14" ry="4" fill="#312d24" opacity=".22" filter={`url(#${id}shadow)`} />
    <g transform="rotate(-20 26 29)">
      <path d="M26 34v21l1.4-3V34Z" fill={`url(#${id}steel)`} />
      <ellipse cx="26" cy="33" rx="12" ry="5" fill="#692720" />
      <path d="M17 17c2 4 3 9 0 13 4 5 14 5 18 0-3-4-2-9 0-13Z" fill={`url(#${id}body)`} />
      <ellipse cx="26" cy="17" rx="15" ry="7" fill="#752820" />
      <ellipse cx="26" cy="13" rx="15" ry="7" fill={`url(#${id}cap)`} />
      <ellipse cx="21" cy="10" rx="6" ry="2" fill="#fff4e8" opacity=".35" transform="rotate(-12 21 10)" />
      <path d="M19 23c.5 2 .4 4-.3 6" fill="none" stroke="#f3aa91" strokeOpacity=".45" strokeWidth="1.2" strokeLinecap="round" />
    </g>
  </svg></span>;
}

function CardContent({ client, work, card, onScroll }: { client: ApiClient; work: Work; card: Card; onScroll: (scroll: number) => void }) {
  const { locale } = useI18n(); const t = workspaceCopy[locale];
  const [content, setContent] = useState<Content | null>(null); const [error, setError] = useState<'contentBusy' | 'contentMissing' | 'contentConnection' | 'contentFailed' | null>(null); const [retry, setRetry] = useState(0);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let live = true; setContent(null); setError(null);
    const path = card.kind === 'requirement' ? `requirements/${card.version}` : card.kind === 'task' ? `tasks/${card.ref_id}` : `artifacts/${card.ref_id}/${card.version}`;
    void client.get<Content>(`/api/v1/works/${work.id}/${path}`).then(value => { if (live) setContent(value); }).catch(err => {
      if (!live) return;
      setError(err instanceof ApiRequestError && err.status === 503 ? 'contentBusy' :
        err instanceof ApiRequestError && err.status === 404 ? 'contentMissing' :
          err instanceof SessionUnavailableError || err instanceof ApiRequestError && (err.status === null || err.status === 401) ? 'contentConnection' : 'contentFailed');
    });
    return () => { live = false; };
  }, [client, work.id, card.kind, card.ref_id, card.version, retry, card.kind === 'task' ? work.revision : 0]);
  useEffect(() => { if (body.current && content && Math.abs(body.current.scrollTop - card.scroll) > 1) body.current.scrollTop = card.scroll; }, [content, card.scroll]);
  return <div className="work-card-body" ref={body} onScroll={e => { if (content && Math.abs(e.currentTarget.scrollTop - card.scroll) > 1) onScroll(e.currentTarget.scrollTop); }}>
    {error ? <p role="alert">{t[error]} <button onClick={() => setRetry(n => n + 1)}>{t.retry}</button></p> : content ? <>
      <h2>{content.title ?? `${work.title} · ${t.requirement}`}</h2>
      {content.requirement_version && <small className="work-provenance">{content.source === 'AGENT_RUN' ? t.runOutput : content.source === 'USER_EDITED_RUN_OUTPUT' ? t.editedRunOutput : t.manual} · {t.basedOn} v{content.requirement_version}</small>}
      {content.source_run_id && <code>{content.source_run_id}</code>}
      <div className="work-prose"><Prose text={content.body} /></div>
    </> : <p>{t.load}</p>}
  </div>;
}

type Copy = (typeof workspaceCopy)['en-US'];
/** Display names for known identities; anything else is shown verbatim. */
const AGENT_NAMES: Record<string, string> = { CODEX: 'Codex', CLAUDE_CODE: 'Claude Code', KIMI: 'Kimi', GEMINI: 'Gemini', CURSOR: 'Cursor' };
/**
 * Agent as a replaceable tool: a tinted film with only facts the Core reports.
 * Presence comes from discovery; execution activity stays explicitly unknown.
 */
function AgentBubble({ agent, selectedTints, index, size, tasks, copy, className = '', style, onClick, onPointerDown, onPointerEnter, onPointerLeave, triggerProps }: {
  agent: WorkAgent; selectedTints?: Record<string, string>; index: number; size: number; tasks: { id: string; title: string }[]; copy: Copy; className?: string;
  triggerProps?: ButtonHTMLAttributes<HTMLButtonElement>;
  style: CSSProperties; onClick: () => void; onPointerDown?: (event: PointerEvent) => void; onPointerEnter?: () => void; onPointerLeave?: () => void;
}) {
  const state = agent.presence === 'CURRENT' ? 'current' : agent.presence === 'NOT_CURRENT' ? 'absent' : 'unknown';
  const linked = tasks.filter(task => agent.task_ids?.includes(task.id));
  return <button style={{ ...style, width: size, height: size }} onClick={onClick}
    onPointerDown={onPointerDown} onPointerEnter={onPointerEnter} onPointerLeave={onPointerLeave} {...triggerProps} className={`work-agent-bubble is-${state} ${className} ${triggerProps?.className ?? ''}`}>
    <AgentFilm seed={agent.agent_id} size={size} tint={agentTint(agent.agent_id, selectedTints)} state={state} />
    <span className="work-agent-text">
      <strong>{AGENT_NAMES[agent.detected_identity] ?? agent.detected_identity}</strong>
      <span className="work-agent-state"><i aria-hidden="true" />{state === 'current' ? copy.presentNow : state === 'absent' ? copy.presentAbsent : copy.presentUnknown}</span>
      <span className="work-agent-load">{copy.carries(linked.length)}</span>
      {linked[0] && size > 140 && <span className="work-agent-task">{linked[0].title}</span>}
      <small>{copy.activityUnknown}</small>
    </span>
  </button>;
}
