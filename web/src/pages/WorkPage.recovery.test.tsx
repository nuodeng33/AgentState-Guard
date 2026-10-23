import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import { I18nProvider } from '../i18n/I18nProvider';
import WorkPage, { type Board } from './WorkPage';

// Keep panel transport out of these integration tests: the real WorkPage must
// own visibility, keyboard routing, and the lifetime of an unresolved command.
vi.mock('../components/WorkRecovery', async () => {
  const { useEffect, useState } = await import('react');
  return { default: function RecoveryProbe({ visible, onClose }: { visible: boolean; onClose: () => void }) {
    const [draft, setDraft] = useState('');
    useEffect(() => {
      if (!visible) return;
      const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); onClose(); } };
      document.addEventListener('keydown', escape);
      return () => document.removeEventListener('keydown', escape);
    }, [visible, onClose]);
    return <section hidden={!visible} role="dialog" aria-label="Recovery panel probe">
      <label>Pending recovery command<input value={draft} onChange={event => setDraft(event.target.value)} /></label>
      <button onClick={onClose}>Close recovery probe</button>
    </section>;
  } };
});
vi.mock('../components/WorkSessions', () => ({
  createWorkSessionMemory: () => ({}),
  default: ({ visible, onClose }: { visible: boolean; onClose: () => void }) => <section hidden={!visible} role="dialog" aria-label="Session panel probe"><button onClick={onClose}>Close session probe</button></section>,
}));
vi.mock('../components/WorkDelivery', () => ({
  createDeliveryMemory: () => ({}),
  default: ({ visible, onClose }: { visible: boolean; onClose: () => void }) => <section hidden={!visible} role="dialog" aria-label="Delivery panel probe"><button onClick={onClose}>Close delivery probe</button></section>,
}));

const workId = '11111111-1111-4111-8111-111111111111';
const work = { id: workId, title: 'Existing work', owner: 'Owner', revision: 1, requirement_version: 1,
  requirement: 'Retained requirement', tasks: [], artifacts: [] };
const board: Board = { schema_version: 1, surface: 'board', viewport: { x: 31, y: 43, zoom: .8 },
  space: { viewport: { x: 0, y: 0, zoom: 1 }, nodes: [{ kind: 'requirement', ref_id: workId, version: 1, x: 0, y: 0 }] },
  cards: [{ kind: 'requirement', ref_id: workId, version: 1, x: 0, y: 0, width: 400, height: 430, scroll: 0 }] };
function fixture() {
  const get = vi.fn(async (path: string) => {
    if (path.endsWith('/view')) return { revision: 1, state: board };
    if (path.endsWith('/execution-summary')) return { work_id: workId, requirement_version: 1, counts: {}, items: [] };
    if (path.endsWith('/agents')) return { items: [] };
    if (path.includes('/requirements/')) return { body: work.requirement };
    if (path.includes('?limit=')) return { items: [work] };
    return work;
  });
  const post = vi.fn().mockResolvedValue({ revision: 2 });
  history.replaceState(null, '', '/?work=' + workId);
  return { client: { get, post } as unknown as ApiClient, get, post };
}
function openMenu(name = 'More actions') { fireEvent.click(screen.getByRole('button', { name })); }
function openRecovery() { openMenu(); fireEvent.click(screen.getByRole('button', { name: 'File recovery' })); }
afterEach(() => { cleanup(); history.replaceState(null, '', '/'); localStorage.clear(); });

describe('global file recovery entry from a Work', () => {
  it.each([['en-US', 'More actions', 'File recovery'], ['zh-CN', '更多操作', '文件恢复']] as const)(
    'offers a global recovery entry in %s without changing the Work', async (language, actions, entry) => {
      localStorage.setItem('asg.ui.language', language);
      const { client, post } = fixture();
      const { container } = render(<I18nProvider><WorkPage client={client} /></I18nProvider>);
      await screen.findByText(work.requirement);
      const before = container.querySelector('.work-world')?.getAttribute('style');
      openMenu(actions);
      fireEvent.click(screen.getByRole('button', { name: entry }));
      expect(screen.getByRole('dialog', { name: 'Recovery panel probe' })).toBeTruthy();
      expect(screen.queryByRole('button', { name: entry })).toBeNull();
      expect(location.search).toContain('work=' + workId);
      expect(container.querySelector('.work-world')?.getAttribute('style')).toBe(before);
      expect(post).not.toHaveBeenCalled();
    });

  it('keeps an unresolved recovery command while hidden and mutually excludes other panels', async () => {
    const { client } = fixture();
    const { rerender } = render(<WorkPage client={client} />);
    await screen.findByText(work.requirement);
    openRecovery();
    fireEvent.change(screen.getByLabelText('Pending recovery command'), { target: { value: 'same-request-id' } });
    await act(async () => { rerender(<WorkPage client={client} visible={false} />); });
    expect(screen.queryByRole('dialog', { name: 'Recovery panel probe' })).toBeNull();
    await act(async () => { rerender(<WorkPage client={client} visible />); });
    expect((screen.getByLabelText('Pending recovery command') as HTMLInputElement).value).toBe('same-request-id');
    openMenu(); fireEvent.click(screen.getByRole('button', { name: 'Session management' }));
    expect(screen.getByRole('dialog', { name: 'Session panel probe' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Recovery panel probe' })).toBeNull();
    openRecovery();
    expect(screen.queryByRole('dialog', { name: 'Session panel probe' })).toBeNull();
    expect((screen.getByLabelText('Pending recovery command') as HTMLInputElement).value).toBe('same-request-id');
    openMenu(); fireEvent.click(screen.getByRole('button', { name: 'Delivery' }));
    expect(screen.getByRole('dialog', { name: 'Delivery panel probe' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Recovery panel probe' })).toBeNull();
    openRecovery();
    expect(screen.queryByRole('dialog', { name: 'Delivery panel probe' })).toBeNull();
  });

  it('returns focus on Escape without letting the canvas navigate or lose its position', async () => {
    const { client } = fixture();
    const { container } = render(<WorkPage client={client} />);
    await screen.findByText(work.requirement);
    const before = container.querySelector('.work-world')?.getAttribute('style');
    openRecovery();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'More actions' })));
    expect(screen.queryByRole('dialog', { name: 'Recovery panel probe' })).toBeNull();
    expect(container.querySelector('.work-world')?.getAttribute('style')).toBe(before);
    expect(location.search).toContain('work=' + workId);
    openRecovery();
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Recovery panel probe' })).getByRole('button', { name: 'Close recovery probe' }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'More actions' })));
  });
});
