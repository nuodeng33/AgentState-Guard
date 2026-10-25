import { useEffect, useRef, useState } from 'react';
import { ApiActionError, ApiRequestError, type ApiClient } from '../api/client';
import type { DiscoverySource } from '../api/types';
import { useI18n } from '../i18n/I18nProvider';
import './EnvironmentSourceControls.css';

export type ConnectionSource = DiscoverySource & { running?: boolean; connected?: boolean;
  protection?: { running?: boolean; connected?: boolean } };
type Action = 'connect' | 'start-and-connect' | 'disconnect';
type Command = { action: Action; requestId: string; operationId?: string };
type Operation = { id: string; kind: string; request_id: string; state: string; reason_code?: string;
  result: { source_id?: string; action?: string } };
const safeCode = (value: unknown) => typeof value === 'string' && /^[A-Z0-9_]{1,64}$/.test(value) ? value : '';
const active = (state: string) => ['QUEUED', 'RUNNING'].includes(state);

export default function EnvironmentSourceControls({ client, source, onChanged, disabled = false }: {
  client: ApiClient; source: ConnectionSource; onChanged: () => void; disabled?: boolean;
}) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const text = (en: string, cn: string) => zh ? cn : en;
  const [confirm, setConfirm] = useState(false);
  const [command, setCommand] = useState<Command | null>(null);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(false);
  const generation = useRef(0); const busyRef = useRef(false);
  const changed = useRef(onChanged); changed.current = onChanged;
  const running = source.protection?.running ?? source.running;
  const connected = source.protection?.connected ?? source.connected;
  const known = typeof running === 'boolean' && typeof connected === 'boolean' && source.freshness === 'FRESH';
  const supported = source.kind === 'WSL' || source.kind === 'CONTAINER';
  const locked = disabled || busy || Boolean(command);
  useEffect(() => {
    generation.current++; busyRef.current = false; setBusy(false); setCommand(null);
    setOperation(null); setError(''); setNotice(false); setConfirm(false);
    return () => { generation.current++; };
  }, [source.source_id]);

  function accept(value: Operation, current: Command) {
    if (!value || typeof value.id !== 'string' || !value.id || value.id.length > 128 || value.kind !== 'SOURCE' ||
      value.request_id !== current.requestId || value.result?.source_id !== source.source_id ||
      value.result?.action !== current.action || (current.operationId && value.id !== current.operationId) ||
      !['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'REJECTED'].includes(value.state)) throw new Error('UNCONFIRMED');
    setOperation(value); setError('');
    if (value.state === 'SUCCEEDED') {
      setCommand(null); setNotice(true); changed.current();
    } else setCommand({ ...current, operationId: value.id });
  }
  async function submit(current: Command) {
    if (busyRef.current || disabled) return;
    busyRef.current = true; setBusy(true); setError(''); setNotice(false);
    const epoch = generation.current;
    try {
      const value = current.operationId
        ? await client.get<Operation>(`/api/v1/recovery/operations/${encodeURIComponent(current.operationId)}`)
        : await client.post<Operation>(`/api/v1/discovery/sources/${encodeURIComponent(source.source_id)}/${current.action}`, { request_id: current.requestId });
      if (generation.current === epoch) accept(value, current);
    } catch (failure) {
      if (generation.current !== epoch) return;
      const code = failure instanceof ApiActionError || failure instanceof ApiRequestError ? safeCode(failure.reasonCode) : '';
      setError(code || 'SOURCE_ACTION_UNCONFIRMED');
      if (failure instanceof ApiActionError && failure.status === 409) {
        setCommand(null); setOperation(null); changed.current();
      }
    } finally {
      if (generation.current === epoch) { busyRef.current = false; setBusy(false); }
    }
  }
  useEffect(() => {
    if (!command?.operationId || !operation || !active(operation.state) || busy || error) return;
    const timer = window.setTimeout(() => { if (!document.hidden) void submit(command); }, 1000);
    return () => window.clearTimeout(timer);
  }, [command, operation, busy, error]);
  function begin(action: Action) {
    if (busyRef.current || locked || !known || !supported || (action === 'start-and-connect' && (source.kind !== 'WSL' || running !== false || !confirm)) ||
      (action === 'connect' && (running !== true || connected !== false)) || (action === 'disconnect' && connected !== true)) return;
    const next = { action, requestId: crypto.randomUUID() };
    setConfirm(false); setCommand(next); setOperation(null); void submit(next);
  }
  const terminal = operation && !active(operation.state);
  return <div className="environment-source-controls" aria-label={text('Connection controls', '连接控制')}>
    {!supported ? <p className="muted">{source.kind === 'NATIVE' ? text('Local access needs no connection; disconnect is unavailable.', '本机访问无需连接，不提供断开操作。') : text('Connection control is unavailable for this source.', '此来源不提供连接控制。')}</p> : <>
      {!known && <p className="muted">{text('Refresh current source facts before connecting.', '请刷新来源事实后再连接。')}</p>}
      {known && running === false && source.kind === 'CONTAINER' && <p><code>DOMAIN_NOT_RUNNING</code> · {text('Start this container outside ASG, then refresh.', '请在 ASG 外启动此容器后刷新。')}</p>}
      <div className="environment-source-actions">
        <button type="button" disabled={locked || !known || running !== true || connected !== false} onClick={() => begin('connect')}>{text('Connect', '连接')}</button>
        <button type="button" disabled={locked || !known || connected !== true} onClick={() => begin('disconnect')}>{text('Disconnect', '断开连接')}</button>
        {source.kind === 'WSL' && known && running === false && <button type="button" disabled={locked} aria-expanded={confirm} onClick={() => setConfirm(true)}>{text('Start and connect…', '启动并连接…')}</button>}
      </div>
      {confirm && <div className="environment-source-confirm" role="group" aria-label={text('Confirm WSL startup', '确认启动 WSL')}>
        <p>{text('This starts only this WSL distribution and connects its directory channel:', '这将仅启动以下 WSL 发行版，并连接其目录通道：')} <strong>{source.label}</strong> <code>{source.source_id}</code></p>
        <button type="button" disabled={locked} onClick={() => begin('start-and-connect')}>{text('Confirm start and connect', '确认启动并连接')}</button>
        <button type="button" disabled={busy} onClick={() => setConfirm(false)}>{text('Cancel', '取消')}</button>
      </div>}
    </>}
    {notice && <p role="status">{text('Operation succeeded. Source facts are being refreshed.', '操作成功，正在刷新来源事实。')}</p>}
    {error && <p role="alert">{text('The result is not confirmed. Check records or retry the same submission.', '结果尚未确认，请核对记录或重试同一提交。')} <code>{error}</code></p>}
    {command && <div role="status">
      <p>{text('Requested action', '已请求操作')}: {command.action === 'start-and-connect' ? text('Start and connect', '启动并连接') : command.action === 'connect' ? text('Connect', '连接') : text('Disconnect', '断开连接')} · {operation?.state ?? text('Unconfirmed', '未确认')}{safeCode(operation?.reason_code) && <> · <code>{safeCode(operation?.reason_code)}</code></>}</p>
      <button type="button" disabled={busy || disabled} onClick={() => void submit(command)}>{command.operationId ? text('Check operation result', '核对操作结果') : text('Retry same submission', '重试同一提交')}</button>
      {terminal && <button type="button" disabled={busy || disabled} onClick={() => { setCommand(null); setOperation(null); setError(''); changed.current(); }}>{text('Close result and refresh', '关闭结果并刷新')}</button>}
    </div>}
  </div>;
}
