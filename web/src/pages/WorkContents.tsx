import { useId } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { Icon } from './WorkVisuals';
import { workspaceCopy } from './workspaceCopy';

export type WorkContentEntry = { id: string; title: string };
export type WorkArtifactEntry = WorkContentEntry & { version: number; requirement_version: number; created_at?: string };

export default function WorkContents({ tasks, artifacts, expanded, canOpen, onToggle, onOpen, onCreate }: {
  tasks: WorkContentEntry[];
  artifacts: WorkArtifactEntry[];
  expanded: boolean;
  canOpen: boolean;
  onToggle: () => void;
  onOpen: (kind: 'task' | 'artifact', id: string, version: number | null) => void;
  onCreate: (kind: 'task' | 'artifact', opener: HTMLButtonElement) => void;
}) {
  const { locale } = useI18n();
  const t = workspaceCopy[locale];
  const id = useId();
  const count = tasks.length + artifacts.length;
  const createButton = (kind: 'task' | 'artifact', empty: boolean) => {
    const label = kind === 'task' ? t.newTask : t.newArtifact;
    return <button type="button" className={empty ? 'work-quiet work-contents-start' : 'work-icon-button'} title={label} aria-label={label}
      onClick={event => onCreate(kind, event.currentTarget)}>
      <Icon name="plus" size={16} />{empty && label}
    </button>;
  };

  return <aside className="work-contents" aria-label={t.currentContents}>
    <button type="button" className="work-quiet work-contents-toggle" title={t.currentContents} aria-label={t.currentContents} aria-expanded={expanded} aria-controls={id} onClick={onToggle}>
      <Icon name="library" size={16} /><span className="work-contents-title">{t.currentContents}</span>
      {count > 0 && <span className="work-contents-total">{count}</span>}
      <span className="work-contents-chevron" aria-hidden="true" />
    </button>
    {expanded && <div className="work-contents-list" id={id}>
      <section aria-label={t.tasksHeading}>
        <header><h2>{t.tasksHeading}</h2>{tasks.length > 0 && createButton('task', false)}</header>
        {tasks.length ? <ul>{tasks.map(task => <li key={task.id}>
          <button type="button" className="work-quiet work-contents-entry" disabled={!canOpen} title={task.title} data-directory-card={`task:${task.id}:`}
            onClick={() => onOpen('task', task.id, null)}>
            <Icon name="task" size={15} /><span>{task.title}</span>
          </button>
        </li>)}</ul> : createButton('task', true)}
      </section>
      <section aria-label={t.artifactsHeading}>
        <header><h2>{t.artifactsHeading}</h2>{artifacts.length > 0 && createButton('artifact', false)}</header>
        {artifacts.length ? <ul>{artifacts.map(artifact => <li key={`${artifact.id}:${artifact.version}`}>
          <button type="button" className="work-quiet work-contents-entry" disabled={!canOpen} title={artifact.title}
            data-directory-card={`artifact:${artifact.id}:${artifact.version}`}
            onClick={() => onOpen('artifact', artifact.id, artifact.version)}>
            <Icon name="artifact" size={15} /><span>{artifact.title}</span><small className="work-version">v{artifact.version}</small>
          </button>
        </li>)}</ul> : createButton('artifact', true)}
      </section>
    </div>}
  </aside>;
}
