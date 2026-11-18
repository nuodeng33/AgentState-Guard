import { useId, type MouseEvent } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { formatDateTime } from '../i18n/format';
import { Icon } from './WorkVisuals';
import { workspaceCopy } from './workspaceCopy';
import { lastOpenedWork } from './workRecency';
import './WorkOverview.css';

export type WorkIndexItem = { id: string; title: string; owner: string; requirement_version: number; updated_at?: string };

const homeCopy = {
  'zh-CN': {
    lastOpened: '上次打开', recentlyUpdated: '最近更新', resume: '继续工作', open: '打开工作',
    index: '工作索引', emptyTitle: '开始第一项工作', emptyHint: '写下要完成的要求，再添加任务和成果。',
  },
  'en-US': {
    lastOpened: 'Last opened', recentlyUpdated: 'Recently updated', resume: 'Resume work', open: 'Open work',
    index: 'Work index', emptyTitle: 'Start your first work item', emptyHint: 'Set a requirement, then add tasks and deliverables.',
  },
};

function mostRecentlyUpdated(items: WorkIndexItem[]): WorkIndexItem | undefined {
  let newest: WorkIndexItem | undefined;
  let newestTime = -Infinity;
  for (const item of items) {
    const time = Date.parse(item.updated_at ?? '');
    if (time > newestTime) {
      newest = item;
      newestTime = time;
    }
  }
  return newest;
}

export default function WorkOverview({ items, more, onOpen, onCreate, onMore }: {
  items: WorkIndexItem[];
  more: boolean;
  onOpen: (id: string) => void;
  onCreate: (opener: HTMLButtonElement) => void;
  onMore: () => void;
}) {
  const { locale } = useI18n();
  const t = workspaceCopy[locale];
  const copy = homeCopy[locale];
  const indexId = useId();
  const featuredId = useId();
  const actionId = useId();
  const previousId = lastOpenedWork();
  const previous = items.find(item => item.id === previousId);
  const featured = previous ?? mostRecentlyUpdated(items);
  const index = featured ? items.filter(item => item.id !== featured.id) : items;

  function open(event: MouseEvent<HTMLAnchorElement>, id: string) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onOpen(id);
  }

  function updated(item: WorkIndexItem) {
    return item.updated_at && Number.isFinite(Date.parse(item.updated_at))
      ? <time dateTime={item.updated_at}>{formatDateTime(item.updated_at, locale)}</time>
      : null;
  }

  return <div className="work-overview work-home">
    <div className="work-home-inner">
      <header className="work-home-heading">
        <h1>{t.title}</h1>
      </header>

      {!items.length && <section className="work-home-empty">
        <h2>{copy.emptyTitle}</h2>
        <p>{copy.emptyHint}</p>
        <button className="work-home-create" type="button" onClick={event => onCreate(event.currentTarget)}>
          <Icon name="plus" size={16} />{t.create}
        </button>
      </section>}

      {featured && <section className="work-home-feature" aria-labelledby={featuredId}>
        <div className="work-home-feature-body">
          <p className="work-home-context">{previous ? copy.lastOpened : copy.recentlyUpdated}</p>
          <h2 id={featuredId}>{featured.title}</h2>
          <div className="work-home-meta">
            {featured.owner && <span>{featured.owner}</span>}
            {!previous && updated(featured)}
          </div>
        </div>
        <a className="work-home-action" href={`?work=${encodeURIComponent(featured.id)}`}
          aria-labelledby={`${actionId} ${featuredId}`} onClick={event => open(event, featured.id)}>
          <span id={actionId}>{previous ? copy.resume : copy.open}</span><span aria-hidden="true">→</span>
        </a>
      </section>}

      {(index.length > 0 || more) && <section className="work-home-directory" aria-labelledby={indexId}>
        <h2 id={indexId}>{copy.index}</h2>
        <ul className="work-home-list">{index.map(item => <li key={item.id}>
          <a href={`?work=${encodeURIComponent(item.id)}`} onClick={event => open(event, item.id)}>
            <Icon name="board" size={16} />
            <div className="work-home-row-content">
              <strong>{item.title}</strong>
              {item.owner && <span>{item.owner}</span>}
            </div>
            {updated(item)}
            <span className="work-home-row-arrow" aria-hidden="true">→</span>
          </a>
        </li>)}</ul>
        {more && <button className="work-home-more" type="button" onClick={onMore}>{t.more}</button>}
      </section>}
    </div>
  </div>;
}
