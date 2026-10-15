import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiActionError, ApiRequestError, type ApiClient } from '../api/client';
import { executionBase, normalizeRunEvent, runIsActive, type ExecutionPage, type ProtectionScope, type RunEvent } from '../api/execution';
import { useI18n } from '../i18n/I18nProvider';
import type { WorkRunOutput, WorkSessionMemory } from './WorkSessions';
import './WorkDelivery.css';

type Artifact = { id: string; title: string; version: number; requirement_version: number };
type ArtifactRef = { artifact_id: string; version: number };
type FileRef = { scope_id: string; version_id: string };
type Review = { id: string; decision: 'APPROVED' | 'CHANGES_REQUESTED'; body: string; reviewer: string; created_at: string };
type Acceptance = { id: string; decision: 'ACCEPTED' | 'REJECTED'; basis: string; owner: string; created_at: string };
type Submission = { id: string; requirement_version: number; run_id: string | null; responsibility: string;
  refs: { artifacts: ArtifactRef[]; files: FileRef[] }; current_requirement: boolean;
  reviews: Review[]; acceptances: Acceptance[]; created_at?: string };
type DirectoryVersion = { id: string; scope_id: string; created_at: string; available: boolean | number;
  metadata_available: boolean | number; history_status?: string };
type Draft = { title: string; mode: 'original' | 'edited'; body: string; eventSeqs: number[];
  artifactRefs: ArtifactRef[]; fileRefs: FileRef[]; responsibility: string; includeRun: boolean };
type DecisionDraft = { reviewer: string; reviewBody: string; reviewDecision: Review['decision'];
  acceptanceOwner: string; basis: string; acceptanceDecision: Acceptance['decision'] };
type Props = { client: ApiClient; workId: string; revision: number; requirementVersion: number; owner: string;
  artifacts: Artifact[]; output: WorkRunOutput | null; visible: boolean; memory: WorkSessionMemory;
  onClose: () => void; onRevision: (revision: number) => void; onChanged: () => Promise<void>;
  onOpenRequirement: (version: number) => void; onShowArtifact: (id: string, version: number) => void };
const code = (value: unknown) => typeof value === 'string' && /^[A-Z0-9_]{1,64}$/.test(value) ? value : '';
const artifactKey = (ref: ArtifactRef) => `${ref.artifact_id}:${ref.version}`;
const fileKey = (ref: FileRef) => `${ref.scope_id}:${ref.version_id}`;
function readDraft<T>(memory: WorkSessionMemory, key: string, fallback: T): T {
  try { return JSON.parse(memory.drafts.get(key) ?? 'null') ?? fallback; } catch { return fallback; }
}

export default function WorkDelivery({ client, workId, revision, requirementVersion, owner, artifacts, output,
  visible, memory, onClose, onRevision, onChanged, onOpenRequirement, onShowArtifact }: Props) {
  const { locale } = useI18n(); const label = (en: string, cn: string) => locale === 'zh-CN' ? cn : en;
  const draftKey = `${workId}:delivery:${output?.run.id ?? 'work'}`;
  const initialDraft = (): Draft => ({ title: '', mode: 'original', body: '', eventSeqs: [],
    artifactRefs: [], fileRefs: [], responsibility: '', includeRun: false });
  const [draft, setDraft] = useState<Draft>(() => readDraft(memory, draftKey, initialDraft()));
  const [tab, setTab] = useState<'prepare' | 'records'>('prepare');
  const [submissions, setSubmissions] = useState<Submission[]>([]); const [hasMore, setHasMore] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [decision, setDecision] = useState<DecisionDraft>({ reviewer: '', reviewBody: '', reviewDecision: 'CHANGES_REQUESTED',
    acceptanceOwner: '', basis: '', acceptanceDecision: 'REJECTED' });
  const [scopes, setScopes] = useState<ProtectionScope[]>([]); const [scopeIds, setScopeIds] = useState<string[]>([]);
  const [scopeId, setScopeId] = useState(''); const [versions, setVersions] = useState<DirectoryVersion[]>([]);
  const [versionsMore, setVersionsMore] = useState(false); const versionsOffset = useRef(0);
  const [loading, setLoading] = useState(false); const [catalogBusy, setCatalogBusy] = useState(false);
  const [busy, setBusy] = useState(false); const busyRef = useRef(false);
  const [error, setError] = useState(''); const [catalogError, setCatalogError] = useState(''); const [notice, setNotice] = useState('');
  const [savedArtifact, setSavedArtifact] = useState<{ id: string; version: number } | null>(null);
  const [outputEvents, setOutputEvents] = useState<RunEvent[]>(output?.events ?? []);
  const [outputMore, setOutputMore] = useState(false); const [outputLoading, setOutputLoading] = useState(false);
  const outputAfter = useRef(0); const outputSeq = useRef(0);
  const live = useRef(true); const recordsSeq = useRef(0); const versionsSeq = useRef(0); const catalogSeq = useRef(0);
  const revisionRef = useRef(revision); revisionRef.current = Math.max(revisionRef.current, revision);
  const callbacks = useRef({ onClose, onRevision, onChanged }); callbacks.current = { onClose, onRevision, onChanged };
  const decisionKey = `${workId}:delivery:submission:${selectedId ?? ''}`;
  const context = useRef(''); context.current = `${draftKey}:${selectedId ?? ''}`;
  const textEvents = outputEvents.filter(event => event.kind === 'TEXT' && typeof event.payload.text === 'string');
  const selected = submissions.find(item => item.id === selectedId);
  const current = Boolean(selected?.current_requirement && selected.requirement_version === requirementVersion);
  const runReady = Boolean(output && !runIsActive(output.run.status) && output.run.requirement_version === requirementVersion);
  const safeError = (err: unknown) => {
    const reason = err instanceof ApiActionError || err instanceof ApiRequestError ? code(err.reasonCode) : '';
    return `${label('The action was not confirmed. Your draft is preserved.', '操作尚未确认，草稿已保留。')}${reason ? ` ${reason}` : ''}`;
  };
  function updateDraft(values: Partial<Draft>) {
    setDraft(old => { const next = { ...old, ...values }; memory.drafts.set(draftKey, JSON.stringify(next)); return next; });
  }
  function updateDecision(values: Partial<DecisionDraft>) {
    setDecision(old => { const next = { ...old, ...values }; memory.drafts.set(decisionKey, JSON.stringify(next)); return next; });
  }
  useEffect(() => { live.current = true; return () => { live.current = false; recordsSeq.current++; versionsSeq.current++; catalogSeq.current++; outputSeq.current++; }; }, []);
  useEffect(() => {
    setDraft(readDraft(memory, draftKey, initialDraft())); setSavedArtifact(null); setNotice(''); setError(''); setTab('prepare');
  }, [draftKey, memory]);
  useEffect(() => {
    setDecision(readDraft(memory, decisionKey, { reviewer: '', reviewBody: '', reviewDecision: 'CHANGES_REQUESTED',
      acceptanceOwner: '', basis: '', acceptanceDecision: 'REJECTED' }));
  }, [decisionKey, memory]);
  useEffect(() => {
    if (!visible) return;
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); callbacks.current.onClose(); } };
    document.addEventListener('keydown', escape); return () => document.removeEventListener('keydown', escape);
  }, [visible]);

  async function loadOutput() {
    if (!output) return;
    const seq = ++outputSeq.current; setOutputLoading(true);
    try {
      const page = await client.get<ExecutionPage<unknown>>(`${executionBase(workId)}/runs/${encodeURIComponent(output.run.id)}/events?after=${outputAfter.current}&limit=100`);
      const events = page.items.map(normalizeRunEvent);
      if (!live.current || seq !== outputSeq.current) return;
      if (events.length) outputAfter.current = events[events.length - 1].seq;
      setOutputEvents(old => [...old, ...events.filter(item => !old.some(previous => previous.seq === item.seq))].sort((a, b) => a.seq - b.seq));
      setOutputMore(page.has_more);
    } catch (err) { if (live.current && seq === outputSeq.current) setError(safeError(err)); }
    finally { if (live.current && seq === outputSeq.current) setOutputLoading(false); }
  }
  useEffect(() => {
    setOutputEvents(output?.events ?? []); outputAfter.current = 0; setOutputMore(false);
    if (visible && output) void loadOutput();
    return () => { outputSeq.current++; };
  }, [client, draftKey, visible]);

  async function loadSubmissions(append = false, selectId?: string) {
    const seq = ++recordsSeq.current; setLoading(true);
    try {
      const page = await client.get<ExecutionPage<Submission>>(`${executionBase(workId)}/submissions?limit=50&offset=${append ? submissions.length : 0}`);
      if (!live.current || seq !== recordsSeq.current) return;
      setSubmissions(old => append ? [...old, ...page.items.filter(item => !old.some(previous => previous.id === item.id))] : page.items);
      setHasMore(page.has_more);
      if (selectId) setSelectedId(selectId);
    } catch (err) { if (live.current && seq === recordsSeq.current) setError(safeError(err)); }
    finally { if (live.current && seq === recordsSeq.current) setLoading(false); }
  }
  useEffect(() => { if (visible) void loadSubmissions(); return () => { recordsSeq.current++; }; }, [client, workId, visible, requirementVersion]);

  useEffect(() => {
    if (!visible) return;
    const seq = ++catalogSeq.current; setCatalogBusy(true); setCatalogError('');
    void Promise.all([client.get<{ items: ProtectionScope[] }>('/api/v1/protection/scopes'),
      client.get<{ items: { kind: string; object_id: string }[] }>(`${executionBase(workId)}/protection-links`)]).then(([protection, links]) => {
      if (!live.current || seq !== catalogSeq.current) return;
      setScopes(protection.items); setScopeIds([...new Set(links.items.filter(item => item.kind === 'scope').map(item => item.object_id))]);
    }).catch(err => { if (live.current && seq === catalogSeq.current) setCatalogError(safeError(err)); })
      .finally(() => { if (live.current && seq === catalogSeq.current) setCatalogBusy(false); });
    return () => { catalogSeq.current++; };
  }, [client, workId, visible]);
  async function loadVersions(append = false) {
    if (!scopeId) return;
    const seq = ++versionsSeq.current; setCatalogBusy(true); setCatalogError('');
    try {
      const page = await client.get<{ items: DirectoryVersion[]; has_more?: boolean }>(`/api/v1/protection/scopes/${encodeURIComponent(scopeId)}/versions?limit=50&offset=${append ? versionsOffset.current : 0}&view=summary`);
      if (!live.current || seq !== versionsSeq.current) return;
      if (page.items.some(item => item.scope_id !== scopeId)) throw new ApiRequestError('Invalid directory version');
      setVersions(old => append ? [...old, ...page.items.filter(item => !old.some(previous => previous.id === item.id))] : page.items);
      versionsOffset.current = (append ? versionsOffset.current : 0) + page.items.length;
      setVersionsMore(page.has_more ?? page.items.length === 50);
    } catch (err) { if (live.current && seq === versionsSeq.current) setCatalogError(safeError(err)); }
    finally { if (live.current && seq === versionsSeq.current) setCatalogBusy(false); }
  }
  useEffect(() => {
    setVersions([]); setVersionsMore(false); versionsOffset.current = 0;
    if (visible && scopeId) void loadVersions();
    return () => { versionsSeq.current++; };
  }, [client, scopeId, visible]);

  async function perform(key: string, fields: Record<string, unknown>, path: string,
    completed: (receipt: Record<string, unknown>) => void) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(''); setNotice('');
    const signature = JSON.stringify([path, Object.fromEntries(Object.entries(fields).filter(([name]) => name !== 'expected_revision'))]);
    const startedContext = context.current;
    let command = memory.commands.get(key);
    if (!command || command.signature !== signature) {
      command = { signature, payload: { ...fields, request_id: crypto.randomUUID() } }; memory.commands.set(key, command);
    }
    try {
      const receipt = await client.post<Record<string, unknown>>(path, command.payload);
      if (!live.current) return;
      if (typeof receipt.revision === 'number') { revisionRef.current = receipt.revision; callbacks.current.onRevision(receipt.revision); }
      if (startedContext === context.current) { memory.commands.delete(key); completed(receipt); }
      try { await callbacks.current.onChanged(); }
      catch { if (live.current) setError(label('The action was recorded. Reload the work to refresh its contents.', '操作已记录，请刷新工作内容。')); }
    } catch (err) {
      if (err instanceof ApiActionError && err.reasonCode?.endsWith('REVISION_CONFLICT')) memory.commands.delete(key);
      if (live.current) setError(safeError(err));
    } finally { busyRef.current = false; if (live.current) setBusy(false); }
  }
  function saveOutput(event: FormEvent) {
    event.preventDefault();
    if (!output || runIsActive(output.run.status) || !draft.title.trim() || output.run.history_status === 'SUMMARY_ONLY' ||
      draft.mode === 'original' && (!draft.eventSeqs.length || draft.eventSeqs.some(seq => !textEvents.some(item => item.seq === seq))) ||
      draft.mode === 'edited' && (!draft.body.trim() || !textEvents.length)) return;
    const fields = { expected_revision: revisionRef.current, title: draft.title,
      ...(draft.mode === 'original' ? { event_seqs: [...draft.eventSeqs].sort((a, b) => a - b) } : { body: draft.body,
        ...(draft.eventSeqs.length ? { event_seqs: [...draft.eventSeqs].sort((a, b) => a - b) } : {}) }) };
    void perform(`${draftKey}:save-output`, fields,
      `${executionBase(workId)}/sessions/${encodeURIComponent(output.sessionId)}/runs/${encodeURIComponent(output.run.id)}/artifacts`, receipt => {
        if (typeof receipt.artifact_id === 'string' && typeof receipt.version === 'number') setSavedArtifact({ id: receipt.artifact_id, version: receipt.version });
        updateDraft({ title: '', eventSeqs: [], body: '' }); setNotice(label('Artifact saved', '成果已保存'));
      });
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft.responsibility.trim() || !draft.artifactRefs.length && !draft.fileRefs.length || draft.includeRun && !runReady) return;
    if (draft.artifactRefs.some(ref => !artifacts.some(item => item.id === ref.artifact_id && item.version === ref.version && item.requirement_version === requirementVersion))) return;
    void perform(`${draftKey}:submit`, { expected_revision: revisionRef.current, requirement_version: requirementVersion,
      ...(draft.includeRun && output ? { run_id: output.run.id } : {}), artifact_refs: draft.artifactRefs,
      file_refs: draft.fileRefs, responsibility: draft.responsibility }, `${executionBase(workId)}/submissions`, receipt => {
        updateDraft({ artifactRefs: [], fileRefs: [], responsibility: '', includeRun: false }); setTab('records');
        setNotice(label('Delivery submitted', '交付已提交')); void loadSubmissions(false, typeof receipt.submission_id === 'string' ? receipt.submission_id : undefined);
      });
  }
  function review(event: FormEvent) {
    event.preventDefault(); if (!selected || !decision.reviewer.trim() || !decision.reviewBody.trim()) return;
    const target = selected.id;
    void perform(`${workId}:delivery:${target}:review`, { expected_revision: revisionRef.current,
      decision: decision.reviewDecision, reviewer: decision.reviewer, body: decision.reviewBody },
    `${executionBase(workId)}/submissions/${encodeURIComponent(target)}/reviews`, () => {
      updateDecision({ reviewBody: '' }); setNotice(label('Review recorded', '复核已记录')); void loadSubmissions(false, target);
    });
  }
  function accept(event: FormEvent) {
    event.preventDefault(); if (!selected || !current || decision.acceptanceOwner !== owner || !decision.basis.trim()) return;
    const target = selected.id;
    void perform(`${workId}:delivery:${target}:acceptance`, { expected_revision: revisionRef.current,
      decision: decision.acceptanceDecision, owner: decision.acceptanceOwner, basis: decision.basis },
    `${executionBase(workId)}/submissions/${encodeURIComponent(target)}/acceptances`, () => {
      updateDecision({ basis: '' }); setNotice(label('Acceptance decision recorded', '验收决定已记录')); void loadSubmissions(false, target);
    });
  }

  if (!visible) return null;
  const runFile = output?.run.file_result;
  const availableScopes = scopes.filter(item => scopeIds.includes(item.id) || item.id === runFile?.scope_id);
  const outputUnavailable = !output || runIsActive(output.run.status) || output.run.history_status === 'SUMMARY_ONLY';
  const invalidArtifacts = draft.artifactRefs.some(ref => !artifacts.some(item => item.id === ref.artifact_id && item.version === ref.version && item.requirement_version === requirementVersion));
  return <section className="work-delivery" role="region" aria-label={label('Delivery', '交付')}>
    <header className="work-delivery-header"><h2>{label('Delivery', '交付')}</h2>
      <button type="button" title={label('Reload delivery', '刷新交付')} aria-label={label('Reload delivery', '刷新交付')} disabled={loading || busy} onClick={() => { void callbacks.current.onChanged(); void loadSubmissions(); if (output) { outputAfter.current = 0; void loadOutput(); } }}>↻</button>
      <button type="button" autoFocus title={label('Close delivery', '关闭交付')} aria-label={label('Close delivery', '关闭交付')} onClick={onClose}>×</button>
    </header>
    <div className="work-delivery-tabs" role="tablist" aria-label={label('Delivery views', '交付视图')}>
      <button type="button" role="tab" aria-selected={tab === 'prepare'} onClick={() => setTab('prepare')}>{label('Prepare', '准备')}</button>
      <button type="button" role="tab" aria-selected={tab === 'records'} onClick={() => setTab('records')}>{label('Records', '记录')} · {submissions.length}</button>
    </div>
    <div className="work-delivery-content">
      {error && <p role="alert" className="work-delivery-error">{error}</p>}{notice && <p role="status">{notice}</p>}
      <div className="work-delivery-requirement"><button type="button" onClick={() => onOpenRequirement(requirementVersion)}>{label('Requirement', '要求')} v{requirementVersion}</button><span>{label('Owner', '负责人')} · {owner}</span></div>
      {tab === 'prepare' && <>
        {output && <form onSubmit={saveOutput} className="work-delivery-output">
          <fieldset className="work-delivery-fields" disabled={busy}>
          <h3>{label('Save execution output', '保存执行输出')}</h3><code>{output.run.id}</code>
          <button type="button" onClick={() => onOpenRequirement(output.run.requirement_version)}>{label('Requirement', '要求')} v{output.run.requirement_version}</button>
          {outputUnavailable && <p>{label('Execution output unavailable', '执行输出不可用')}</p>}
          {output.omitted > 0 && outputMore && <p>{label('Earlier recorded entries are not fully loaded', '更早的记录尚未全部载入')} · {output.omitted}</p>}
          <label>{label('Artifact title', '成果标题')}<input required maxLength={200} value={draft.title} onChange={event => updateDraft({ title: event.target.value })} /></label>
          <fieldset className="work-delivery-modes"><legend>{label('Output source', '输出来源')}</legend>
            <label><input type="radio" name={`output-source-${workId}`} checked={draft.mode === 'original'} onChange={() => updateDraft({ mode: 'original' })} />{label('Original output', '原始输出')}</label>
            <label><input type="radio" name={`output-source-${workId}`} checked={draft.mode === 'edited'} onChange={() => updateDraft({ mode: 'edited', body: draft.body || textEvents.map(event => event.payload.text).join('') })} />{label('Edited output', '用户编辑输出')}</label>
          </fieldset>
          <div className="work-delivery-events">{textEvents.map(event => <label className="work-delivery-check" key={event.seq}><input type="checkbox" aria-label={`${label('Output event', '输出事件')} ${event.seq}`} disabled={draft.eventSeqs.length >= 1000 && !draft.eventSeqs.includes(event.seq)} checked={draft.eventSeqs.includes(event.seq)} onChange={change => updateDraft({ eventSeqs: change.target.checked ? [...draft.eventSeqs, event.seq] : draft.eventSeqs.filter(seq => seq !== event.seq) })} /><span><small>TEXT · {event.seq}</small><span>{String(event.payload.text)}</span></span></label>)}</div>
          {!textEvents.length && <p>{label('No original text output loaded', '未载入原始文本输出')}</p>}
          {outputMore && <button type="button" disabled={outputLoading} onClick={() => void loadOutput()}>{label('Load more output', '加载更多输出')}</button>}
          {draft.mode === 'edited' && <label>{label('Edited content', '编辑后的内容')}<textarea required rows={7} maxLength={100000} value={draft.body} onChange={event => updateDraft({ body: event.target.value })} /></label>}
          <div className="work-delivery-actions"><button disabled={busy || outputUnavailable || !draft.title.trim() || (draft.mode === 'original' ? !draft.eventSeqs.length : !draft.body.trim() || !textEvents.length)}>{label('Save artifact', '保存成果')}</button>
            {savedArtifact && <button type="button" onClick={() => onShowArtifact(savedArtifact.id, savedArtifact.version)}>{label('Open saved artifact', '打开已保存成果')} · v{savedArtifact.version}</button>}</div>
          </fieldset>
        </form>}
        <form onSubmit={submit} className="work-delivery-submit"><fieldset className="work-delivery-fields" disabled={busy}><h3>{label('Submission', '提交交付')}</h3>
          {output && <label className="work-delivery-check"><input type="checkbox" checked={draft.includeRun} disabled={!runReady && !draft.includeRun} onChange={event => updateDraft({ includeRun: event.target.checked })} /><span>{label('Reference execution', '引用本次执行')} · {output.run.id}</span></label>}
          <h4>{label('Artifact versions', '成果版本')}</h4>
          {!artifacts.length && <p>{label('No saved artifacts', '暂无已保存成果')}</p>}
          <div className="work-delivery-artifacts">{artifacts.map(artifact => {
            const ref = { artifact_id: artifact.id, version: artifact.version }, matches = artifact.requirement_version === requirementVersion;
            const checked = draft.artifactRefs.some(item => artifactKey(item) === artifactKey(ref));
            return <div className="work-delivery-artifact" key={artifactKey(ref)}><label className="work-delivery-check"><input type="checkbox" disabled={!matches && !checked || draft.artifactRefs.length >= 40 && !checked} checked={checked} onChange={event => updateDraft({ artifactRefs: event.target.checked ? [...draft.artifactRefs, ref] : draft.artifactRefs.filter(item => artifactKey(item) !== artifactKey(ref)) })} /><span>{artifact.title} · v{artifact.version}<small>{label('Requirement', '要求')} v{artifact.requirement_version}</small></span></label><button type="button" title={label('Open artifact', '打开成果')} aria-label={`${label('Open artifact', '打开成果')} ${artifact.title} v${artifact.version}`} onClick={() => onShowArtifact(artifact.id, artifact.version)}>↗</button></div>;
          })}</div>
          <h4>{label('Directory versions', '目录版本')}</h4>{catalogError && <p role="alert">{catalogError}</p>}
          {runFile?.status === 'AVAILABLE' && runFile.scope_id && runFile.version_id && <label className="work-delivery-check"><input type="checkbox" disabled={draft.fileRefs.length >= 40 && !draft.fileRefs.some(item => fileKey(item) === fileKey(runFile))} checked={draft.fileRefs.some(item => fileKey(item) === fileKey(runFile))} onChange={event => updateDraft({ fileRefs: event.target.checked ? [...draft.fileRefs, { scope_id: runFile.scope_id, version_id: runFile.version_id }] : draft.fileRefs.filter(item => fileKey(item) !== fileKey(runFile)) })} /><span>{label('Execution directory result', '执行目录结果')} · {runFile.version_id}<small>{label('Attribution unconfirmed', '归因未确认')} · {runFile.scope_id}</small></span></label>}
          <label>{label('Protected directory', '保护目录')}<select value={scopeId} onChange={event => setScopeId(event.target.value)}><option value="">{label('Select directory', '选择目录')}</option>{availableScopes.map(item => <option key={item.id} value={item.id}>{item.root}</option>)}</select></label>
          {versions.map(version => {
            const ref = { scope_id: scopeId, version_id: version.id }, available = Boolean(version.available && version.metadata_available);
            const checked = draft.fileRefs.some(item => fileKey(item) === fileKey(ref));
            return <label className="work-delivery-check" key={version.id}><input type="checkbox" disabled={!available && !checked || draft.fileRefs.length >= 40 && !checked} checked={checked} onChange={event => updateDraft({ fileRefs: event.target.checked ? [...draft.fileRefs, ref] : draft.fileRefs.filter(item => fileKey(item) !== fileKey(ref)) })} /><span>{version.created_at} · {version.id}<small>{code(version.history_status)}</small></span></label>;
          })}
          {versionsMore && <button type="button" disabled={catalogBusy} onClick={() => void loadVersions(true)}>{label('Load more versions', '加载更多版本')}</button>}
          <div className="work-delivery-selected">{draft.fileRefs.map(ref => <div key={fileKey(ref)}><code>{ref.scope_id} · {ref.version_id}</code><button type="button" title={label('Remove directory version', '移除目录版本')} aria-label={`${label('Remove directory version', '移除目录版本')} ${ref.version_id}`} onClick={() => updateDraft({ fileRefs: draft.fileRefs.filter(item => fileKey(item) !== fileKey(ref)) })}>×</button></div>)}</div>
          <label>{label('Responsible person', '交付责任人')}<input required maxLength={120} value={draft.responsibility} onChange={event => updateDraft({ responsibility: event.target.value })} /></label>
          {invalidArtifacts && <p role="alert">{label('Selected artifact versions belong to an earlier requirement', '所选成果版本对应旧要求')}</p>}
          <button disabled={busy || invalidArtifacts || !draft.responsibility.trim() || !draft.artifactRefs.length && !draft.fileRefs.length || draft.includeRun && !runReady}>{label('Submit delivery', '提交交付')}</button>
        </fieldset></form>
      </>}
      {tab === 'records' && <>
        {loading && <p role="status">{label('Loading delivery records', '正在读取交付记录')}</p>}
        {!loading && !submissions.length && <p>{label('No submissions', '暂无交付')}</p>}
        <div className="work-delivery-list">{submissions.map(item => <button type="button" key={item.id} aria-pressed={selectedId === item.id} onClick={() => setSelectedId(item.id)}><strong>{item.responsibility} · {label('Requirement', '要求')} v{item.requirement_version}</strong><small>{item.current_requirement && item.requirement_version === requirementVersion ? label('Current requirement', '当前要求') : label('Historical requirement', '历史要求')} · {item.created_at || item.id}</small></button>)}</div>
        {hasMore && <button type="button" disabled={loading} onClick={() => void loadSubmissions(true)}>{label('Load more submissions', '加载更多交付')}</button>}
        {selected && <section className="work-delivery-record" aria-label={label('Submission detail', '交付详情')}>
          <h3>{label('Submission', '交付')} · {selected.id}</h3><button type="button" onClick={() => onOpenRequirement(selected.requirement_version)}>{label('Requirement', '要求')} v{selected.requirement_version}</button>
          {selected.run_id && <p>{label('Execution', '执行')} · <code>{selected.run_id}</code></p>}
          <h4>{label('Submitted versions', '已提交版本')}</h4>
          {selected.refs.artifacts.map(ref => <button className="work-delivery-reference" type="button" key={artifactKey(ref)} onClick={() => onShowArtifact(ref.artifact_id, ref.version)}>{artifacts.find(item => item.id === ref.artifact_id && item.version === ref.version)?.title || ref.artifact_id} · v{ref.version}</button>)}
          {selected.refs.files.map(ref => <code className="work-delivery-reference" key={fileKey(ref)}>{ref.scope_id} · {ref.version_id}</code>)}
          <h4>{label('Review feedback', '复核反馈')}</h4>
          {!selected.reviews.length && <p>{label('No reviews', '暂无复核')}</p>}
          {selected.reviews.map(item => <div className="work-delivery-decision" key={item.id}><strong>{item.decision === 'APPROVED' ? label('Review approved', '复核通过') : label('Changes requested', '要求修改')} · {item.reviewer}</strong><p>{item.body}</p><small>{item.created_at}</small></div>)}
          <form onSubmit={review}><fieldset className="work-delivery-fields" disabled={busy}><label>{label('Reviewer', '复核人')}<input required maxLength={120} value={decision.reviewer} onChange={event => updateDecision({ reviewer: event.target.value })} /></label><label>{label('Review decision', '复核决定')}<select value={decision.reviewDecision} onChange={event => updateDecision({ reviewDecision: event.target.value as Review['decision'] })}><option value="CHANGES_REQUESTED">{label('Changes requested', '要求修改')}</option><option value="APPROVED">{label('Approved', '通过')}</option></select></label><label>{label('Feedback', '反馈')}<textarea required rows={4} maxLength={65536} value={decision.reviewBody} onChange={event => updateDecision({ reviewBody: event.target.value })} /></label><button disabled={busy || !decision.reviewer.trim() || !decision.reviewBody.trim()}>{label('Record review', '记录复核')}</button></fieldset></form>
          <h4>{label('Owner acceptance', '负责人验收')}</h4>
          {!current && <p>{label('Historical submission. Acceptance is unavailable for the current requirement.', '历史交付无法用于当前要求验收。')}</p>}
          {!selected.acceptances.length && <p>{label('No acceptance decisions', '暂无验收决定')}</p>}
          {selected.acceptances.map((item, index) => <div className="work-delivery-decision" key={item.id}><strong>{!current || index < selected.acceptances.length - 1 ? `${label('Historical', '历史记录')} · ` : ''}{item.decision === 'ACCEPTED' ? label('Accepted', '已验收') : label('Rejected', '已拒绝')} · {item.owner}</strong><p>{item.basis}</p><small>{item.created_at}</small></div>)}
          <form onSubmit={accept}><fieldset className="work-delivery-fields" disabled={busy}><label>{label('Acceptance owner', '验收负责人')}<input required maxLength={120} value={decision.acceptanceOwner} onChange={event => updateDecision({ acceptanceOwner: event.target.value })} /></label><label>{label('Acceptance decision', '验收决定')}<select value={decision.acceptanceDecision} onChange={event => updateDecision({ acceptanceDecision: event.target.value as Acceptance['decision'] })}><option value="REJECTED">{label('Reject', '拒绝')}</option><option value="ACCEPTED">{label('Accept', '验收通过')}</option></select></label><label>{label('Acceptance basis', '验收依据')}<textarea required rows={4} maxLength={65536} value={decision.basis} onChange={event => updateDecision({ basis: event.target.value })} /></label><button disabled={busy || !current || decision.acceptanceOwner !== owner || !decision.basis.trim()}>{label('Record acceptance', '记录验收')}</button></fieldset></form>
        </section>}
      </>}
    </div>
  </section>;
}
