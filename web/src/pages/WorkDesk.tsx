import { useId, useRef, useState, type ReactNode, type RefObject } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { WorkArtifactEntry, WorkContentEntry } from './workContentTypes';
import { Icon } from './WorkVisuals';
import { workspaceCopy } from './workspaceCopy';
import './workDesk.css';

type DeskItem = { kind: 'requirement' | 'task' | 'artifact'; ref_id: string; version: number | null };
type Contents = { open: boolean; allTasks: boolean; allArtifacts: boolean };
type Props = {
  work: {
    id: string; title: string; owner: string; updated_at?: string;
    requirement: string; requirement_version: number;
    tasks: WorkContentEntry[]; artifacts: WorkArtifactEntry[];
  };
  canOpen: boolean;
  contents: Contents;
  onContents: (next: Contents) => void;
  continueButton: RefObject<HTMLButtonElement>;
  onContinue: () => void;
  onOpen: (item: DeskItem, opener: string) => void;
  previewArtifact: (artifact: WorkArtifactEntry) => ReactNode;
  onCreate: (kind: 'task' | 'artifact', opener: HTMLButtonElement) => void;
};

function summaryLines(requirement: string) {
  const lines: string[] = [];
  let fenced = false;
  for (const raw of requirement.split(/\r?\n/)) {
    const line = raw.trim();
    if (/^(\x60{3}|~{3})/.test(line)) { fenced = !fenced; continue; }
    if (fenced || !line || /^([#|]|[-*_]{3,}$)/.test(line)) continue;
    const plain = line.replace(/^(?:[-*+>]\s+|\d+\.\s+)/, '').replace(/\*\*|[\x60]/g, '').trim();
    if (plain) lines.push(plain.slice(0, 280) + (plain.length > 280 ? '…' : ''));
    if (lines.length === 2) break;
  }
  if (!lines.length) {
    const heading = requirement.split(/\r?\n/).find(line => /^#{1,6}\s+\S/.test(line.trim()));
    if (heading) lines.push(heading.trim().replace(/^#{1,6}\s+/, '').slice(0, 280));
  }
  return lines;
}

export default function WorkDesk({ work, canOpen, contents, onContents, continueButton, onContinue, onOpen, onCreate, previewArtifact }: Props) {
  const { locale } = useI18n();
  const t = workspaceCopy[locale];
  const zh = locale === 'zh-CN';
  const contentsId = useId();
  const previewId = useId();
  const [preview, setPreview] = useState<string | null>(null);
  const previewTrigger = useRef<HTMLButtonElement | null>(null);
  function togglePreview(id: string, opener: HTMLButtonElement) {
    previewTrigger.current = opener;
    setPreview(current => current === id ? null : id);
  }
  const requirement = (version: number): DeskItem => ({ kind: 'requirement', ref_id: work.id, version });
  const updatedAt = work.updated_at && Number.isFinite(Date.parse(work.updated_at)) ? new Date(work.updated_at) : null;
  const excerpt = summaryLines(work.requirement ?? '');
  const artifacts = [...work.artifacts].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '') || b.version - a.version);
  const create = (kind: 'artifact' | 'task') => <button className="desk-add" type="button" onClick={event => onCreate(kind, event.currentTarget)}>
    <Icon name="plus" size={14} />{kind === 'artifact' ? t.newArtifact : t.newTask}
  </button>;

  return <div className="work-desk">
    <header className="desk-heading">
      <div>
        <h1>{work.title}</h1>
        <div className="desk-meta">
          {work.owner && <span>{work.owner}</span>}
          {updatedAt && <time dateTime={work.updated_at}>
            {t.updated} {updatedAt.toLocaleDateString(locale, { month: 'short', day: 'numeric', year: 'numeric' })}
          </time>}
        </div>
      </div>
      <button type="button" className="desk-continue" ref={continueButton} onClick={onContinue} disabled={!canOpen}>
        <Icon name="board" size={16} />{t.continueBoard}<span className="desk-arrow" aria-hidden="true">↗</span>
      </button>
    </header>

    <div className="desk-layout">
      <section className="desk-requirement" aria-label={t.current}>
        <button type="button" className="desk-sheet" data-desk-card="current" disabled={!canOpen}
          aria-label={t.open + ' ' + work.title} onClick={() => onOpen(requirement(work.requirement_version), 'current')}>
          <span className="desk-sheet-label"><Icon name="requirement" size={17} />{t.current}<span className="desk-version">v{work.requirement_version}</span></span>
          <span className="desk-excerpt">
            {excerpt.length ? excerpt.map((line, index) => <span key={index}>{line}</span>) : <span>{work.requirement ? (zh ? '在工作台查看完整要求' : 'Read the full requirement in the workbench') : (zh ? '这份要求暂无正文' : 'This requirement has no text')}</span>}
          </span>
          <span className="desk-open-note">{zh ? '打开要求正文' : 'Read requirement'}<span className="desk-arrow" aria-hidden="true">↗</span></span>
        </button>
      </section>

      <aside className="desk-contents" aria-label={t.currentContents}>
        <div className="desk-contents-head">
          <button type="button" className="desk-contents-toggle" aria-label={t.currentContents} aria-expanded={contents.open}
            aria-controls={contentsId} onClick={() => onContents({ ...contents, open: !contents.open })}>
            {t.currentContents}<span aria-hidden="true" className="desk-chevron" />
          </button>
        </div>
        <div id={contentsId} className="desk-activity" hidden={!contents.open}>
          <section className="desk-outcomes" aria-label={t.artifactsHeading}>
            <header className="desk-section-head">
              <h2>{artifacts.length > 0 && !contents.allArtifacts ? (zh ? '最近成果' : 'Recent artifacts') : t.artifactsHeading}</h2>
              {artifacts.length > 0 && create('artifact')}
            </header>
            {artifacts.length ? <ul className="desk-folios">
              {artifacts.slice(0, contents.allArtifacts ? undefined : 2).map(artifact => {
                const id = 'artifact:' + artifact.id + ':' + artifact.version;
                return <li className="desk-folio" key={id} onKeyDown={event => {
                  if (event.key === 'Escape' && preview === id) {
                    event.preventDefault();
                    event.stopPropagation();
                    setPreview(null);
                    previewTrigger.current?.focus();
                  }
                }}>
                  <button type="button" className="desk-open-artifact" data-desk-card={id} disabled={!canOpen} title={artifact.title}
                    onKeyDown={event => {
                      if (event.key === ' ') {
                        event.preventDefault();
                        if (!event.repeat) togglePreview(id, event.currentTarget);
                      }
                    }}
                    onClick={() => onOpen({ kind: 'artifact', ref_id: artifact.id, version: artifact.version }, id)}>
                    <span className="desk-file"><Icon name="artifact" size={17} /></span>
                    <span className="desk-row-title">{artifact.title}</span>
                    <span className="desk-version">v{artifact.version}</span>
                    <span className="desk-arrow" aria-hidden="true">↗</span>
                  </button>
                  <div className="desk-folio-actions"><button type="button" className="desk-peek-action"
                    aria-label={(zh ? '速览 ' : 'Peek at ') + artifact.title} aria-expanded={preview === id}
                    aria-controls={preview === id ? previewId : undefined} onClick={event => togglePreview(id, event.currentTarget)}>
                    {preview === id ? (zh ? '收起速览' : 'Close preview') : (zh ? '速览' : 'Peek')}
                  </button>
                  <button type="button" className="desk-origin" data-current={artifact.requirement_version === work.requirement_version}
                    data-desk-card={'origin:' + id} disabled={!canOpen}
                    onClick={() => onOpen(requirement(artifact.requirement_version), 'origin:' + id)}>
                    <Icon name="link" size={13} />{zh ? '源自要求' : 'From requirement'} v{artifact.requirement_version}
                  </button></div>
                  {preview === id && <div className="desk-peek" id={previewId} role="region" aria-label={artifact.title}>{previewArtifact(artifact)}</div>}
                </li>;
              })}
            </ul> : <div className="desk-empty-line"><span>{t.none}</span>{create('artifact')}</div>}
            {artifacts.length > 2 && <button type="button" className="desk-show-all" aria-expanded={contents.allArtifacts}
              onClick={() => onContents({ ...contents, allArtifacts: !contents.allArtifacts })}>
              {contents.allArtifacts ? (zh ? '仅看最近成果' : 'Show recent artifacts') : (zh ? '查看全部成果' : 'All artifacts')} <span>{artifacts.length}</span>
            </button>}
          </section>

          <section className="desk-tasks" aria-label={t.tasksHeading}>
            <header className="desk-section-head">
              <h2>{t.tasksHeading}{work.tasks.length > 0 && <span>{work.tasks.length}</span>}</h2>
              {work.tasks.length > 0 && create('task')}
            </header>
            {work.tasks.length ? <ul className="desk-task-list">
              {work.tasks.slice(0, contents.allTasks ? undefined : 3).map(task => {
                const id = 'task:' + task.id;
                return <li key={task.id}><button type="button" disabled={!canOpen} title={task.title} data-desk-card={id}
                  onClick={() => onOpen({ kind: 'task', ref_id: task.id, version: null }, id)}>
                  <Icon name="task" size={16} /><span className="desk-row-title">{task.title}</span><span className="desk-arrow" aria-hidden="true">↗</span>
                </button></li>;
              })}
            </ul> : <button type="button" className="desk-next-button" aria-label={t.newTask} onClick={event => onCreate('task', event.currentTarget)}>
              <span className="desk-next-icon"><Icon name="plus" size={16} /></span>
              <span>{zh ? '把要求拆成第一项任务' : 'Create the first task from this requirement'}</span><span className="desk-arrow" aria-hidden="true">↗</span>
            </button>}
            {work.tasks.length > 3 && <button type="button" className="desk-show-all" aria-expanded={contents.allTasks}
              onClick={() => onContents({ ...contents, allTasks: !contents.allTasks })}>
              {contents.allTasks ? (zh ? '收起其余任务' : 'Show fewer tasks') : (zh ? '查看全部任务' : 'All tasks')} <span>{work.tasks.length}</span>
            </button>}
          </section>
        </div>
      </aside>
    </div>
  </div>;
}
