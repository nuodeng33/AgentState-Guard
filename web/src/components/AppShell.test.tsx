import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
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
