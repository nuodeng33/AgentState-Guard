import { useI18n } from '../i18n/I18nProvider';
import { formatDateTime } from '../i18n/format';
import { Icon } from './WorkVisuals';
import { workspaceCopy } from './workspaceCopy';

export type WorkIndexItem = { id: string; title: string; owner: string; requirement_version: number; updated_at?: string };

export default function WorkOverview({ items, more, onOpen, onCreate, onMore }: {
  items: WorkIndexItem[];
  more: boolean;
  onOpen: (id: string) => void;
  onCreate: (opener: HTMLButtonElement) => void;
  onMore: () => void;
}) {
  const { locale } = useI18n();
  const t = workspaceCopy[locale];

  return <div className="work-overview">
    <header className="work-overview-head"><h1>{t.title}</h1></header>
    {!items.length ? <div className="work-empty">
      <p>{t.empty}</p>
      <button className="work-primary" onClick={event => onCreate(event.currentTarget)}><Icon name="plus" size={16} />{t.create}</button>
    </div> : <ul className="work-index">{items.map(item => <li key={item.id}>
      <a href={`?work=${encodeURIComponent(item.id)}`} onClick={event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        onOpen(item.id);
      }}>
        <Icon name="board" /><strong>{item.title}</strong><span className="work-index-owner">{item.owner}</span>
        <span className="work-version">{t.requirement} v{item.requirement_version}</span>
        {item.updated_at && Number.isFinite(Date.parse(item.updated_at)) && <time dateTime={item.updated_at}>{formatDateTime(item.updated_at, locale)}</time>}
      </a>
    </li>)}</ul>}
    {more && <button className="work-more" onClick={onMore}>{t.more}</button>}
  </div>;
}
