import { useEffect, useRef, useState } from 'react';
import { ApiActionError, type ApiClient } from '../api/client';
import { AGENT_COLOR_OPTIONS } from '../pages/workAppearance';

export type AgentAppearance = { agent_id: string; color: string | null; revision: number; updated_at: string | null };
type Command = { request_id: string; expected_revision: number; color: string | null };
type Session = { rows: AgentAppearance[]; phase: 'loading' | 'ready' | 'saving' | 'uncertain' | 'conflict' | 'failed';
  pending: { agentId: string; command: Command } | null; sequence: number };
const palette = new Set<string>(AGENT_COLOR_OPTIONS.map(option => option.tint));

/** All pending receipts stay with their Work even while its panel is closed. */
export function useAgentAppearances(client: ApiClient, workId?: string, linkedSignature = '') {
  const sessions = useRef(new Map<string, Session>());
  const active = useRef(workId); active.current = workId;
  const mounted = useRef(true);
  const [, redraw] = useState(0);
  const notify = (id: string) => { if (mounted.current && active.current === id) redraw(value => value + 1); };
  const sessionFor = (id: string) => {
    let session = sessions.current.get(id);
    if (!session) { session = { rows: [], phase: 'loading', pending: null, sequence: 0 }; sessions.current.set(id, session); }
    return session;
  };
  const refresh = async (id = workId) => {
    if (!id) return;
    const session = sessionFor(id);
    if (session.pending) return;
    const sequence = ++session.sequence;
    session.phase = 'loading'; notify(id);
    try {
      const result = await client.get<{ work_id: string; items: AgentAppearance[] }>(`/api/v1/works/${id}/agent-appearances`);
      if (sequence !== session.sequence) return;
      if (result.work_id !== id || !Array.isArray(result.items) || result.items.some(row =>
        typeof row.agent_id !== 'string' || !Number.isSafeInteger(row.revision) || row.revision < 0 ||
        row.color !== null && !palette.has(row.color)) || new Set(result.items.map(row => row.agent_id)).size !== result.items.length) throw new Error('Invalid appearance list');
      session.rows = result.items; session.phase = 'ready';
    } catch { if (sequence === session.sequence) session.phase = 'failed'; }
    notify(id);
  };
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (workId) { const session = sessionFor(workId); if (!session.pending) void refresh(workId); else notify(workId); } }, [client, workId, linkedSignature]);
  const submit = async (id: string, session: Session) => {
    if (!session.pending || session.phase === 'saving') return;
    const pending = session.pending;
    session.phase = 'saving'; notify(id);
    try {
      const result = await client.post<AgentAppearance & { work_id: string }>(`/api/v1/works/${id}/agents/${encodeURIComponent(pending.agentId)}/appearance`, pending.command);
      if (result.work_id !== id || result.agent_id !== pending.agentId || result.color !== pending.command.color || result.revision !== pending.command.expected_revision + 1) throw new Error('Unconfirmed appearance receipt');
      session.pending = null;
      // A replay receipt can predate another window's choice. Always re-read truth.
      await refresh(id);
    } catch (error) {
      if (error instanceof ApiActionError) {
        session.pending = null;
        session.phase = error.reasonCode === 'WORK_APPEARANCE_CONFLICT' ? 'conflict' : 'failed';
      } else session.phase = 'uncertain';
      notify(id);
    }
  };
  const session = workId ? sessions.current.get(workId) : undefined;
  return {
    hasPending: [...sessions.current.values()].some(value => value.pending !== null),
    rows: session?.rows ?? [], phase: session?.phase ?? 'loading',
    tints: Object.fromEntries((session?.rows ?? []).filter(row => row.color !== null).map(row => [row.agent_id, row.color!])),
    refresh: () => refresh(),
    choose: (agentId: string, color: string | null) => {
      if (!workId || !session || session.phase !== 'ready' || session.pending || color !== null && !palette.has(color)) return;
      const row = session.rows.find(item => item.agent_id === agentId); if (!row) return;
      session.pending = { agentId, command: { request_id: crypto.randomUUID(), expected_revision: row.revision, color } };
      void submit(workId, session);
    },
    retry: () => { if (workId && session?.phase === 'uncertain') void submit(workId, session); },
  };
}
