import { describe, expect, it, vi } from 'vitest';
import { ApiRequestError, createApiClient, SessionUnavailableError } from './client';
import { normalizeRun, normalizeSession, readRunEvents, runIsActive } from './execution';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status,
  headers: { 'Content-Type': 'application/json' } });
function sse(chunks: string[]) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream<Uint8Array>({ start(controller) {
    chunks.forEach(chunk => controller.enqueue(encoder.encode(chunk))); controller.close();
  } }), { headers: { 'Content-Type': 'text/event-stream' } });
}

describe('authenticated execution events', () => {
  it('attaches the session token to SSE and uses one 401 re-bootstrap', async () => {
    const replies = [json({ token: 'old' }), json({}, 401), json({ token: 'new' }), sse([])];
    const fetcher = vi.fn(async () => replies.shift()!);
    const client = createApiClient(fetcher as unknown as typeof fetch);
    const signal = new AbortController().signal;
    await client.stream!('/api/v1/works/w/runs/r/events?after=7&limit=100', signal);
    expect(fetcher).toHaveBeenCalledTimes(4);
    const init = (fetcher.mock.calls as unknown as [unknown, RequestInit][])[3][1];
    expect(init.headers).toEqual({ Accept: 'text/event-stream', 'X-Session-Token': 'new' });
    expect(init.signal).toBe(signal);
    expect(window.sessionStorage.length).toBe(0);
  });

  it('surfaces a repeated 401 without an auth retry loop', async () => {
    const replies = [json({ token: 'old' }), json({}, 401), json({ token: 'new' }), json({}, 401)];
    const fetcher = vi.fn(async () => replies.shift()!);
    const client = createApiClient(fetcher as unknown as typeof fetch);
    await expect(client.stream!('/events')).rejects.toBeInstanceOf(SessionUnavailableError);
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it('parses fragmented replay pages and deduplicates sequence numbers', async () => {
    const client = { stream: vi.fn(async () => sse([
      ': heartbeat\r\n\r\nid: 7\r\ndata: {"seq":7,"kind":"MESSAGE","payload":{"text":"old"},"created_at":"now"}\r\n\r\n',
      'id: 8\r\ndata: {"seq":8,"type":"MESSAGE","payload":',
      '{"text":"new"},"observed_at":"now"}\r\n\r\n',
      'id: 8\ndata: {"seq":8,"kind":"MESSAGE","payload":{"text":"duplicate"},"created_at":"now"}\n\n',
      'id: 9\ndata: {"seq":9,"kind":"STATUS",\ndata: "payload":{"status":"COMPLETED"},"created_at":"now"}\n\n',
    ])) } as unknown as ReturnType<typeof createApiClient>;
    const received = vi.fn();
    const after = await readRunEvents(client, 'work / one', 'run / one', 7, received, new AbortController().signal);
    expect(after).toBe(9);
    expect(received.mock.calls.map(([event]) => event.seq)).toEqual([8, 9]);
    expect(received.mock.calls[0][0].kind).toBe('MESSAGE');
    expect(client.stream).toHaveBeenCalledWith('/api/v1/works/work%20%2F%20one/runs/run%20%2F%20one/events?after=7&limit=100', expect.any(AbortSignal));
  });

  it('rejects a mismatched SSE cursor and never passes the malformed record', async () => {
    const client = { stream: vi.fn(async () => sse(['id: 2\ndata: {"seq":1,"kind":"MESSAGE","payload":{},"created_at":"now"}\n\n'])) } as unknown as ReturnType<typeof createApiClient>;
    const received = vi.fn();
    await expect(readRunEvents(client, 'w', 'r', 0, received, new AbortController().signal)).rejects.toBeInstanceOf(ApiRequestError);
    expect(received).not.toHaveBeenCalled();
  });

  it('ends on backend done metadata without requiring an event sequence', async () => {
    const client = { stream: vi.fn(async () => sse([
      'id: 1\nevent: TEXT\ndata: {"seq":1,"type":"TEXT","payload":{"text":"Result"},"observed_at":"now"}\n\n',
      'event: done\ndata: {"last_seq":1,"history_status":"AVAILABLE"}\n\n',
    ])) } as unknown as ReturnType<typeof createApiClient>;
    const received = vi.fn();
    expect(await readRunEvents(client, 'w', 'r', 0, received, new AbortController().signal)).toBe(1);
    expect(received).toHaveBeenCalledTimes(1);
    expect(received.mock.calls[0][0]).toMatchObject({ kind: 'TEXT', seq: 1 });
  });

  it('surfaces only the safe reason from backend error metadata', async () => {
    const client = { stream: vi.fn(async () => sse(['event: error\ndata: {"reason_code":"RUN_HISTORY_UNAVAILABLE","message":"private detail"}\n\n'])) } as unknown as ReturnType<typeof createApiClient>;
    await expect(readRunEvents(client, 'w', 'r', 0, vi.fn(), new AbortController().signal))
      .rejects.toMatchObject({ message: 'Execution event stream failed', reasonCode: 'RUN_HISTORY_UNAVAILABLE' });
  });

  it('closes the subscription on abort without issuing an execution mutation', async () => {
    const cancelled = vi.fn();
    const controller = new AbortController();
    const client = { stream: vi.fn(async () => new Response(new ReadableStream({ cancel: cancelled }), {
      headers: { 'Content-Type': 'text/event-stream' },
    })), post: vi.fn() } as unknown as ReturnType<typeof createApiClient>;
    const reading = readRunEvents(client, 'w', 'r', 0, vi.fn(), controller.signal);
    await vi.waitFor(() => expect(client.stream).toHaveBeenCalled());
    controller.abort(); await reading;
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(client.post).not.toHaveBeenCalled();
  });
});

describe('execution DTO boundaries', () => {
  it('normalizes backend aliases without treating missing capabilities as permission', () => {
    const session = normalizeSession({ session_id: 's', state: 'READY', capabilities: { send: true }, reason: 'SESSION_READY' });
    expect(session).toMatchObject({ id: 's', status: 'READY', read_only: true, capabilities: { send: true, write: false, cancel: false } });
    const run = normalizeRun({ run_id: 'r', session_id: 's', state: 'WAITING_APPROVAL', seq: 4,
      pending_actions: [{ seq: 4, action_ref: 'tool-1', type: 'APPROVAL', payload: { text: 'Write file' } }],
      file_result: { coverage: 'PARTIAL', change_refs: ['change-1'] } });
    expect(run).toMatchObject({ id: 'r', status: 'WAITING_APPROVAL', last_seq: 4,
      pending_responses: [{ event_seq: 4, action_ref: 'tool-1', kind: 'APPROVAL' }], change_refs: ['change-1'] });
    expect(normalizeRun({ id: 'r', state: 'INVENTED' }).status).toBe('UNCERTAIN');
  });

  it('retains connection and dispatch states and keeps file finalization active', () => {
    for (const state of ['CONNECTING', 'DISCONNECTED', 'UNCERTAIN'] as const)
      expect(normalizeSession({ session_id: 's', state }).status).toBe(state);
    for (const state of ['QUEUED', 'DISPATCHING', 'FINALIZING', 'INTERRUPTED'] as const)
      expect(normalizeRun({ run_id: 'r', state }).status).toBe(state);
    expect(runIsActive('FINALIZING')).toBe(true);
    expect(runIsActive('QUEUED')).toBe(true);
    expect(runIsActive('DISPATCHING')).toBe(true);
    expect(runIsActive('INTERRUPTED')).toBe(false);
  });
});
