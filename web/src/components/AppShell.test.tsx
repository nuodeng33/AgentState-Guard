import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '../api/client';
import { AppShell, WorkSidebar } from './AppShell';

describe('work sidebar and account settings', () => {
  it('keeps auxiliary pages behind avatar then settings', () => {
    const navigate = vi.fn(); render(<WorkSidebar page="work" onNavigate={navigate} onCreate={() => {}} />);
    const primary = screen.getByRole('navigation');
    expect(within(primary).getAllByRole('button')).toHaveLength(1);
    expect(within(primary).getByRole('button', { name: 'My work' })).toBeTruthy();
    for (const label of ['Guard','Environment','Changes','Supervision','Recovery','Devices','Settings'])
      expect(screen.queryByRole('button', { name: label })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Workspace account' }));
    expect(screen.queryByRole('button', { name: 'Environment' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(navigate).toHaveBeenCalledWith('settings');
    expect(screen.queryByRole('button', { name: 'Settings' })).toBeNull();
  });
  it('searches beyond the loaded index and ignores late responses from earlier queries', async () => {
    let finishFirst!: (page: { items: { id: string; title: string }[] }) => void;
    const get = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve; }))
      .mockResolvedValueOnce({ items: [{ id: 'outside', title: 'Autumn 演出' }] });
    const open = vi.fn();
    render(<WorkSidebar client={{ get } as unknown as ApiClient} page="work" onNavigate={() => {}} onOpen={open}
      items={[{ id: 'local', title: 'Loaded work' }]} />);
    const search = screen.getByRole('searchbox');
    fireEvent.change(search, { target: { value: 'winter' } });
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    fireEvent.change(search, { target: { value: '演出' } });
    const found = await screen.findByRole('button', { name: 'Autumn 演出' });
    expect(new URL(get.mock.calls[1][0], 'http://localhost').searchParams.get('q')).toBe('演出');
    await act(async () => { finishFirst({ items: [{ id: 'stale', title: 'Winter work' }] }); });
    expect(screen.queryByRole('button', { name: 'Winter work' })).toBeNull();
    fireEvent.click(found);
    expect(open).toHaveBeenCalledWith('outside');
    fireEvent.keyDown(search, { key: 'Escape' });
    expect(screen.getByRole('button', { name: 'Loaded work' })).toBeTruthy();
  });
  it('dismisses the avatar menu with Escape and outside input', () => {
    render(<WorkSidebar page="work" onNavigate={() => {}} />);
    const trigger = screen.getByRole('button', { name: 'Workspace account' });
    fireEvent.click(trigger); fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.activeElement).toBe(trigger);
    fireEvent.click(trigger); fireEvent.pointerDown(document.body);
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
  });
  it('keeps every existing auxiliary route available inside settings', () => {
    const navigate=vi.fn();render(<AppShell page="settings" title="Settings" onNavigate={navigate}>Preferences</AppShell>);
    const sections=screen.getByRole('navigation',{name:'Settings sections'});
    for(const label of ['Guard','Environment','Changes','Supervision','Recovery','Devices','Settings'])
      fireEvent.click(within(sections).getByRole('button',{name:label}));
    expect(navigate.mock.calls.map(call=>call[0])).toEqual(['home','environment','changes','supervision','recovery','devices','settings']);
  });
  it('locks switching during a pending action', () => {
    const navigate=vi.fn();render(<WorkSidebar page="work" navigationLocked onNavigate={navigate} onCreate={()=>{}} />);
    expect((screen.getByRole('button',{name:'New work'}) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button',{name:'Workspace account'}));
    expect((screen.getByRole('button',{name:'Settings'}) as HTMLButtonElement).disabled).toBe(true);
  });
});
