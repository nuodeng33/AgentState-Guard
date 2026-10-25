import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { ApiActionError, type ApiClient } from '../api/client';
import { useI18n } from '../i18n/I18nProvider';
import './WorkEditor.css';

export type EditableWork = { id: string; title: string; owner: string; revision: number;
  requirement: string; requirement_version: number; tasks: { id: string; title: string }[] };
type Mode = 'work' | 'requirement' | 'task';
type Fields = { title: string; owner: string; body: string; assignee: string; dependsOn: string[] };
type Mutation = <T>(path: string, body: unknown) => Promise<T>;
type Command = { method: 'patch' | 'post' | 'put'; path: string; payload: Record<string, unknown>;
  fields: Fields; work: EditableWork; taskId: string; confirmed: boolean };
// Only unresolved commands live here. Keeping their immutable request identity
// across editor closure avoids treating a lost reply as a new user command.
const unresolvedByClient = new WeakMap<ApiClient, Map<string, Command>>();
function commandsFor(client: ApiClient) {
  let commands = unresolvedByClient.get(client);
  if (!commands) { commands = new Map(); unresolvedByClient.set(client, commands); }
  return commands;
}
type Props = { client: ApiClient; work: EditableWork; mode: Mode; taskId?: string;
  onClose: () => void; onChanged: () => Promise<void> | void };
const fieldsFor = (work: EditableWork): Fields => ({ title: work.title, owner: work.owner,
  body: work.requirement, assignee: '', dependsOn: [] });
function validWork(value: unknown, id: string): EditableWork {
  const row = value as EditableWork;
  if (!row || row.id !== id || typeof row.title !== 'string' || typeof row.owner !== 'string' ||
    typeof row.requirement !== 'string' || !Number.isSafeInteger(row.revision) || row.revision < 1 ||
    !Number.isSafeInteger(row.requirement_version) || !Array.isArray(row.tasks) ||
    row.tasks.some(item => !item || typeof item.id !== 'string' || typeof item.title !== 'string')) throw new Error();
  return row;
}
export default function WorkEditor(props: Props) {
  return <WorkEditorBody key={`${props.work.id}:${props.mode}:${props.taskId ?? ''}`} {...props} />;
}
function WorkEditorBody({ client, work, mode, taskId, onClose, onChanged }: Props) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const text = (en: string, cn: string) => zh ? cn : en;
  const key = `${work.id}:${mode}:${taskId ?? ''}`;
  const unresolved = commandsFor(client);
  const initial = unresolved.get(key);
  const [base, setBase] = useState(initial?.work ?? work);
  const [fields, setFields] = useState<Fields>(initial?.fields ?? fieldsFor(work));
  const [selectedTask, setSelectedTask] = useState(initial?.taskId ?? taskId ?? work.tasks[0]?.id ?? '');
  const [command, setCommand] = useState<Command | null>(initial ?? null);
  const [loading, setLoading] = useState(!initial);
  const [loaded, setLoaded] = useState(Boolean(initial));
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const root = useRef<HTMLDivElement>(null), inFlight = useRef(false), live = useRef(true), epoch = useRef(0);
  const callbacks = useRef({ onClose, onChanged }); callbacks.current = { onClose, onChanged };
  const titleId = useId();
  const api = client as ApiClient & { patch?: Mutation; put?: Mutation };
  const supported = mode === 'requirement' || typeof api[mode === 'work' ? 'patch' : 'put'] === 'function';
  const locked = busy || Boolean(command);
  const heading = mode === 'work' ? text('Edit work information', '编辑工作信息') :
    mode === 'requirement' ? text('Revise requirement', '修订要求') : text('Edit task', '编辑任务');

  useEffect(() => {
    live.current = true;
    const previous = document.activeElement as HTMLElement | null;
    root.current?.querySelector<HTMLElement>('input:not(:disabled),textarea:not(:disabled),select:not(:disabled),button:not(:disabled)')?.focus();
    return () => { live.current = false; epoch.current++; if (previous?.isConnected) previous.focus(); };
  }, []);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (!inFlight.current) callbacks.current.onClose();
      }
      if (event.key !== 'Tab') return;
      const nodes = Array.from(root.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled)') ?? []);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keyboard, true);
    return () => document.removeEventListener('keydown', keyboard, true);
  }, []);

  async function reload() {
    if (inFlight.current || unresolved.has(key)) return;
    const seq = ++epoch.current;
    setLoading(true); setLoaded(false); setError('');
    try {
      const [freshValue, taskValue] = await Promise.all([
        client.get<unknown>(`/api/v1/works/${encodeURIComponent(work.id)}`),
        mode === 'task' && selectedTask ? client.get<unknown>(`/api/v1/works/${encodeURIComponent(work.id)}/tasks/${encodeURIComponent(selectedTask)}`) : Promise.resolve(null),
      ]);
      const fresh = validWork(freshValue, work.id);
      let next = fieldsFor(fresh);
      if (mode === 'task') {
        const task = taskValue as { id: string; work_id: string; title: string; body: string; assignee: string | null; depends_on: string[] };
        if (!selectedTask || !task || task.id !== selectedTask || task.work_id !== work.id ||
          typeof task.title !== 'string' || typeof task.body !== 'string' ||
          !(task.assignee === null || typeof task.assignee === 'string') ||
          !Array.isArray(task.depends_on) || task.depends_on.some(id => typeof id !== 'string')) throw new Error();
        next = { ...next, title: task.title, body: task.body, assignee: task.assignee ?? '', dependsOn: [...task.depends_on] };
      }
      if (!live.current || seq !== epoch.current) return;
      setBase(fresh); setFields(next); setConflict(false); setLoaded(true);
    } catch { if (live.current && seq === epoch.current) setError('READ_FAILED'); }
    finally { if (live.current && seq === epoch.current) setLoading(false); }
  }
  useEffect(() => {
    if (!unresolved.has(key) && (mode !== 'task' || selectedTask)) void reload();
    else if (mode === 'task' && !selectedTask) { setLoading(false); setLoaded(false); }
    return () => { epoch.current++; };
  }, [client, work.id, mode, selectedTask]);

  function remember(next: Command | null) {
    if (next) unresolved.set(key, next); else unresolved.delete(key);
    if (live.current) setCommand(next);
  }
  async function send(next: Command) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(''); remember(next);
    try {
      if (!next.confirmed) {
        const action = next.method === 'post' ? client.post.bind(client) : api[next.method]?.bind(client);
        if (!action) { remember(null); setError('UNSUPPORTED'); return; }
        const receipt = await action<Record<string, unknown>>(next.path, next.payload);
        if (!receipt || receipt.work_id !== work.id || receipt.revision !== next.work.revision + 1 ||
          mode === 'requirement' && receipt.requirement_version !== next.work.requirement_version + 1 ||
          mode === 'task' && receipt.task_id !== next.taskId) throw new Error();
        next = { ...next, confirmed: true }; remember(next);
      }
      try {
        await callbacks.current.onChanged();
      } catch { if (live.current) setError('REFRESH_FAILED'); return; }
      remember(null);
      if (live.current) callbacks.current.onClose();
    } catch (failure) {
      if (failure instanceof ApiActionError && failure.status >= 400 && failure.status < 500) {
        remember(null);
        const code = failure.reasonCode?.match(/^[A-Z0-9_]{1,64}$/)?.[0];
        if (live.current) { setError(code ?? 'REJECTED'); setConflict(failure.status === 409); }
      } else if (live.current) setError('UNCONFIRMED');
    } finally { inFlight.current = false; if (live.current) setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (locked || !loaded || loading || conflict || !supported) return;
    const payload: Record<string, unknown> = { request_id: crypto.randomUUID(), expected_revision: base.revision,
      ...(mode === 'work' ? { title: fields.title, owner: fields.owner } : mode === 'requirement' ? { body: fields.body } :
        { title: fields.title, body: fields.body, assignee: fields.assignee.trim() || null, depends_on: [...fields.dependsOn] }) };
    const path = `/api/v1/works/${encodeURIComponent(work.id)}` +
      (mode === 'requirement' ? '/requirements' : mode === 'task' ? `/tasks/${encodeURIComponent(selectedTask)}` : '');
    void send({ method: mode === 'work' ? 'patch' : mode === 'task' ? 'put' : 'post', path, payload,
      fields: { ...fields, dependsOn: [...fields.dependsOn] }, work: base, taskId: selectedTask, confirmed: false });
  }
  const message = !supported ? text('This client cannot perform this edit.', '当前客户端不支持此编辑。') :
    error === 'READ_FAILED' ? text('Content could not be loaded. No edit has been sent.', '内容读取失败，尚未提交编辑。') :
    error === 'UNCONFIRMED' ? text('The result is unconfirmed. Retry the same submission; its content is locked.', '结果尚未确认。请重试原提交，内容已锁定。') :
    error === 'REFRESH_FAILED' ? text('Saved, but the current result could not be refreshed.', '已保存，但当前结果未能刷新。') :
    conflict ? text('The work changed. Review current content before submitting again.', '工作已改变，请重新载入并核对后再提交。') :
    error ? text('The edit was rejected. Your draft is preserved.', '编辑被拒绝，草稿仍保留。') : '';
  const dependencies = [...base.tasks.filter(task => task.id !== selectedTask),
    ...fields.dependsOn.filter(id => !base.tasks.some(task => task.id === id)).map(id => ({ id, title: id }))];
  return <div className="work-editor-overlay" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={root}>
    <form className="work-editor" onSubmit={submit}>
      <header><h2 id={titleId}>{heading}</h2><button type="button" aria-label={text('Close editor', '关闭编辑')} disabled={busy}
        onClick={() => callbacks.current.onClose()}>×</button></header>
      {mode === 'requirement' && <p>{text(`Saving creates a new version; current v${base.requirement_version} remains unchanged.`, `保存将新增版本；当前 v${base.requirement_version} 保持不变。`)}</p>}
      {message && <p role="alert">{message} {error.match(/^[A-Z][A-Z0-9_]{1,63}$/) && !['READ_FAILED', 'UNCONFIRMED', 'REFRESH_FAILED', 'UNSUPPORTED', 'REJECTED'].includes(error) ? error : ''}</p>}
      {loading && <p role="status">{text('Loading current content…', '正在读取当前内容…')}</p>}
      <fieldset disabled={locked || loading || !loaded}>
        {mode === 'task' && !taskId && <label>{text('Task to edit', '编辑任务')}<select value={selectedTask} onChange={event => setSelectedTask(event.target.value)}>
          {base.tasks.map(task => <option key={task.id} value={task.id}>{task.title}</option>)}</select></label>}
        {mode !== 'requirement' && <label>{mode === 'work' ? text('Work title', '工作名称') : text('Title', '标题')}<input required maxLength={200} value={fields.title} onChange={event => setFields(old => ({ ...old, title: event.target.value }))} /></label>}
        {mode === 'work' ? <label>{text('Owner', '负责人')}<input required maxLength={120} value={fields.owner} onChange={event => setFields(old => ({ ...old, owner: event.target.value }))} /></label> :
          <label>{mode === 'requirement' ? text('Requirement', '要求') : text('Content', '正文')}<textarea required={mode === 'requirement'} maxLength={100000} rows={10} value={fields.body} onChange={event => setFields(old => ({ ...old, body: event.target.value }))} /></label>}
        {mode === 'task' && <><label>{text('Assignee (optional)', '执行者（可选）')}<input maxLength={120} value={fields.assignee} onChange={event => setFields(old => ({ ...old, assignee: event.target.value }))} /></label>
          <fieldset className="work-editor-dependencies"><legend>{text('Dependencies', '前置任务')}</legend>
            {dependencies.map(task => <label key={task.id}><input type="checkbox" checked={fields.dependsOn.includes(task.id)} onChange={event => setFields(old => ({ ...old,
              dependsOn: event.target.checked ? [...old.dependsOn, task.id] : old.dependsOn.filter(id => id !== task.id) }))} />{task.title}</label>)}
            {!dependencies.length && <p>{text('No other tasks', '没有其他任务')}</p>}</fieldset></>}
      </fieldset>
      {!command && (conflict || error === 'READ_FAILED') && <button type="button" disabled={busy || loading} onClick={() => void reload()}>{text('Reload current content (replaces draft)', '重新载入当前内容（替换草稿）')}</button>}
      <footer><button type="button" disabled={busy} onClick={() => callbacks.current.onClose()}>{text('Cancel', '取消')}</button>
        {command ? <button type="button" disabled={busy} onClick={() => void send(command)}>{command.confirmed ? text('Refresh saved result', '刷新保存结果') : text('Retry same submission', '重试原提交')}</button> :
          <button type="submit" disabled={busy || loading || !loaded || conflict || !supported}>{mode === 'requirement' ? text('Save new version', '保存新版本') : text('Save', '保存')}</button>}
      </footer>
    </form>
  </div>;
}
