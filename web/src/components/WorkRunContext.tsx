import { useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../api/client';
import { executionBase, readProtectionVersions, type ExecutionContextRef } from '../api/execution';
import { useI18n } from '../i18n/I18nProvider';

type Artifact = { id: string; title: string; version: number };
type Review = { id: string; decision: string; body: string };
type Choice = { label: string; ref: ExecutionContextRef };
export const contextKey = (ref: ExecutionContextRef) => JSON.stringify(ref);

export default function WorkRunContext({ client, workId, artifacts, fileResult, selected, onChange, disabled }: {
  client: ApiClient; workId: string; artifacts: Artifact[]; selected: Choice[]; disabled: boolean;
  fileResult?: { scope_id: string; version_id: string; status: string };
  onChange: (choices: Choice[]) => void;
}) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const [choices, setChoices] = useState<Choice[]>([]); const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(false);
  const [offset, setOffset] = useState(0); const [more, setMore] = useState(false);
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current++; }, [workId]);
  const artifactChoices: Choice[] = artifacts.map(artifact => ({ label: `${artifact.title} · v${artifact.version}`,
    ref: { kind: 'artifact', ref_id: artifact.id, version: artifact.version } }));
  async function reviews(append = false) {
    const seq = ++sequence.current; setBusy(true); setError(false);
    try {
      const page = await client.get<{ items: { reviews: Review[] }[]; has_more: boolean }>(`${executionBase(workId)}/submissions?limit=50&offset=${append ? offset : 0}`);
      const next: Choice[] = page.items.flatMap(submission => submission.reviews.map(review => ({
        label: `${zh ? '复核' : 'Review'} · ${review.decision} · ${review.body.slice(0, 80) || review.id.slice(0, 8)}`,
        ref: { kind: 'review' as const, ref_id: review.id },
      })));
      if (seq !== sequence.current) return;
      setChoices(old => append ? [...old, ...next.filter(item => !old.some(previous => contextKey(previous.ref) === contextKey(item.ref)))] : next);
      setOffset((append ? offset : 0) + page.items.length); setMore(page.has_more);
    } catch { if (seq === sequence.current) setError(true); }
    finally { if (seq === sequence.current) setBusy(false); }
  }
  async function files() {
    if (!fileResult?.scope_id || !fileResult.version_id || fileResult.status !== 'AVAILABLE') return;
    const seq = ++sequence.current; setBusy(true); setError(false);
    try {
      const found = await readProtectionVersions(client, fileResult.scope_id, [fileResult.version_id]);
      const value = found.get(fileResult.version_id)!;
      if (seq !== sequence.current) return;
      const next: Choice[] = value.files.filter(file => file.category === 'restorable').map(file => ({
        label: `${file.relative_path} · ${fileResult.version_id.slice(0, 8)}`,
        ref: { kind: 'file', ref_id: fileResult.scope_id, version_id: fileResult.version_id, file_id: file.file_id },
      }));
      setChoices(old => [...old.filter(choice => choice.ref.kind !== 'file'), ...next]);
    } catch { if (seq === sequence.current) setError(true); }
    finally { if (seq === sequence.current) setBusy(false); }
  }
  const all = [...artifactChoices, ...choices, ...selected].filter((choice, index, list) => list.findIndex(item => contextKey(item.ref) === contextKey(choice.ref)) === index);
  return <details className="work-run-context" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{zh ? '本次发送的上下文' : 'Context to send'} · {selected.length}/32</summary>
    <p>{zh ? '要求和所选任务会自动附带；ASG 只额外发送勾选内容。继续会话仍会使用提供方原有上下文。' : 'The requirement and selected task are included. ASG only attaches checked extra content. Continuing a session also uses its native context.'}</p>
    <button type="button" disabled={busy || disabled} onClick={() => void reviews()}>{zh ? '读取复核反馈' : 'Load review feedback'}</button>
    {more && <button type="button" disabled={busy || disabled} onClick={() => void reviews(true)}>{zh ? '更多复核' : 'More reviews'}</button>}
    {fileResult?.status === 'AVAILABLE' && fileResult.scope_id && <button type="button" disabled={busy || disabled} onClick={() => void files()}>{zh ? '读取所查看运行的文件版本' : 'Load inspected run file version'}</button>}
    {error && <p role="status">{zh ? '上下文读取失败，已选内容保留；可重试。' : 'Context could not be loaded. Selections are retained; retry the load.'}</p>}
    {all.map(choice => { const checked = selected.some(item => contextKey(item.ref) === contextKey(choice.ref)); return <label key={contextKey(choice.ref)}>
      <input type="checkbox" checked={checked} disabled={disabled || !checked && selected.length >= 32}
        onChange={() => onChange(checked ? selected.filter(item => contextKey(item.ref) !== contextKey(choice.ref)) : [...selected, choice])} />{choice.label}
    </label>; })}
    {!all.length && <small>{zh ? '暂无已选额外上下文' : 'No extra context selected'}</small>}
  </details>;
}

export type { Choice as RunContextChoice };
