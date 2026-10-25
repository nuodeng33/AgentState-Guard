import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import { I18nProvider } from '../i18n/I18nProvider';
import WorkPage from './WorkPage';
import { LanguageSelector } from '../components/LanguageSelector';

const id = '11111111-1111-4111-8111-111111111111';
afterEach(() => { cleanup(); localStorage.clear(); history.replaceState(null, '', '/'); });

function setup() {
  const post = vi.fn();
  const client = { post, get: vi.fn(async (path: string) => {
    if (path.endsWith('/view')) return { revision: 0, state: null };
    if (path.endsWith('/agents')) return { items: [] };
    if (path.endsWith('/execution-summary')) return { work_id: id, requirement_version: 1, counts: {}, items: [] };
    if (path.includes('/requirements/')) return { body: '用户原文 **unchanged**' };
    return { id, title: '用户工作', requirement: '用户原文 **unchanged**', owner: 'Owner', revision: 1, requirement_version: 1,
      tasks: [{ id: 'one', title: '用户任务' }, { id: 'two', title: 'Second task' }],
      artifacts: [{ id: 'artifact', title: '成果', version: 1, requirement_version: 1 }] };
  }) } as unknown as ApiClient;
  history.replaceState(null, '', `/?work=${id}`);
  const view = render(<I18nProvider systemLanguage="en-US"><LanguageSelector /><WorkPage client={client} /></I18nProvider>);
  return { ...view, post };
}

it('uses English plural counts and switches controls without changing content, view or business state', async () => {
  const { container, post } = setup();
  expect(await screen.findByText('2 tasks')).toBeTruthy();
  expect(screen.getByText('1 artifact')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Continue workbench' }));
  await screen.findByText('unchanged');
  const before = container.querySelector('.work-world')?.getAttribute('style');
  expect(before).toBeTruthy();
  // Drive the same shared preference control now hosted only in Settings.
  fireEvent.click(screen.getByRole('radio', { name: '简体中文' }));
  expect(document.documentElement.lang).toBe('zh-CN');
  expect(localStorage.getItem('asg.ui.language')).toBe('zh-CN');
  expect(screen.getByRole('button', { name: '放大' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '缩小' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '调整要求卡片大小' })).toBeTruthy();
  expect(screen.getByText('unchanged')).toBeTruthy();
  expect(container.querySelector('.work-world')?.getAttribute('style')).toBe(before);
  expect(post).not.toHaveBeenCalled();
});

it('keeps an unsaved form draft when the interface language changes', async () => {
  const { post } = setup();
  fireEvent.click(await screen.findByRole('button', { name: 'More actions' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add task' }));
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: '未保存的任务 draft' } });
  // Drive the same shared preference control now hosted only in Settings.
  fireEvent.click(screen.getByRole('radio', { name: '简体中文' }));
  expect(screen.getByLabelText('标题').getAttribute('value')).toBe('未保存的任务 draft');
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});
