import { useEffect, useRef, useState } from 'react';
import { ApiActionError, type ApiClient } from '../api/client';
import { useI18n } from '../i18n/I18nProvider';
import './WorkProtectionLinks.css';

type Scope = { id: string; source_id: string; root: string; status: string };
type Link = { kind: 'scope' | 'batch' | 'session'; object_id: string; task_id: string;
  basis?: string; object?: { root?: string; source_id?: string; status?: string } };
type Receipt = { work_id: string; revision: number; scope_id: string | null };
type Command = { action: 'link' | 'unlink'; label: string; body: { request_id: string; expected_revision: number;
  kind: Link['kind']; object_id: string; task_id: string | null }; receipt?: Receipt };
// Transient intent survives a panel remount; no token or intent is written to disk.
const intentions = new WeakMap<ApiClient, Map<string, Command>>();
const submissions = new WeakMap<ApiClient, Set<string>>();
function running(client: ApiClient) {
  let ids = submissions.get(client);
  if (!ids) { ids = new Set(); submissions.set(client, ids); }
  return ids;
}
function memory(client: ApiClient) {
  let values = intentions.get(client);
  if (!values) { values = new Map(); intentions.set(client, values); }
  return values;
}
const safeCode = (value: unknown) => typeof value === 'string' && /^[A-Z0-9_]{1,64}$/.test(value) ? value : '';
const linkKey = (item: Link) => `${item.kind}:${item.object_id}:${item.task_id ?? ''}`;
type Props = { client: ApiClient; workId: string; onClose: () => void; onChanged?: () => void;
  visible?: boolean };
export default function WorkProtectionLinks({ client, workId, onClose, onChanged, visible = true }: Props) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const text = (en: string, cn: string) => zh ? cn : en;
  const [links, setLinks] = useState<Link[]>([]), [scopes, setScopes] = useState<Scope[]>([]);
  const [revision, setRevision] = useState<number | null>(null), [selected, setSelected] = useState('');
  const [draft, setDraft] = useState<Command | null>(null);
  const [pending, setPending] = useState<Command | null>(() => memory(client).get(workId) ?? null);
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(false);
  const epoch = useRef(0), generation = useRef(0), inFlight = useRef(false), mounted = useRef(true);
  const context = useRef({ client, workId, visible }); context.current = { client, workId, visible };
  const callbacks = useRef({ onChanged, onClose }); callbacks.current = { onChanged, onClose };
  const panel = useRef<HTMLDivElement>(null), closeButton = useRef<HTMLButtonElement>(null);
  const base = `/api/v1/works/${encodeURIComponent(workId)}/protection-links`;
  const same = (id: string, api: ApiClient) => mounted.current && context.current.workId === id && context.current.client === api && context.current.visible;
  function keep(command: Command | null, id = workId, api = client) {
    if (command) memory(api).set(id, command); else memory(api).delete(id);
    if (same(id, api)) setPending(command);
  }
  async function read(id = workId, api = client, view = generation.current) {
    const current = same(id, api) && view === generation.current ? ++epoch.current : -1;
    const [work, records, directories] = await Promise.all([
      api.get<{ revision: number }>(`/api/v1/works/${encodeURIComponent(id)}`),
      api.get<{ items: Link[] }>(`/api/v1/works/${encodeURIComponent(id)}/protection-links`),
      api.get<{ items: Scope[] }>('/api/v1/protection/scopes'),
    ]);
    if (!Number.isInteger(work.revision) || work.revision < 1 || !Array.isArray(records.items) ||
      records.items.some(item => !['scope', 'batch', 'session'].includes(item.kind) || typeof item.object_id !== 'string' || !item.object_id ||
        item.task_id != null && typeof item.task_id !== 'string') ||
      !Array.isArray(directories.items) || directories.items.some(item => !item || typeof item.id !== 'string' || !item.id ||
        typeof item.source_id !== 'string' || typeof item.root !== 'string' || typeof item.status !== 'string')) throw new Error();
    if (current === epoch.current && same(id, api) && view === generation.current) {
      setRevision(work.revision); setLinks(records.items); setScopes(directories.items);
    }
    return { revision: work.revision, links: records.items };
  }
  useEffect(() => {
    mounted.current = true;
    generation.current++; epoch.current++; setLinks([]); setScopes([]); setRevision(null); setDraft(null); setSelected('');
    setPending(memory(client).get(workId) ?? null); setError(''); setNotice(false); setBusy(inFlight.current);
    if (!visible) return;
    let active = true; setLoading(true);
    void read().catch(() => { if (active) setError('READ_FAILED'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; epoch.current++; };
  }, [client, workId, visible]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; epoch.current++; generation.current++; }; }, []);
  useEffect(() => {
    if (!visible) return;
    const previous = document.activeElement as HTMLElement | null;
    const currentPanel = panel.current;
    closeButton.current?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation(); callbacks.current.onClose(); return;
      }
      if (event.key !== 'Tab') return;
      const nodes = Array.from(currentPanel?.querySelectorAll<HTMLElement>('button:not(:disabled),select:not(:disabled),input:not(:disabled),textarea:not(:disabled)') ?? []);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (!currentPanel?.contains(document.activeElement)) { event.preventDefault(); first?.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keyboard, true);
    return () => {
      document.removeEventListener('keydown', keyboard, true);
      if (previous?.isConnected && (document.activeElement === document.body || currentPanel?.contains(document.activeElement))) previous.focus();
    };
  }, [visible, workId, client]);
  async function refresh() {
    if (loading || inFlight.current) return;
    const view = generation.current;
    setLoading(true); setError(''); setDraft(null);
    try { await read(workId, client, view); } catch { if (same(workId, client) && view === generation.current) setError('READ_FAILED'); }
    finally { if (same(workId, client) && view === generation.current) setLoading(false); }
  }
  async function submit(command: Command) {
    if (inFlight.current || running(client).has(workId) || !visible) return;
    const id = workId, api = client, targetBase = base, view = generation.current;
    inFlight.current = true; running(api).add(id); setBusy(true); setDraft(null); setError(''); setNotice(false); keep(command);
    let confirmed = Boolean(command.receipt);
    try {
      if (!command.receipt) {
        const receipt = await api.post<Receipt>(targetBase + (command.action === 'unlink' ? '/unlink' : ''), command.body);
        if (!receipt || receipt.work_id !== id || receipt.revision !== command.body.expected_revision + 1 ||
          (command.body.kind === 'scope' && receipt.scope_id !== command.body.object_id)) throw new Error();
        command = { ...command, receipt }; confirmed = true; keep(command, id, api);
      }
      const current = await read(id, api, view);
      const exists = current.links.some(item => linkKey(item) === linkKey(command.body as Link));
      if (current.revision < command.receipt!.revision || exists !== (command.action === 'link')) throw new Error();
      keep(null, id, api);
      if (same(id, api) && view === generation.current) { setNotice(true); callbacks.current.onChanged?.(); }
    } catch (err) {
      if (!confirmed && err instanceof ApiActionError && err.status >= 400 && err.status < 500) {
        keep(null, id, api);
        if (same(id, api) && view === generation.current) setError(safeCode(err.reasonCode) || 'REJECTED');
        try { await read(id, api, view); } catch { if (same(id, api) && view === generation.current) setError('READ_FAILED'); }
      } else if (same(id, api) && view === generation.current) setError('UNCONFIRMED');
    } finally {
      inFlight.current = false; running(api).delete(id);
      if (mounted.current) setBusy(false);
    }
  }
  function prepare(action: Command['action'], kind: Link['kind'], objectId: string, taskId: string | null, label: string) {
    if (revision === null || loading || busy || pending) return;
    setDraft({ action, label, body: { request_id: crypto.randomUUID(), expected_revision: revision,
      kind, object_id: objectId, task_id: taskId || null } });
    setError(''); setNotice(false);
  }
  if (!visible) return null;
  const locked = loading || busy || Boolean(pending) || revision === null || error === 'READ_FAILED';
  const available = scopes.filter(scope => scope.status !== 'RETIRED' && !links.some(item => item.kind === 'scope' && item.object_id === scope.id));
  const selectedScope = available.find(item => item.id === selected);
  const errorText = error === 'UNCONFIRMED' ? text('Result unconfirmed. Retry the same request below.', '结果尚未确认，请在下方重试同一请求。') :
    error === 'READ_FAILED' ? text('Directory links could not be refreshed.', '未能刷新目录关联。') :
    error === 'WORK_REVISION_CONFLICT' ? text('Work changed. Review refreshed records and confirm again.', '工作已改变，请核对刷新记录并重新确认。') : text('The action was rejected.', '操作被拒绝。');
  return <div ref={panel} role="dialog" aria-modal="true" aria-label={text('Work directory links', '工作目录关联')}><section className="work-protection-links" aria-label={text('Work directory links', '工作目录关联')}>
    <header><h2>{text('Work directory links', '工作目录关联')}</h2><button ref={closeButton} type="button" onClick={onClose}>{text('Close directory links', '关闭目录关联')}</button></header>
    <p>{text('Link a protected directory to this work. Linking does not copy files, start protection or prove who changed them.', '将保护目录关联到此工作。关联不会复制文件、启动保护，也不证明修改作者。')}</p>
    {error && <p role="alert">{errorText}{safeCode(error) && !['READ_FAILED', 'UNCONFIRMED', 'REJECTED'].includes(error) ? ` ${error}` : ''}</p>}
    {notice && <p role="status">{text('Directory links refreshed after saving.', '已保存并刷新目录关联。')}</p>}
    {loading && <p role="status">{text('Loading directory links', '正在读取目录关联')}</p>}
    {pending && <div className="work-protection-links-confirm" role="status"><p>{text('Pending request', '待确认请求')}: {pending.action} · {pending.label}</p>
      <button type="button" disabled={busy || loading} onClick={() => void submit(pending)}>{pending.receipt ? text('Refresh confirmed request', '刷新已确认请求') : text('Retry same request', '重试同一请求')}</button></div>}
    <button type="button" disabled={loading || busy} onClick={() => void refresh()}>{text('Refresh directory links', '刷新目录关联')}</button>
    <label>{text('Protected directory to link', '待关联保护目录')}<select value={selected} disabled={locked || Boolean(draft)} onChange={event => setSelected(event.target.value)}>
      <option value="">{text('Select protected directory', '选择保护目录')}</option>
      {available.map(scope => <option key={scope.id} value={scope.id}>{scope.source_id} · {scope.root} · {scope.status}</option>)}
    </select></label>
    <button type="button" disabled={locked || !selectedScope || Boolean(draft)} onClick={() => selectedScope && prepare('link', 'scope', selectedScope.id, null, `${selectedScope.source_id} · ${selectedScope.root}`)}>{text('Link directory', '关联目录')}</button>
    {draft && <div className="work-protection-links-confirm"><p>{draft.action === 'link' ? text('Confirm linking', '确认关联') : text('Confirm removing the link', '确认解除关联')}: {draft.label}</p>
      <p>{text('Removing a link keeps the directory, recovery history and original records.', '解除关联将保留目录、恢复历史和原始记录。')}</p>
      <button type="button" disabled={locked} onClick={() => void submit(draft)}>{text('Confirm directory link change', '确认目录关联变更')}</button>
      <button type="button" disabled={busy} onClick={() => setDraft(null)}>{text('Cancel link change', '取消关联变更')}</button></div>}
    <ul>{links.map(item => <li key={linkKey(item)}><span>{item.kind} · {item.object?.source_id ? `${item.object.source_id} · ` : ''}{item.object?.root ?? item.object_id}{item.task_id ? ` · ${item.task_id}` : ''} · {item.basis ?? 'UNKNOWN'}</span>
      <button type="button" disabled={locked || Boolean(draft)} onClick={() => prepare('unlink', item.kind, item.object_id, item.task_id, item.object?.root ?? item.object_id)}>{text('Remove link', '解除关联')}</button></li>)}</ul>
    {!loading && !links.length && <p>{text('No linked directories or observations.', '暂无关联目录或观测记录。')}</p>}
  </section></div>;
}
