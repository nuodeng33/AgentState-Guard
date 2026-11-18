import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import WorkOverview, { type WorkIndexItem } from './WorkOverview';
import { lastOpenedWork, recordOpenedWork } from './workRecency';

const older = { id: 'older', title: 'April handoff', owner: 'Amy', requirement_version: 1, updated_at: '2026-04-25T13:30:00Z' };
const newer = { id: 'newer', title: 'June release', owner: 'Li', requirement_version: 2, updated_at: '2026-06-14T13:30:00Z' };

function show(items: WorkIndexItem[], { more = false, lang = 'en-US' } = {}) {
  const onOpen = vi.fn();
  const onCreate = vi.fn();
  const onMore = vi.fn();
  render(<I18nProvider systemLanguage={lang}>
    <WorkOverview items={items} more={more} onOpen={onOpen} onCreate={onCreate} onMore={onMore} />
  </I18nProvider>);
  return { onOpen, onCreate, onMore };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

it('labels the API timestamp on first use and keeps the remaining work accessible', () => {
  const { onOpen, onMore } = show([older, newer], { more: true });
  const featured = screen.getByRole('region', { name: newer.title });
  expect(within(featured).getByText('Recently updated')).toBeTruthy();
  expect(screen.queryByText('Last opened')).toBeNull();
  expect(screen.getAllByText(newer.title)).toHaveLength(1);
  const open = within(featured).getByRole('link', { name: `Open work ${newer.title}` });
  expect(within(open).getByText('Open work')).toBeTruthy();
  fireEvent.click(open);
  expect(onOpen).toHaveBeenLastCalledWith(newer.id);
  expect(lastOpenedWork()).toBeNull();
  const index = screen.getByRole('region', { name: 'Work index' });
  const indexedWork = within(index).getByRole('link', { name: /April handoff/ });
  expect(indexedWork.getAttribute('href')).toBe('?work=older');
  fireEvent.click(indexedWork);
  fireEvent.click(within(index).getByRole('button', { name: 'Load more' }));
  expect(onOpen).toHaveBeenLastCalledWith(older.id);
  expect(onMore).toHaveBeenCalledOnce();
});

it('resumes a recorded open only while that work is visible', () => {
  recordOpenedWork(older.id, [older, newer]);
  recordOpenedWork('inaccessible', [older, newer]);
  const first = show([older, newer], { lang: 'zh-CN' });
  const recent = screen.getByRole('region', { name: older.title });
  expect(within(recent).getByText('上次打开')).toBeTruthy();
  const resume = within(recent).getByRole('link', { name: `继续工作 ${older.title}` });
  expect(within(resume).getByText('继续工作')).toBeTruthy();
  fireEvent.click(resume);
  expect(first.onOpen).toHaveBeenCalledWith(older.id);

  cleanup();
  show([newer]);
  expect(screen.queryByText(older.title)).toBeNull();
  expect(screen.queryByText('Last opened')).toBeNull();
  expect(screen.getByText('Recently updated')).toBeTruthy();
});

it('keeps works without usable timestamps in the index when storage is unavailable', () => {
  const blocked = () => { throw new Error('Storage blocked'); };
  vi.spyOn(window, 'localStorage', 'get').mockReturnValue({ getItem: blocked, setItem: blocked } as unknown as Storage);
  recordOpenedWork(older.id, [older]);
  show([{ ...older, updated_at: 'invalid' }]);
  expect(screen.queryByText('Recently updated')).toBeNull();
  expect(screen.queryByText('Last opened')).toBeNull();
  expect(screen.getByRole('link', { name: /April handoff/ })).toBeTruthy();
});

it('offers a single create action with guidance when the index is empty', () => {
  const { onCreate } = show([]);
  expect(screen.getByText('Set a requirement, then add tasks and deliverables.')).toBeTruthy();
  const create = screen.getByRole('button', { name: 'Create work' });
  fireEvent.click(create);
  expect(onCreate).toHaveBeenCalledWith(create);
});
