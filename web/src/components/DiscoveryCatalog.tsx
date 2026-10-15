import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiActionError, type ApiClient } from '../api/client';
import { DISCOVERY_POLL_INTERVAL_MS } from '../api/product';
import type {
  DiscoveryApplication, DiscoveryInventoryCoverage, DiscoveryPage, DiscoverySessionStore,
  DiscoverySessionsPage, DiscoverySource,
} from '../api/types';

const PAGE_SIZE = 50;
const STORE_PROVIDERS = ['CODEX', 'KIMI_CODE', 'ZCODE', 'CLOUDCLI'] as const;

export function DiscoveryCatalog({ client, source, coverage, visible, refreshKey, zh, locale }: {
  client: ApiClient;
  source: DiscoverySource;
  coverage?: DiscoveryInventoryCoverage;
  visible: boolean;
  refreshKey: number;
  zh: boolean;
  locale: string;
}) {
  const [open, setOpen] = useState(false);
  const [showUnclassified, setShowUnclassified] = useState(false);
  const [applications, setApplications] = useState<DiscoveryPage<DiscoveryApplication> | null>(null);
  const [stores, setStores] = useState<DiscoveryPage<DiscoverySessionStore> | null>(null);
  const [sessions, setSessions] = useState<DiscoverySessionsPage | null>(null);
  const [applicationOffset, setApplicationOffset] = useState(0);
  const [storeOffset, setStoreOffset] = useState(0);
  const [sessionOffset, setSessionOffset] = useState(0);
  const [selectedStore, setSelectedStore] = useState('');
  const [selectedStoreLabel, setSelectedStoreLabel] = useState('');
  const [sessionOpen, setSessionOpen] = useState(false);
  const [applicationError, setApplicationError] = useState(false);
  const [storeError, setStoreError] = useState(false);
  const [sessionError, setSessionError] = useState(false);
  const [mutationError, setMutationError] = useState('');
  const [busy, setBusy] = useState(false);
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [applicationPath, setApplicationPath] = useState('');
  const [applicationLabel, setApplicationLabel] = useState('');
  const [storePath, setStorePath] = useState('');
  const [storeProvider, setStoreProvider] = useState<(typeof STORE_PROVIDERS)[number]>('CODEX');
  const applicationPending = useRef(false);
  const storePending = useRef(false);
  const sessionPending = useRef(false);
  const sourceId = source.source_id;
  const current = source.enabled && source.freshness === 'FRESH' &&
    ['OBSERVED', 'PARTIAL', 'EMPTY'].includes(source.state);
  const unavailable = zh ? '来源不可用' : 'Source unavailable';
  const installedState = (app: DiscoveryApplication) => !current ? unavailable :
    app.status === 'RUNNING' ? 'UNVERIFIED' :
    app.status === 'AVAILABLE' && app.installation_freshness !== 'FRESH' ?
      app.installation_freshness === 'STALE' ? 'STALE' : 'UNVERIFIED' : app.status || 'UNKNOWN';
  const storeState = (store: DiscoverySessionStore) => !current ? unavailable :
    store.reason_code === 'SESSION_STORE_NOT_PRESENT' ? (zh ? '未找到会话库' : 'Store not present') :
    store.status === 'AVAILABLE' && store.freshness === 'STALE' ? 'STALE' : store.status;
  const interactionState = (capabilities: DiscoveryApplication['control_capabilities'] | undefined) =>
    Boolean(capabilities && (capabilities.create || capabilities.continue || capabilities.send ||
      capabilities.stream || capabilities.cancel)) ? (zh ? '可用' : 'Available') : (zh ? '不可用' : 'Unavailable');

  useEffect(() => {
    if (!open || !visible) return;
    let active = true;
    const poll = async () => {
      if (document.visibilityState !== 'visible' || applicationPending.current) return;
      applicationPending.current = true;
      try {
        const suffix = showUnclassified ? '&include_unclassified=true' : '';
        const page = await client.get<DiscoveryPage<DiscoveryApplication> & { unclassified_count?: number }>(
          `/api/v1/discovery/applications?source_id=${encodeURIComponent(sourceId)}&limit=${PAGE_SIZE}&offset=${applicationOffset}${suffix}`,
        );
        if (active) { setApplications(page); setApplicationError(false); }
      } catch {
        if (active) { setApplications(null); setApplicationError(true); }
      } finally { applicationPending.current = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), DISCOVERY_POLL_INTERVAL_MS);
    return () => { active = false; window.clearInterval(timer); };
  }, [client, sourceId, open, visible, applicationOffset, showUnclassified, refreshKey, catalogRevision]);

  useEffect(() => {
    if (!open || !visible) return;
    let active = true;
    const poll = async () => {
      if (document.visibilityState !== 'visible' || storePending.current) return;
      storePending.current = true;
      try {
        const page = await client.get<DiscoveryPage<DiscoverySessionStore>>(
          `/api/v1/discovery/sources/${encodeURIComponent(sourceId)}/session-stores?limit=${PAGE_SIZE}&offset=${storeOffset}`,
        );
        if (active) { setStores(page); setStoreError(false); }
      } catch {
        if (active) { setStores(null); setStoreError(true); }
      } finally { storePending.current = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), DISCOVERY_POLL_INTERVAL_MS);
    return () => { active = false; window.clearInterval(timer); };
  }, [client, sourceId, open, visible, storeOffset, refreshKey, catalogRevision]);

  useEffect(() => {
    if (!open || !sessionOpen || !selectedStore || !visible || !current) return;
    let active = true;
    const poll = async () => {
      if (document.visibilityState !== 'visible' || sessionPending.current) return;
      sessionPending.current = true;
      try {
        const page = await client.get<DiscoverySessionsPage>(
          `/api/v1/discovery/sources/${encodeURIComponent(sourceId)}/sessions?store_id=${encodeURIComponent(selectedStore)}&limit=${PAGE_SIZE}&offset=${sessionOffset}`,
        );
        if (active) { setSessions(page); setSessionError(false); }
      } catch {
        if (active) { setSessions(null); setSessionError(true); }
      } finally { sessionPending.current = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), DISCOVERY_POLL_INTERVAL_MS);
    return () => { active = false; window.clearInterval(timer); };
  }, [client, sourceId, open, sessionOpen, selectedStore, visible, current, sessionOffset, refreshKey, catalogRevision]);

  useEffect(() => {
    if (!current) setSessions(null);
  }, [current]);

  async function registerApplication(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!applicationPath.trim() || busy) return;
    await submit('/api/v1/discovery/applications', {
      source_id: sourceId, path: applicationPath.trim(),
      ...(applicationLabel.trim() ? { label: applicationLabel.trim() } : {}),
    }, () => { setApplicationPath(''); setApplicationLabel(''); });
  }

  async function registerCandidate(candidateId: string) {
    if (busy) return;
    await submit('/api/v1/discovery/applications', { source_id: sourceId, candidate_id: candidateId });
  }

  async function registerStore(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!storePath.trim() || busy) return;
    await submit('/api/v1/discovery/session-stores', {
      source_id: sourceId, provider: storeProvider, path: storePath.trim(),
    }, () => setStorePath(''));
  }

  async function submit(path: string, body: object, after?: () => void) {
    setBusy(true); setMutationError('');
    try {
      await client.post(path, body);
      after?.();
      setCatalogRevision(value => value + 1);
    } catch (error) {
      const code = error instanceof ApiActionError ? error.reasonCode : null;
      setMutationError(`${zh ? '登记未完成。' : 'Registration failed.'}${code ? ` ${code}` : ''}`);
    } finally { setBusy(false); }
  }

  const unclassifiedCount = (applications as (DiscoveryPage<DiscoveryApplication> & { unclassified_count?: number }) | null)?.unclassified_count ?? 0;
  return <details className="work-agent-catalog" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{zh ? '程序与会话库' : 'Programs and session stores'}</summary>
    {coverage && <p className="work-agent-catalog-meta">{zh ? '程序盘点' : 'Program inventory'}: {coverage.status}
      {typeof coverage.observed === 'number' && ` · ${coverage.observed} ${zh ? '项' : 'found'}`}</p>}
    <div className="work-agent-catalog-section">
      <div className="work-agent-catalog-heading"><h4>{zh ? '程序' : 'Programs'}</h4>
        <label><input type="checkbox" checked={showUnclassified} onChange={event => {
          setShowUnclassified(event.target.checked); setApplicationOffset(0); setApplications(null);
        }} />{zh ? '未分类程序' : 'Unclassified programs'} ({unclassifiedCount})</label></div>
      {applicationError && <p role="alert">{zh ? '未能读取程序目录。' : 'Could not load programs.'}</p>}
      {!applications && !applicationError && <p>{zh ? '读取中…' : 'Loading…'}</p>}
      {applications && !applications.items.length && <p>{zh ? '此来源尚无程序记录。' : 'No programs recorded for this source.'}</p>}
      {applications?.items.map(app => <div className="work-agent-catalog-row" key={app.id}>
        <strong>{app.label || (app.provider === 'UNKNOWN' ? app.path.replace(/\\/g, '/').split('/').pop() : app.provider)}</strong><span className="work-agent-catalog-path">{app.path}</span>
        <small>{zh ? '安装核验' : 'Installation'}: {installedState(app)} · {zh ? '运行中' : 'Running'}: {current ? app.running ? (zh ? '是' : 'Yes') : (zh ? '否' : 'No') : unavailable}
          {' · '}{zh ? '交互' : 'Interaction'}: {interactionState(app.control_capabilities)}
          {(!current || app.installation_freshness !== 'FRESH') && (app.installation_observed_at || app.observed_at) &&
            ` · ${zh ? '上次观察' : 'Last observed'}: ${new Date(app.installation_observed_at || app.observed_at).toLocaleString(locale)}`}</small>
        <small>{app.classification === 'UNCLASSIFIED' ? (zh ? '未分类程序' : 'Unclassified program') :
          app.registered ? (zh ? '已登记' : 'Registered') : (zh ? '自动识别' : 'Automatically recognized')}
          {app.version && ` · ${app.version}`}</small>
        {!app.registered && current && app.status === 'AVAILABLE' &&
          <button type="button" disabled={busy} onClick={() => void registerCandidate(app.id)}>{zh ? '登记' : 'Register'}</button>}
      </div>)}
      {applications && <div className="work-agent-catalog-pages">
        <button type="button" disabled={applicationOffset === 0} onClick={() => { setApplicationOffset(Math.max(0, applicationOffset - PAGE_SIZE)); setApplications(null); }}>{zh ? '上一页' : 'Previous'}</button>
        <span>{Math.floor(applicationOffset / PAGE_SIZE) + 1}</span>
        <button type="button" disabled={!applications.has_more || applications.next_offset == null} onClick={() => { setApplicationOffset(applications.next_offset!); setApplications(null); }}>{zh ? '下一页' : 'Next'}</button>
      </div>}
      <details><summary>{zh ? '登记程序' : 'Register program'}</summary>
        <form className="work-agent-catalog-form" onSubmit={event => void registerApplication(event)}>
          <label>{zh ? '程序路径' : 'Program path'}<input value={applicationPath} onChange={event => setApplicationPath(event.target.value)} required /></label>
          <label>{zh ? '名称（可选）' : 'Name (optional)'}<input value={applicationLabel} onChange={event => setApplicationLabel(event.target.value)} /></label>
          <button type="submit" disabled={!current || busy || !applicationPath.trim()}>{zh ? '登记程序' : 'Register program'}</button>
        </form>
      </details>
    </div>
    <div className="work-agent-catalog-section">
      <h4>{zh ? '会话库' : 'Session stores'}</h4>
      {storeError && <p role="alert">{zh ? '未能读取会话库。' : 'Could not load session stores.'}</p>}
      {!stores && !storeError && <p>{zh ? '读取中…' : 'Loading…'}</p>}
      {stores && !stores.items.length && <p>{zh ? '此来源尚无会话库。' : 'No session stores recorded for this source.'}</p>}
      {stores?.items.map(store => <div className="work-agent-catalog-row" key={store.id}>
        <strong>{store.provider}</strong><span className="work-agent-catalog-path">{store.path}</span>
        <small>{zh ? '库状态' : 'Store status'}: {storeState(store)} · {zh ? '会话读取' : 'Sessions'}: {!current ? unavailable : selectedStore === store.id && sessions && !sessionError ? sessions.status : (zh ? '未读取' : 'Not read')}
          {' · '}{zh ? '交互' : 'Interaction'}: {interactionState(store.control_capabilities)}
          {(!current || store.freshness === 'STALE') && ` · ${zh ? '上次观察' : 'Last observed'}: ${new Date(store.observed_at).toLocaleString(locale)}`}</small>
        <small>{store.registered ? (zh ? '已登记' : 'Registered') : store.reason_code === 'SESSION_STORE_NOT_PRESENT' ? (zh ? '默认候选位置' : 'Default candidate location') : (zh ? '自动发现' : 'Automatically discovered')}</small>
      </div>)}
      {stores && <div className="work-agent-catalog-pages">
        <button type="button" disabled={storeOffset === 0} onClick={() => { setStoreOffset(Math.max(0, storeOffset - PAGE_SIZE)); setStores(null); }}>{zh ? '上一页' : 'Previous'}</button>
        <span>{Math.floor(storeOffset / PAGE_SIZE) + 1}</span>
        <button type="button" disabled={!stores.has_more || stores.next_offset == null} onClick={() => { setStoreOffset(stores.next_offset!); setStores(null); }}>{zh ? '下一页' : 'Next'}</button>
      </div>}
      <details><summary>{zh ? '登记会话库' : 'Register session store'}</summary>
        <form className="work-agent-catalog-form" onSubmit={event => void registerStore(event)}>
          <label>{zh ? '提供方' : 'Provider'}<select value={storeProvider} onChange={event => setStoreProvider(event.target.value as typeof storeProvider)}>
            {STORE_PROVIDERS.map(provider => <option key={provider} value={provider}>{provider}</option>)}
          </select></label>
          <label>{zh ? '会话库路径' : 'Session store path'}<input value={storePath} onChange={event => setStorePath(event.target.value)} required /></label>
          <button type="submit" disabled={!current || busy || !storePath.trim()}>{zh ? '登记会话库' : 'Register session store'}</button>
        </form>
      </details>
    </div>
    <details className="work-agent-catalog-section" open={sessionOpen} onToggle={event => setSessionOpen(event.currentTarget.open)}>
      <summary>{zh ? '提供方会话' : 'Provider sessions'}</summary>
      <label className="work-agent-store-select">{zh ? '选择会话库' : 'Select session store'}
        <select value={selectedStore} onChange={event => {
          const next = event.target.value;
          setSelectedStore(next);
          setSelectedStoreLabel(event.target.selectedOptions[0]?.textContent || '');
          setSessionOffset(0); setSessions(null); setSessionError(false);
        }}>
          <option value="">{zh ? '请选择' : 'Choose a store'}</option>
          {selectedStore && !stores?.items.some(store => store.id === selectedStore) &&
            <option value={selectedStore}>{selectedStoreLabel}</option>}
          {stores?.items.map(store => <option key={store.id} value={store.id}>{store.provider} · {store.path}</option>)}
        </select>
      </label>
      {!selectedStore && <p>{zh ? '选择会话库后读取其会话。' : 'Select a store to read its sessions.'}</p>}
      {selectedStore && !current && <p>{zh ? '来源当前不可读取。' : 'This source is not currently readable.'}</p>}
      {sessionError && <p role="alert">{zh ? '未能读取会话。' : 'Could not read sessions.'}</p>}
      {selectedStore && current && !sessions && !sessionError && <p>{zh ? '读取中…' : 'Loading…'}</p>}
      {current && !sessionError && sessions && <div className="work-agent-session-result">
        <small>{zh ? '提供方会话，不关联此工作或进程' : 'Provider sessions, not linked to this work or process'} · {sessions.status}
          {sessions.metadata_scope === 'PAGE' && ` · ${zh ? '按页检查元数据' : 'Metadata checked per page'}`}
          {sessions.observed_at && ` · ${new Date(sessions.observed_at).toLocaleTimeString(locale)}`}</small>
        {sessions.reason_code === 'SESSION_STORE_SELECTION_REQUIRED' && <p role="status">{zh ? '请明确选择会话库。' : 'Choose a session store explicitly.'}</p>}
        {sessions.sessions.map(session => <p key={session.binding_id || `${selectedStore}:${session.session_id}`}>
          {session.title || (zh ? '无标题会话' : 'Untitled session')}
          {session.updated_at && ` · ${new Date(session.updated_at).toLocaleString(locale)}`}
          {session.cwd && <small>{session.cwd}</small>}
        </p>)}
        {!sessions.sessions.length && <p>{zh ? '没有可显示的会话。' : 'No sessions to show.'}</p>}
        <div className="work-agent-catalog-pages">
          <button type="button" disabled={sessionOffset === 0} onClick={() => { setSessionOffset(Math.max(0, sessionOffset - PAGE_SIZE)); setSessions(null); }}>{zh ? '上一页' : 'Previous'}</button>
          <span>{Math.floor(sessionOffset / PAGE_SIZE) + 1}</span>
          <button type="button" disabled={!sessions.has_more || sessions.next_offset == null} onClick={() => { setSessionOffset(sessions.next_offset!); setSessions(null); }}>{zh ? '下一页' : 'Next'}</button>
        </div>
      </div>}
    </details>
    {mutationError && <p role="alert">{mutationError}</p>}
  </details>;
}
