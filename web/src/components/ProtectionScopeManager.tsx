import { useEffect, useRef, useState } from 'react';
import { ApiActionError, type ApiClient } from '../api/client';
import type { ProtectionScope } from '../api/execution';
import {
  protectionApi, PROTECTION_MIB, type ProtectionCandidate, type ProtectionScopeCommand,
  type ProtectionSource,
} from '../api/protection';
import { useI18n } from '../i18n/I18nProvider';
import './ProtectionScopeManager.css';

type Props = {
  client: ApiClient; sourceId?: string; selectedScopeId?: string; scopes: ProtectionScope[];
  onScopesChanged: (items: ProtectionScope[], selectedId?: string) => void;
  pending: ProtectionScopeCommand | null;
  onPendingChange: (command: ProtectionScopeCommand | null) => void;
  disabled?: boolean;
};
const safeCode = (code: unknown) => typeof code === 'string' && /^[A-Z0-9_]{1,64}$/.test(code) ? code : '';
export default function ProtectionScopeManager({ client, sourceId, selectedScopeId, scopes,
  onScopesChanged, pending, onPendingChange, disabled = false }: Props) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const text = (en: string, cn: string) => zh ? cn : en;
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<ProtectionSource[]>([]);
  const [candidates, setCandidates] = useState<ProtectionCandidate[]>([]);
  const [chosenSource, setChosenSource] = useState('');
  const [path, setPath] = useState('');
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [days, setDays] = useState('30');
  const [quota, setQuota] = useState('2048');
  const mounted = useRef(true), inFlight = useRef(false), loadEpoch = useRef(0);
  const currentSourceId = sourceId ?? chosenSource;
  const source = sources.find(item => item.source_id === currentSourceId);
  const selectedScope = scopes.find(item => item.id === selectedScopeId);
  const scope = selectedScope?.source_id === currentSourceId ? selectedScope : undefined;
  const locked = disabled || busy || Boolean(pending);
  const available = !loading && source?.running === true && source.connected === true && source.capabilities?.scan === true;
  const api = protectionApi(client);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; loadEpoch.current++; }; }, []);
  useEffect(() => {
    if (sourceId === undefined && selectedScope && !pending) setChosenSource(selectedScope.source_id);
  }, [sourceId, selectedScopeId, selectedScope?.source_id, pending]);
  useEffect(() => {
    setPath(''); setError(''); setNotice('');
  }, [currentSourceId]);
  useEffect(() => {
    setEnabled(Boolean(scope?.enabled)); setDays(String(scope?.retention_days ?? 30));
    setQuota(String((scope?.quota_bytes ?? 2147483648) / PROTECTION_MIB));
  }, [scope?.id, scope?.revision, scope?.enabled, scope?.retention_days, scope?.quota_bytes]);

  async function loadCandidates(append = false) {
    if (inFlight.current) return;
    const epoch = ++loadEpoch.current;
    setLoading(true);
    try {
      const nextOffset = append ? offset : 0;
      const page = await api.candidates(nextOffset);
      if (!Array.isArray(page.items) || !Array.isArray(page.sources) || typeof page.has_more !== 'boolean') throw new Error();
      if (!mounted.current || epoch !== loadEpoch.current) return;
      setSources(page.sources); setCandidates(items => append ? [...items, ...page.items] : page.items);
      setOffset(nextOffset + page.limit); setMore(page.has_more); setError('');
    } catch {
      if (mounted.current && epoch === loadEpoch.current) { setSources([]); setError('LOAD_FAILED'); }
    } finally { if (mounted.current && epoch === loadEpoch.current) setLoading(false); }
  }
  useEffect(() => {
    if (open) void loadCandidates();
    return () => { loadEpoch.current++; };
  }, [client, open]);

  async function submit(command: ProtectionScopeCommand) {
    if (inFlight.current || disabled) return;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    let confirmed = Boolean(command.receipt);
    onPendingChange(command);
    try {
      let receipt = command.receipt;
      if (!receipt) {
        receipt = await api.submit(command);
        if (!receipt || typeof receipt.scope_id !== 'string' || !receipt.scope_id ||
          !Number.isInteger(receipt.revision) || receipt.revision < 1 ||
          command.kind === 'configure' && (receipt.scope_id !== command.scopeId || receipt.revision !== command.payload.expected_revision + 1)) throw new Error();
        confirmed = true;
        command = { ...command, receipt };
        onPendingChange(command);
      }
      const page = await api.scopes();
      const found = Array.isArray(page.items) && page.items.find(item => item.id === receipt.scope_id);
      if (!found || found.source_id !== command.sourceId ||
        typeof found.revision !== 'number' || found.revision < receipt.revision) throw new Error();
      onScopesChanged(page.items, receipt.scope_id);
      onPendingChange(null);
      if (mounted.current) {
        setPath(''); setNotice(command.kind === 'register' ? 'REGISTERED' : 'CONFIGURED');
      }
    } catch (err) {
      if (!confirmed && err instanceof ApiActionError && err.status >= 400 && err.status < 500) {
        onPendingChange(null);
        if (err.reasonCode === 'PROTECTION_REVISION_CONFLICT') {
          try {
            const page = await api.scopes();
            if (!Array.isArray(page.items)) throw new Error();
            onScopesChanged(page.items);
          } catch { if (mounted.current) setError('REFRESH_FAILED'); return; }
        }
        if (mounted.current) setError(safeCode(err.reasonCode) || 'REJECTED');
      } else if (mounted.current) setError('UNCONFIRMED');
    } finally {
      inFlight.current = false; if (mounted.current) setBusy(false);
    }
  }
  function register() {
    if (locked || !available || !path.trim() || !currentSourceId) return;
    void submit({ kind: 'register', sourceId: currentSourceId, path: path.trim(),
      payload: { request_id: crypto.randomUUID(), source_id: currentSourceId, path: path.trim() } });
  }
  const quotaBytes = quota === String((scope?.quota_bytes ?? 2147483648) / PROTECTION_MIB)
    ? scope?.quota_bytes ?? 2147483648 : Number(quota) * PROTECTION_MIB;
  const policyValid = /^\d+$/.test(days) && Number(days) >= 1 && Number(days) <= 3650 &&
    quota.trim() !== '' && Number.isSafeInteger(quotaBytes) && quotaBytes >= 1024 && quotaBytes <= 1099511627776;
  function configure() {
    if (locked || !scope || scope.status === 'RETIRED' || !Number.isInteger(scope.revision) || !policyValid) return;
    void submit({ kind: 'configure', sourceId: scope.source_id, path: scope.root, scopeId: scope.id,
      payload: { request_id: crypto.randomUUID(), expected_revision: scope.revision!,
        enabled, retention_days: Number(days), quota_bytes: quotaBytes } });
  }
  const errorMessage = error === 'UNCONFIRMED'
    ? text('The result is unconfirmed. Retry the same submission to check it.', '结果尚未确认，请重试同一提交以核对。')
    : error === 'PROTECTION_REVISION_CONFLICT'
      ? text('Protection settings changed. Review the refreshed settings before submitting again.', '保护设置已改变，请核对刷新后的设置再提交。')
      : error === 'LOAD_FAILED' || error === 'REFRESH_FAILED'
        ? text('Directory records could not be refreshed.', '未能刷新目录记录。')
        : text('The action was rejected.', '操作被拒绝。');
  return <details className="protection-scope-manager" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{text('Manage protected directories', '管理保护目录')}{pending ? text(' · Submission pending', ' · 提交待确认') : ''}</summary>
    <div className="protection-scope-content">
      <p>{text('Protection applies to the whole selected directory and all work using it.', '保护针对整个所选目录及所有使用该目录的工作。')}</p>
      {error && <p role="alert">{errorMessage}{safeCode(error) && !['UNCONFIRMED', 'LOAD_FAILED', 'REFRESH_FAILED', 'REJECTED'].includes(error) ? ` ${error}` : ''}</p>}
      {notice && <p role="status">{notice === 'REGISTERED' ? text('Directory registered. Check its protection status below.', '目录已登记，请核对下方保护状态。') : text('Protection settings saved.', '保护设置已保存。')}</p>}
      {pending && <div className="protection-scope-pending" role="status">
        <p>{text('Pending directory', '待确认目录')}: {pending.sourceId} · {pending.path}</p>
        <button type="button" disabled={disabled || busy} onClick={() => void submit(pending)}>
          {pending.receipt ? text('Refresh confirmed submission', '刷新已确认提交') : text('Retry same submission', '重试同一提交')}
        </button>
      </div>}
      <button type="button" disabled={locked || loading} onClick={() => void loadCandidates()}>{text('Refresh directory candidates', '刷新目录候选')}</button>
      {loading && <p role="status">{text('Loading directory candidates', '正在读取目录候选')}</p>}
      {sourceId === undefined ? <label>{text('Directory source', '目录来源')}<select value={chosenSource} disabled={locked} onChange={event => setChosenSource(event.target.value)}>
        <option value="">{text('Select source', '选择来源')}</option>
        {sources.map(item => <option key={item.source_id} value={item.source_id}>{item.label} · {item.source_id}{!item.running || !item.connected ? text(' · Unavailable', ' · 不可用') : ''}</option>)}
      </select></label> : <p>{text('Directory source', '目录来源')}: {source?.label ?? sourceId} · {sourceId}</p>}
      {currentSourceId && !available && !loading && <p>{text('Source is unavailable for registration. Connect it through environment controls first.', '该来源暂不可登记，请先在环境控制中连接。')}</p>}
      <label>{text('Observed directory candidate', '观测到的目录候选')}<select value="" disabled={locked || !available} onChange={event => {
        const candidate = candidates[Number(event.target.value)];
        if (event.target.value && candidate?.source_id === currentSourceId) setPath(candidate.path);
      }}>
        <option value="">{text('Choose a candidate to fill the path', '选择候选以填写路径')}</option>
        {candidates.map((item, index) => item.source_id === currentSourceId && <option key={`${item.session_id}:${index}`} value={index}>{item.path} · {item.current ? text('Recent observation', '近期观测') : text('Older observation', '历史观测')}</option>)}
      </select></label>
      {more && <button type="button" disabled={locked || loading} onClick={() => void loadCandidates(true)}>{text('Load more directory candidates', '加载更多目录候选')}</button>}
      <p>{text('Candidates only fill the path. Registration validates the directory and starts protection with 30 days retention and a 2048 MiB quota.', '候选仅用于填写路径。登记将验证目录，并按保留30天、配额2048 MiB启用保护。')}</p>
      <label>{text('Project directory path', '项目目录路径')}<input value={path} maxLength={4096} disabled={locked || !available} onChange={event => setPath(event.target.value)} /></label>
      <button type="button" disabled={locked || !available || !path.trim()} onClick={register}>{text('Register and protect directory', '登记并保护目录')}</button>
      {scope && <section aria-label={text('Directory protection settings', '目录保护设置')}>
        <h4>{text('Selected directory protection', '所选目录保护')}</h4>
        <p className="protection-scope-root">{scope.root} · {scope.status}{safeCode(scope.reason_code) ? ` · ${scope.reason_code}` : ''}</p>
        <fieldset disabled={locked || scope.status === 'RETIRED' || !Number.isInteger(scope.revision)}>
          <label className="protection-scope-check"><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} />{text('Protection enabled', '启用保护')}</label>
          <label>{text('Retention days', '保留天数')}<input type="number" min="1" max="3650" step="1" value={days} onChange={event => setDays(event.target.value)} /></label>
          <label>{text('Storage quota (MiB)', '存储配额（MiB）')}<input type="number" min="0.0009765625" max="1048576" step="any" value={quota} onChange={event => setQuota(event.target.value)} /></label>
          <p>{text('Reducing retention or quota may release older recovery content. Pausing protection stops new observations.', '缩短保留期或降低配额可能清理旧恢复内容。暂停保护会停止新的观测。')}</p>
          <button type="button" disabled={!policyValid} onClick={configure}>{text('Save protection settings', '保存保护设置')}</button>
        </fieldset>
      </section>}
    </div>
  </details>;
}
