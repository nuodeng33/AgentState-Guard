import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiActionError, type ApiClient } from '../api/client';
import { useI18n } from '../i18n/I18nProvider';
import './SessionNicknameEditor.css';

type Command = { payload: { request_id: string; expected_revision: number; nickname: string }; confirmed: boolean };
const byClient = new WeakMap<ApiClient, Map<string, Command>>();
type Props = { client: ApiClient; workId: string; sessionId: string; disabled?: boolean; onChanged: () => Promise<void> | void };
export default function SessionNicknameEditor(props: Props) {
  return <NicknameBody key={`${props.workId}:${props.sessionId}`} {...props} />;
}
function NicknameBody({ client, workId, sessionId, disabled = false, onChanged }: Props) {
  const { locale } = useI18n(); const text = (en: string, cn: string) => locale === 'zh-CN' ? cn : en;
  let commands = byClient.get(client);
  if (!commands) { commands = new Map(); byClient.set(client, commands); }
  const memory = commands, key = `${workId}:${sessionId}`, initial = memory.get(key);
  const path = `/api/v1/works/${encodeURIComponent(workId)}/sessions/${encodeURIComponent(sessionId)}`;
  const [open, setOpen] = useState(Boolean(initial));
  const [nickname, setNickname] = useState(initial?.payload.nickname ?? '');
  const [saved, setSaved] = useState(''), [revision, setRevision] = useState(0);
  const [command, setCommand] = useState<Command | null>(initial ?? null);
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const live = useRef(true), flight = useRef(false), epoch = useRef(0);
  const trigger = useRef<HTMLButtonElement>(null), input = useRef<HTMLInputElement>(null);
  const callback = useRef(onChanged); callback.current = onChanged;
  const patch = (client as ApiClient & { patch?: <T>(path: string, body: unknown) => Promise<T> }).patch;
  useEffect(() => { live.current = true; return () => { live.current = false; epoch.current++; }; }, []);
  useEffect(() => { if (!memory.has(key)) void reload(); return () => { epoch.current++; }; }, [client, workId, sessionId]);
  useEffect(() => { if (open && !loading) input.current?.focus(); }, [open, loading]);
  function remember(next: Command | null) {
    if (next) memory.set(key, next); else memory.delete(key);
    if (live.current) setCommand(next);
  }
  async function reload() {
    if (flight.current || memory.has(key)) return;
    const sequence = ++epoch.current; setLoading(true); setError('');
    try {
      const value = await client.get<{ id?: string; session_id?: string; work_id: string; revision: number; nickname?: string }>(path);
      if ((value.id ?? value.session_id) !== sessionId || value.work_id !== workId ||
        !Number.isSafeInteger(value.revision) || value.revision < 1 ||
        value.nickname != null && typeof value.nickname !== 'string') throw new Error();
      if (!live.current || sequence !== epoch.current) return;
      setSaved(value.nickname ?? ''); setNickname(value.nickname ?? ''); setRevision(value.revision); setConflict(false);
    } catch { if (live.current && sequence === epoch.current) { setError('READ_FAILED'); setRevision(0); } }
    finally { if (live.current && sequence === epoch.current) setLoading(false); }
  }
  function close() { setOpen(false); requestAnimationFrame(() => trigger.current?.focus()); }
  async function send(next: Command) {
    if (flight.current || disabled) return;
    flight.current = true; setBusy(true); setError(''); remember(next);
    try {
      if (!next.confirmed) {
        if (!patch) { remember(null); setError('UNSUPPORTED'); return; }
        const receipt = await patch.call(client, path, next.payload) as { session_id?: string; revision?: number };
        if (receipt?.session_id !== sessionId || receipt.revision !== next.payload.expected_revision + 1) throw new Error();
        next = { ...next, confirmed: true }; remember(next);
      }
      try { await callback.current(); }
      catch { if (live.current) setError('REFRESH_FAILED'); return; }
      remember(null);
      if (live.current) { setSaved(next.payload.nickname); setRevision(next.payload.expected_revision + 1); close(); }
    } catch (failure) {
      if (failure instanceof ApiActionError && failure.status >= 400 && failure.status < 500) {
        remember(null);
        if (live.current) { setError(failure.reasonCode?.match(/^[A-Z0-9_]{1,64}$/)?.[0] ?? 'REJECTED'); setConflict(failure.status === 409); }
      } else if (live.current) setError('UNCONFIRMED');
    } finally { flight.current = false; if (live.current) setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled || busy || command || loading || conflict || revision < 1 || !patch) return;
    void send({ payload: { request_id: crypto.randomUUID(), expected_revision: revision, nickname }, confirmed: false });
  }
  const message = !patch ? text('Session rename is unavailable in this client.', '当前客户端不支持会话重命名。') :
    conflict ? text('The session changed. Reload before editing again.', '会话已改变，请重新载入后再编辑。') :
    error === 'READ_FAILED' ? text('The session name could not be loaded.', '未能读取会话名称。') :
    error === 'UNCONFIRMED' ? text('Rename result unconfirmed. Retry the same submission.', '重命名结果未确认，请重试原提交。') :
    error === 'REFRESH_FAILED' ? text('Renamed; refresh is still required.', '名称已保存，仍需刷新结果。') :
    error ? text('Rename rejected. Your draft is preserved.', '重命名被拒绝，草稿仍保留。') : '';
  return <div className="session-nickname-editor">
    {!open ? <><strong>{saved || text('Unnamed session', '未命名会话')}</strong><button ref={trigger} type="button" disabled={disabled || loading}
      onClick={() => { setOpen(true); if (!memory.has(key)) void reload(); }}>{text('Rename session', '重命名会话')}</button>
      {message && <span role="alert">{message}</span>}</> :
      <form onSubmit={submit} onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!flight.current) close(); } }}>
        <label>{text('Session nickname', '会话名称')}<input ref={input} maxLength={120} value={nickname} disabled={disabled || busy || loading || Boolean(command)}
          onChange={event => setNickname(event.target.value)} /></label>
        {message && <span role="alert">{message} {error.match(/^[A-Z][A-Z0-9_]{1,63}$/) && !['READ_FAILED', 'UNCONFIRMED', 'REFRESH_FAILED', 'UNSUPPORTED', 'REJECTED'].includes(error) ? error : ''}</span>}
        {!command && (conflict || error === 'READ_FAILED') && <button type="button" disabled={disabled || busy || loading} onClick={() => void reload()}>{text('Reload session name (replaces draft)', '重新载入会话名称（替换草稿）')}</button>}
        {command ? <button type="button" disabled={disabled || busy} onClick={() => void send(command)}>{command.confirmed ? text('Refresh saved name', '刷新保存名称') : text('Retry same rename', '重试原重命名')}</button> :
          <button type="submit" disabled={disabled || busy || loading || conflict || revision < 1 || !patch}>{text('Save session name', '保存会话名称')}</button>}
        <button type="button" disabled={busy} onClick={close}>{text('Cancel rename', '取消重命名')}</button>
      </form>}
  </div>;
}
