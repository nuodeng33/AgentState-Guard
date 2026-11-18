import { useId, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { WorkArtifactEntry, WorkContentEntry } from './workContentTypes';
import { Icon } from './WorkVisuals';
import { workspaceCopy } from './workspaceCopy';
import { workExcerpt } from './workExcerpt';
import './workDesk.css';

type DeskItem = { kind: 'requirement' | 'task' | 'artifact'; ref_id: string; version: number | null };
type Contents = { open: boolean; allTasks: boolean; allArtifacts: boolean };
type Props = {
  work: {
    id: string;
    title: string;
    owner: string;
    updated_at?: string;
    requirement: string;
    requirement_version: number;
    tasks: WorkContentEntry[];
    artifacts: WorkArtifactEntry[];
  };
  canOpen: boolean;
  contents: Contents;
  onContents: (next: Contents) => void;
  continueButton: RefObject<HTMLButtonElement>;
  onContinue: () => void;
  onOpen: (item: DeskItem, opener: string) => void;
  previewArtifact: (artifact: WorkArtifactEntry, mode?: 'excerpt') => ReactNode;
  onCreate: (kind: 'task' | 'artifact', opener: HTMLButtonElement) => void;
};

export default function WorkDesk({ work, canOpen, contents, onContents, continueButton, onContinue, onOpen, onCreate, previewArtifact }: Props) {
  const { locale } = useI18n();
  const t = workspaceCopy[locale];
  const zh = locale === 'zh-CN';
  const contentsId = useId();
  const recentId = useId();
  const previewId = useId();
  const [preview, setPreview] = useState<string | null>(null);
  const previewTrigger = useRef<HTMLButtonElement | null>(null);
  const requirement = (version: number): DeskItem => ({ kind: 'requirement', ref_id: work.id, version });
  const updatedAt = work.updated_at && Number.isFinite(Date.parse(work.updated_at)) ? new Date(work.updated_at) : null;
  const excerpt = workExcerpt(work.requirement ?? '');
  const savedTime = (value: WorkArtifactEntry) => {
    const time = Date.parse(value.created_at ?? '');
    return Number.isFinite(time) ? time : -Infinity;
  };
  const artifacts = [...work.artifacts].sort((a, b) => savedTime(b) - savedTime(a) || b.version - a.version);
  const latest = artifacts[0];

  function togglePreview(id: string, opener: HTMLButtonElement) {
    previewTrigger.current = opener;
    setPreview(current => current === id ? null : id);
  }

  function closePreviewOnEscape(event: KeyboardEvent<HTMLElement>, id: string) {
    if (event.key !== 'Escape' || preview !== id) return;
    event.preventDefault();
    event.stopPropagation();
    setPreview(null);
    previewTrigger.current?.focus();
  }

  function setContentsOpen(open: boolean) {
    if (!open) setPreview(null);
    onContents({ ...contents, open });
  }

  const create = (kind: 'artifact' | 'task') => <button className="desk-add" type="button" onClick={event => onCreate(kind, event.currentTarget)}>
    <Icon name="plus" size={14} />{kind === 'artifact' ? t.newArtifact : t.newTask}
  </button>;

  function renderArtifact(artifact: WorkArtifactEntry, featured = false) {
    const id = 'artifact:' + artifact.id + ':' + artifact.version;
    const inPreview = preview === id;
    const isCurrent = artifact.requirement_version === work.requirement_version;
    const Heading = featured ? 'h3' : 'div';

    return <>
      <Heading className="desk-artifact-heading">
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
          <span className="desk-arrow"><Icon name="back" size={14} /></span>
        </button>
      </Heading>
      <div className="desk-folio-actions">
        <button type="button" className="desk-origin" data-current={isCurrent}
          data-desk-card={'origin:' + id} disabled={!canOpen}
          onClick={() => onOpen(requirement(artifact.requirement_version), 'origin:' + id)}>
          <Icon name="link" size={13} />{zh ? '源自要求' : 'From requirement'} v{artifact.requirement_version}
        </button>
        <button type="button" className="desk-peek-action"
          aria-label={(zh ? '速览 ' : 'Peek at ') + artifact.title} aria-expanded={inPreview}
          aria-controls={inPreview ? previewId : undefined} onClick={event => togglePreview(id, event.currentTarget)}>
          {inPreview ? (zh ? '收起速览' : 'Close preview') : (zh ? '速览' : 'Peek')}
        </button>
      </div>
      {inPreview ? <div className="desk-peek" id={previewId} role="region" aria-label={artifact.title}>
        {previewArtifact(artifact)}
      </div> : featured && <div className="desk-artifact-excerpt">
        {previewArtifact(artifact, 'excerpt')}
      </div>}
    </>;
  }

  return <div className="work-desk">
    <header className="desk-heading">
      <div className="desk-heading-main">
        <div className="desk-identity">
          <h1>{work.title}</h1>
          <div className="desk-meta">
            {work.owner && <span>{work.owner}</span>}
            {updatedAt && <time dateTime={work.updated_at}>
              {t.updated} {updatedAt.toLocaleDateString(locale, { month: 'short', day: 'numeric', year: 'numeric' })}
            </time>}
          </div>
        </div>
      </div>
      <button type="button" className="desk-continue" ref={continueButton} onClick={onContinue} disabled={!canOpen}>
        <Icon name="board" size={16} />{t.continueBoard}
      </button>
    </header>

    <aside className="desk-contents" aria-label={t.currentContents}>
      <div className="desk-layout" data-has-recent={Boolean(latest && contents.open)}>
        <section className="desk-requirement" aria-label={t.current}>
          <header className="desk-document-heading">
            <h2>{t.current}</h2>
          </header>
          <button type="button" className="desk-sheet" data-desk-card="current" disabled={!canOpen}
            aria-label={t.open + ' ' + work.title} onClick={() => onOpen(requirement(work.requirement_version), 'current')}>
            <span className="desk-sheet-label"><Icon name="requirement" size={17} /><span className="desk-version">v{work.requirement_version}</span></span>
            <span className="desk-excerpt">
              {excerpt.length ? excerpt.map((line, index) => <span key={index}>{line}</span>) : <span>{work.requirement ? (zh ? '在工作台查看完整要求' : 'Read the full requirement in the workbench') : (zh ? '这份要求暂无正文' : 'This requirement has no text')}</span>}
            </span>
            <span className="desk-open-note">{zh ? '打开要求正文' : 'Read requirement'}<span className="desk-arrow"><Icon name="back" size={15} /></span></span>
          </button>
        </section>

        {latest && <section className="desk-outcomes" id={recentId} aria-label={t.artifactsHeading} hidden={!contents.open}
          onKeyDown={event => closePreviewOnEscape(event, 'artifact:' + latest.id + ':' + latest.version)}>
          <header className="desk-document-heading">
            <h2>{zh ? '最近成果' : 'Recent artifact'}</h2>
            {create('artifact')}
          </header>
          {contents.open && renderArtifact(latest, true)}
        </section>}
      </div>

      <div className="desk-secondary">
        <div className="desk-contents-head">
          <button type="button" className="desk-contents-toggle" aria-label={t.currentContents} aria-expanded={contents.open}
            aria-controls={latest ? contentsId + ' ' + recentId : contentsId} onClick={() => setContentsOpen(!contents.open)}>
            {zh ? '工作内容' : 'Work contents'}<span aria-hidden="true" className="desk-chevron" />
          </button>
        </div>
        <div id={contentsId} className="desk-activity" hidden={!contents.open}>
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
                  <span className="desk-task-icon"><Icon name="task" size={16} /></span><span className="desk-row-title">{task.title}</span><span className="desk-arrow"><Icon name="back" size={14} /></span>
                </button></li>;
              })}
            </ul> : <button type="button" className="desk-next-button" aria-label={t.newTask} onClick={event => onCreate('task', event.currentTarget)}>
              <span className="desk-next-icon"><Icon name="plus" size={16} /></span>
              <span>{zh ? '添加第一项任务' : 'Add the first task'}</span><span className="desk-arrow"><Icon name="back" size={14} /></span>
            </button>}
            {work.tasks.length > 3 && <button type="button" className="desk-show-all" aria-expanded={contents.allTasks}
              onClick={() => onContents({ ...contents, allTasks: !contents.allTasks })}>
              {contents.allTasks ? (zh ? '收起其余任务' : 'Show fewer tasks') : (zh ? '查看全部任务' : 'All tasks')} <span>{work.tasks.length}</span>
            </button>}
          </section>

          {artifacts.length > 1 && <section className="desk-archive" aria-label={zh ? '其他成果' : 'Other artifacts'}>
            <header className="desk-section-head"><h2>{zh ? '其他成果' : 'Other artifacts'}</h2></header>
            <ul className="desk-folios">
              {artifacts.slice(1, contents.allArtifacts ? undefined : 2).map(artifact => {
                const id = 'artifact:' + artifact.id + ':' + artifact.version;
                return <li className="desk-folio" key={id} onKeyDown={event => closePreviewOnEscape(event, id)}>
                  {renderArtifact(artifact)}
                </li>;
              })}
            </ul>
            {artifacts.length > 2 && <button type="button" className="desk-show-all" aria-expanded={contents.allArtifacts}
              onClick={() => onContents({ ...contents, allArtifacts: !contents.allArtifacts })}>
              {contents.allArtifacts ? (zh ? '仅看最近成果' : 'Show recent artifacts') : (zh ? '查看全部成果' : 'All artifacts')} <span>{artifacts.length}</span>
            </button>}
          </section>}
          {!latest && <section className="desk-empty-line" aria-label={t.artifactsHeading}><span>{t.none}</span>{create('artifact')}</section>}
        </div>
      </div>
    </aside>
  </div>;
}
