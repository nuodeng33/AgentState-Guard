import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiActionError, ApiRequestError, type ApiClient } from '../api/client';
import type { ProtectionScope } from '../api/execution';
import { RECOVERY_PAGE_SIZE, recoveryActive, recoveryApi, type DirectoryVersion, type RecoveryCommand,
  type RecoveryDiff, type RecoveryFile, type RecoveryOperation, type RecoveryPreview } from '../api/recovery';
import { useI18n } from '../i18n/I18nProvider';
import './WorkRecovery.css';

export default function WorkRecovery({ client, visible, onClose }: { client: ApiClient; visible: boolean; onClose: () => void }) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const text = (en: string, cn: string) => zh ? cn : en;
  const api = useMemo(() => recoveryApi(client), [client]);
  const [scopes, setScopes] = useState<ProtectionScope[]>([]);
  const [scopeId, setScopeId] = useState('');
  const [versions, setVersions] = useState<DirectoryVersion[]>([]);
  const [versionId, setVersionId] = useState('');
  const [versionsMore, setVersionsMore] = useState(false);
  const [operations, setOperations] = useState<RecoveryOperation[]>([]);
  const [operationsMore, setOperationsMore] = useState(false);
  const [undo, setUndo] = useState<RecoveryOperation | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [filePage, setFilePage] = useState(0);
  const [preview, setPreview] = useState<RecoveryPreview | null>(null);
  const [diff, setDiff] = useState<{ file: string; result?: RecoveryDiff; failed?: boolean } | null>(null);
  const [operation, setOperation] = useState<RecoveryOperation | null>(null);
  const [submission, setSubmission] = useState<RecoveryCommand | null>(null);
  const [busy, setBusy] = useState(false); const busyRef = useRef(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(''); // Stable code only; translated at render time.
  const [refresh, setRefresh] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [tabVisible, setTabVisible] = useState(() => !document.hidden);
  const submissionRef = useRef(submission); submissionRef.current = submission;
  const operationRef = useRef(operation); operationRef.current = operation;
  const reconcileOnRefresh = useRef(false);
  const epoch = useRef(0); const diffEpoch = useRef(0);
  const versionOffset = useRef(0); const operationOffset = useRef(0);
  const panel = useRef<HTMLElement>(null); const close = useRef<HTMLButtonElement>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; epoch.current++; diffEpoch.current++; };
  }, []);
  const version = versions.find(item => item.id === versionId);
  const scope = scopes.find(item => item.id === scopeId);
  const locked = busy || Boolean(submission) || Boolean(operation && (recoveryActive(operation.state) || operation.state === 'NEEDS_ATTENTION'));
  const files: RecoveryFile[] = undo ? (undo.steps ?? []).map(step => ({
    file_id: (step.before ?? step.after)?.file_id ?? '', relative_path: step.relative_path,
    category: [step.before, step.after].every(entry => entry === null || entry?.category === 'restorable') ? 'restorable' : 'unavailable',
  })) : version?.files ?? [];
  const contentReady = Boolean(undo || (version?.available && version.metadata_available && version.details_included));
  const previewReady = Boolean(preview?.status === 'READY' && preview.preview_id && preview.digest &&
    preview.expires_at && new Date(preview.expires_at).getTime() > now);
  const date = (value: string) => Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString(locale) : value;
  function invalidate() { epoch.current++; diffEpoch.current++; setPreview(null); setDiff(null); setError(''); setOperation(null); }
  const reason = (err: unknown) => err instanceof ApiActionError || err instanceof ApiRequestError ? err.reasonCode ?? 'READ_FAILED' : 'READ_FAILED';
  function message(code: string) {
    if (code === 'UNCONFIRMED') return text('Result not confirmed. Retry the same submission or refresh records.', '结果尚未确认。请重试同一提交或刷新记录。');
    if (code.includes('EXPIRED')) return text('Preview expired or file content unavailable. Select again and create a new preview.', '预览已过期或文件内容已失效，请重新选择并生成预览。');
    if (code.includes('CONFLICT') || code.includes('MISMATCH')) return text('Files changed or the preview conflicts. Create a new preview.', '文件已变化或预览存在冲突，请重新预览。');
    if (code.includes('BUSY')) return text('An execution or recovery is active. Wait for its result before trying again.', '有执行或恢复尚未结束，请等待结果后重试。');
    if (code.includes('QUOTA') || code.includes('CAPACITY')) return text('Not enough recovery storage. Check the directory capacity.', '恢复存储空间不足，请检查目录容量。');
    if (code.includes('PERMISSION') || code.includes('DENIED')) return text('The directory cannot be accessed with current permissions.', '当前权限无法访问该目录。');
    return text('Unable to read or prepare recovery. Refresh and try again.', '无法读取或准备恢复，请刷新后重试。');
  }
  function stateLabel(state: string) {
    return ({ SUCCEEDED: text('Restore succeeded', '恢复成功'), QUEUED: text('Queued', '排队中'), RUNNING: text('Restoring', '正在恢复'),
      COMPENSATING: text('Compensating', '正在补偿'), COMPENSATED: text('Compensated; recovery did not complete', '已补偿；恢复未完成'),
      REJECTED: text('Recovery rejected', '恢复已拒绝'), NEEDS_ATTENTION: text('Needs attention', '需要处理'),
      FAILED: text('Recovery failed', '恢复失败') } as Record<string, string>)[state] ?? text('Result unknown', '结果未知');
  }
  useEffect(() => {
    if (visible) close.current?.focus();
  }, [visible]);
  useEffect(() => {
    const changed = () => setTabVisible(!document.hidden);
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  function acceptReceipt(items: RecoveryOperation[], refreshCurrent = false) {
    const command = submissionRef.current;
    const receipt = command ? items.find(item => item.request_id === command.request_id && item.kind === 'RESTORE') :
      refreshCurrent ? items.find(item => item.id === operationRef.current?.id) : undefined;
    if (receipt) {
      setOperation(receipt); setError('');
      if (command) { setSubmission(null); setPreview(null); setDiff(null); }
    }
  }
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    const reconcile = reconcileOnRefresh.current; reconcileOnRefresh.current = false;
    void Promise.all([api.scopes(), api.operations(0)]).then(([catalog, history]) => {
      if (cancelled) return;
      setScopes(catalog.items); setOperations(history.items); operationOffset.current = history.items.length;
      setOperationsMore(history.items.length === RECOVERY_PAGE_SIZE);
      acceptReceipt(history.items, reconcile);
    }).catch(() => { if (!cancelled) setError('READ_FAILED'); });
    return () => { cancelled = true; };
  }, [api, visible, refresh]);
  useEffect(() => {
    if (!visible || !tabVisible || !operation || !recoveryActive(operation.state)) return;
    let cancelled = false; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const current = await api.operation(operation.id);
        if (cancelled) return;
        setOperation(current); setError('');
        if (!recoveryActive(current.state)) { setRefresh(value => value + 1); return; }
      } catch { if (!cancelled) setError('UNCONFIRMED'); }
      if (!cancelled) timer = setTimeout(() => void poll(), 1000);
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api, visible, tabVisible, operation?.id, operation?.state]);
  useEffect(() => {
    if (!visible || !preview) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [visible, preview]);
  async function chooseScope(id: string) {
    if (locked) return;
    invalidate(); setScopeId(id); setVersionId(''); setVersions([]); setUndo(null); setSelected([]); setFilePage(0);
    versionOffset.current = 0; setVersionsMore(false); setLoading(Boolean(id));
    if (!id) return;
    const current = epoch.current;
    try {
      const page = await api.versions(id, 0);
      if (current !== epoch.current || !mounted.current) return;
      setVersions(page.items); versionOffset.current = page.items.length; setVersionsMore(page.items.length === RECOVERY_PAGE_SIZE);
    } catch (err) { if (current === epoch.current) setError(reason(err)); }
    finally { if (current === epoch.current) setLoading(false); }
  }
  async function moreVersions() {
    const current = epoch.current; setLoading(true);
    try {
      const page = await api.versions(scopeId, versionOffset.current);
      if (current !== epoch.current || !mounted.current) return;
      setVersions(items => [...items, ...page.items.filter(row => !items.some(item => item.id === row.id))]);
      versionOffset.current += page.items.length; setVersionsMore(page.items.length === RECOVERY_PAGE_SIZE);
    } catch (err) { if (current === epoch.current) setError(reason(err)); }
    finally { if (current === epoch.current) setLoading(false); }
  }
  async function moreOperations() {
    if (busyRef.current) return; busyRef.current = true; setBusy(true);
    try {
      const page = await api.operations(operationOffset.current);
      if (!mounted.current) return;
      setOperations(items => [...items, ...page.items.filter(row => !items.some(item => item.id === row.id))]);
      operationOffset.current += page.items.length; setOperationsMore(page.items.length === RECOVERY_PAGE_SIZE);
      acceptReceipt(page.items);
    } catch (err) { setError(reason(err)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  async function chooseUndo(item: RecoveryOperation) {
    if (locked || item.kind !== 'RESTORE' || item.state !== 'SUCCEEDED') return;
    invalidate(); const current = epoch.current; setLoading(true); setSelected([]); setFilePage(0); setVersionId(''); setUndo(null); setScopeId(item.scope_id ?? '');
    try {
      const full = await api.operation(item.id);
      if (current !== epoch.current || !mounted.current) return;
      if (full.kind !== 'RESTORE' || full.state !== 'SUCCEEDED' || !full.steps?.length) { setError('READ_FAILED'); return; }
      setUndo(full);
    } catch (err) { if (current === epoch.current) setError(reason(err)); }
    finally { if (current === epoch.current) setLoading(false); }
  }
  async function makePreview() {
    if (locked || loading || !selected.length || !contentReady || !scopeId || busyRef.current) return;
    invalidate(); const current = epoch.current; busyRef.current = true; setBusy(true);
    try {
      const result = await api.preview({ scope_id: scopeId, file_ids: selected,
        mode: undo ? 'undo_operation' : 'restore_version', ...(undo ? { operation_id: undo.id } : { version_id: versionId }) });
      if (current !== epoch.current || !mounted.current) return;
      if (result.status !== 'READY' || !result.preview_id || !result.digest || !result.selection?.length) { setError(result.reason_code ?? 'PREVIEW_CONFLICT'); return; }
      setNow(Date.now()); setPreview(result);
    } catch (err) { if (current === epoch.current) setError(reason(err)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  async function openDiff(file: RecoveryFile) {
    if (!preview?.preview_id) return;
    const current = ++diffEpoch.current; setDiff({ file: file.relative_path });
    try {
      const result = await api.diff(preview.preview_id, file.file_id);
      if (current === diffEpoch.current && mounted.current) setDiff({ file: file.relative_path, result });
    } catch { if (current === diffEpoch.current && mounted.current) setDiff({ file: file.relative_path, failed: true }); }
  }
  async function apply() {
    if (busyRef.current || (!submission && !previewReady)) return;
    const command = submission ?? { request_id: crypto.randomUUID(), preview_id: preview!.preview_id!, digest: preview!.digest! };
    setSubmission(command); busyRef.current = true; setBusy(true); setError(''); setOperation(null);
    try {
      const result = await api.apply(command);
      if (!mounted.current) return;
      if (!result.id || !result.state) { setError('UNCONFIRMED'); return; }
      setOperation(result); setSubmission(null); setPreview(null); setDiff(null); setRefresh(value => value + 1);
    } catch (err) {
      if (!mounted.current) return;
      if (err instanceof ApiActionError && err.status >= 400 && err.status < 500) {
        setSubmission(null); setPreview(null); setDiff(null); setError(reason(err));
      } else setError('UNCONFIRMED');
    } finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  if (!visible) return null;
  return <section ref={panel} className="work-recovery" role="region" aria-label={text('File recovery', '文件恢复')} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
  }}>
    <header><h2>{text('File recovery', '文件恢复')}</h2><button ref={close} type="button" aria-label={text('Close file recovery', '关闭文件恢复')} onClick={onClose}>×</button></header>
    <div className="work-recovery-content">
      <p>{text('All protected directories. Recovery affects the selected directory, including other work that uses it.', '所有已保护目录。恢复会影响所选目录，包括使用该目录的其他工作。')}</p>
      <button type="button" disabled={busy} onClick={() => { reconcileOnRefresh.current = true; setRefresh(value => value + 1); }}>{text('Refresh records', '刷新记录')}</button>
      {error && <p role="alert">{message(error)}</p>}
      {submission && <button type="button" disabled={busy} onClick={() => void apply()}>{text('Retry same submission', '重试同一提交')}</button>}
      {operation && <div role="status" data-testid="recovery-result"><strong>{stateLabel(operation.state)}</strong><code>{operation.id}</code>
        {operation.state === 'NEEDS_ATTENTION' && <p>{text('Resolve this operation before starting another recovery, then refresh records.', '请先处理此操作，再刷新记录后开始其他恢复。')}</p>}
        {operation.reason_code && !['SUCCEEDED', 'QUEUED', 'RUNNING', 'COMPENSATING'].includes(operation.state) && <p>{message(operation.reason_code)}</p>}</div>}
      <label>{text('Protected directory', '已保护目录')}<select aria-label={text('Protected directory', '已保护目录')} value={scopeId} disabled={locked || loading} onChange={event => void chooseScope(event.target.value)}>
        <option value="">{text('Select a directory', '选择目录')}</option>
        {scopes.map(item => <option key={item.id} value={item.id} disabled={item.status === 'RETIRED'}>{item.root} · {item.source_id} · {item.status}</option>)}
      </select></label>
      {!scopes.length && <p>{text('No protected directories available.', '暂无可用的已保护目录。')}</p>}
      {scope && <p className="work-recovery-path">{scope.root}</p>}
      {undo ? <p>{text('Undo operation', '撤销操作')} <code>{undo.id}</code><button type="button" disabled={locked} onClick={() => void chooseScope(scopeId)}>{text('Return to versions', '返回版本')}</button></p> : scopeId && <>
        <label>{text('Directory version', '目录版本')}<select aria-label={text('Directory version', '目录版本')} value={versionId} disabled={locked || loading} onChange={event => { invalidate(); setVersionId(event.target.value); setSelected([]); setFilePage(0); }}>
          <option value="">{text('Select a version', '选择版本')}</option>
          {versions.map(item => <option key={item.id} value={item.id} disabled={!item.available || !item.metadata_available || !item.details_included}>{date(item.created_at)} · {item.id}{!item.available || !item.details_included ? text(' · Content unavailable', ' · 内容不可用') : ''}</option>)}
        </select></label>
        {!loading && !versions.length && <p>{text('No versions available.', '暂无版本。')}</p>}
        {versionsMore && <button type="button" disabled={locked || loading} onClick={() => void moreVersions()}>{text('Load more versions', '加载更多版本')}</button>}
      </>}
      {loading && <p role="status">{text('Loading', '正在读取')}</p>}
      {contentReady && <fieldset disabled={locked || loading}><legend>{text('Select files', '选择文件')} ({selected.length}/1000)</legend>
        {files.slice(filePage * 50, (filePage + 1) * 50).map(file => <label className="work-recovery-file" key={file.file_id}><input type="checkbox" aria-label={file.relative_path}
          checked={selected.includes(file.file_id)} disabled={file.category !== 'restorable' || (!selected.includes(file.file_id) && selected.length >= 1000)}
          onChange={event => { invalidate(); setSelected(ids => event.target.checked ? [...ids, file.file_id] : ids.filter(id => id !== file.file_id)); }} />
          <span>{file.relative_path}{file.category !== 'restorable' && <small>{text('Content unavailable or excluded', '内容不可用或已排除')}</small>}</span></label>)}
        {!files.length && <p>{text('No files available.', '暂无文件。')}</p>}
        {filePage > 0 && <button type="button" onClick={() => setFilePage(page => page - 1)}>{text('Previous files', '上一页文件')}</button>}
        {(filePage + 1) * 50 < files.length && <button type="button" onClick={() => setFilePage(page => page + 1)}>{text('Next files', '下一页文件')}</button>}
      </fieldset>}
      <button type="button" disabled={locked || loading || !selected.length || !contentReady} onClick={() => void makePreview()}>{undo ? text('Preview undo', '预览撤销') : text('Preview restore', '预览恢复')}</button>
      {preview && <section className="work-recovery-preview" aria-label={text('Recovery preview', '恢复预览')}>
        <h3>{text('Recovery preview', '恢复预览')}</h3><p>{scope?.root ?? scopeId}</p>
        <p>{text('Only the selected files will change. Other files remain unchanged.', '只修改所选文件，其他文件保留。')}</p>
        <p>{text('Affected works', '受影响工作')}</p>{preview.affected_work_ids?.map(id => <code key={id}>{id}</code>)}
        {!preview.affected_work_ids?.length && <p>{text('No linked work recorded.', '未记录关联工作。')}</p>}
        {preview.selection?.map(file => <div key={file.file_id}><p>{file.relative_path} · {file.after === null ? text('Delete', '删除') : file.before === null ? text('Create', '创建') : text('Replace', '替换')}</p>
          <button type="button" aria-label={text('View diff ', '查看差异 ') + file.relative_path} onClick={() => void openDiff(file)}>{text('View diff', '查看差异')}</button></div>)}
        {diff && <div><h4>{diff.file}</h4>{diff.failed ? <p role="alert">{text('Diff unavailable. Refresh the preview.', '差异不可用，请刷新预览。')}</p> : !diff.result ? <p role="status">{text('Loading diff', '正在读取差异')}</p> : <>
          {diff.result.binary ? <p>{text('Binary file; text diff unavailable.', '二进制文件，无法显示文本差异。')}</p> : diff.result.text_diff === null ? <p>{text('Diff unavailable', '差异不可用')}</p> : <pre>{diff.result.text_diff || text('No text changes', '没有文本变化')}</pre>}
          {diff.result.truncated && <p>{text('Diff truncated', '差异已截断')}</p>}</>}</div>}
        {!previewReady && !submission && <p role="alert">{text('Preview expired. Create a new preview.', '预览已过期，请重新预览。')}</p>}
        <button type="button" disabled={!previewReady || locked} onClick={() => void apply()}>{undo ? text('Confirm undo', '确认撤销') : text('Confirm restore', '确认恢复')}</button>
      </section>}
      <h3>{text('Global recovery history', '全局恢复记录')}</h3>
      {!operations.some(item => item.kind === 'RESTORE') && <p>{text('No recovery operations on this page.', '本页暂无恢复操作。')}</p>}
      {operations.filter(item => item.kind === 'RESTORE').map(item => <article key={item.id}><code>{item.id}</code><p>{scopes.find(candidate => candidate.id === item.scope_id)?.root ?? item.scope_id}</p>
        <p>{date(item.created_at)} · {stateLabel(item.state)}</p><button type="button" disabled={locked} onClick={() => { setOperation(item); setError(''); }}>{text('View result', '查看结果')}</button>
        {item.state === 'SUCCEEDED' && <button type="button" disabled={locked || loading} aria-label={text('Undo ', '撤销 ') + item.id} onClick={() => void chooseUndo(item)}>{text('Undo', '撤销')}</button>}</article>)}
      {operationsMore && <button type="button" disabled={busy} onClick={() => void moreOperations()}>{text('Load more records', '加载更多记录')}</button>}
    </div>
  </section>;
}
