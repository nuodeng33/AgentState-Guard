import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '../i18n/I18nProvider';
import { WorkMenuBar, type WorkMenuBarProps } from './WorkMenuBar';

afterEach(() => { cleanup(); localStorage.clear(); });

function setup(props: Partial<WorkMenuBarProps> = {}, language = 'en-US') {
  const onNewWork = vi.fn();
  const view = render(<I18nProvider systemLanguage={language}>
    <WorkMenuBar onNewWork={onNewWork} {...props} />
  </I18nProvider>);
  return { ...view, onNewWork };
}

describe('WorkMenuBar', () => {
  it('shows localized disclosures and only real actions, with unavailable actions disabled', () => {
    setup({}, 'zh-CN');
    expect(screen.getByRole('navigation', { name: '工作菜单' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '文件' }));
    const group = screen.getByRole('group', { name: '文件' });
    expect(within(group).getByRole('button', { name: '创建工作' }).hasAttribute('disabled')).toBe(false);
    expect(within(group).getByRole('button', { name: '保存现场' }).hasAttribute('disabled')).toBe(true);
    expect(within(group).getByRole('button', { name: '所有工作' }).hasAttribute('disabled')).toBe(true);
    expect(screen.queryByRole('menubar')).toBeNull();
    expect(screen.queryByText(/Guard|Undo|Copy/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    expect(screen.queryByRole('group', { name: '文件' })).toBeNull();
    expect(screen.getByRole('button', { name: '添加任务' }).hasAttribute('disabled')).toBe(true);
  });

  it('dispatches every enabled action once, closes and preserves callback focus', () => {
    const names = ['onSave', 'onAllWork', 'onNewTask', 'onNewArtifact', 'onBoard', 'onSpace', 'on3d'] as const;
    const callbacks = Object.fromEntries(names.map(name => [name, vi.fn()]));
    const { rerender } = setup(callbacks);
    const pairs = [
      ['File', 'Save workspace', 'onSave'], ['File', 'All work', 'onAllWork'],
      ['Edit', 'Add task', 'onNewTask'], ['Edit', 'Save artifact', 'onNewArtifact'],
      ['View', 'Workbench', 'onBoard'], ['View', 'Relationship space', 'onSpace'],
      ['View', '3D view', 'on3d'],
    ];
    for (const [menu, action, callback] of pairs) {
      fireEvent.click(screen.getByRole('button', { name: menu }));
      fireEvent.click(screen.getByRole('button', { name: action }));
      expect(callbacks[callback]).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole('group')).toBeNull();
      expect(document.activeElement).toBe(screen.getByRole('button', { name: menu }));
    }
    // A newly opened dialog owns focus after its action callback.
    const dialogInput = document.createElement('input');
    document.body.appendChild(dialogInput);
    const onNewWork = vi.fn(() => { dialogInput.focus(); });
    rerender(<WorkMenuBar onNewWork={onNewWork} />);
    fireEvent.click(screen.getByRole('button', { name: 'File' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create work' }));
    expect(onNewWork).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(dialogInput);
    dialogInput.remove();
  });

  it('opens with arrow keys, skips disabled actions, and Escape restores focus without bubbling', () => {
    const parentEscape = vi.fn();
    render(<div onKeyDown={parentEscape}><WorkMenuBar onNewWork={vi.fn()} onAllWork={vi.fn()} /></div>);
    const file = screen.getByRole('button', { name: 'File' });
    fireEvent.keyDown(file, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'All work' }));
    parentEscape.mockClear();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByRole('group')).toBeNull();
    expect(document.activeElement).toBe(file);
    expect(parentEscape).not.toHaveBeenCalled();
    fireEvent.keyDown(file, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Create work' }));
  });

  it('consumes open-menu Escape before an earlier document navigation handler', () => {
    const navigate = vi.fn();
    document.addEventListener('keydown', navigate);
    try {
      setup();
      fireEvent.click(screen.getByRole('button', { name: 'View' }));
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(navigate).not.toHaveBeenCalled();
      expect(screen.queryByRole('group')).toBeNull();
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(navigate).toHaveBeenCalledTimes(1);
    } finally {
      document.removeEventListener('keydown', navigate);
    }
  });

  it('closes outside and restores the trigger, while Tab leaving does not trap focus', () => {
    setup();
    const file = screen.getByRole('button', { name: 'File' });
    fireEvent.click(file);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('group')).toBeNull();
    expect(document.activeElement).toBe(file);
    fireEvent.click(file);
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    act(() => outside.focus());
    expect(screen.queryByRole('group')).toBeNull();
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it('disables the whole bar while busy and closes an already open menu', () => {
    const onNewWork = vi.fn();
    const { rerender } = render(<WorkMenuBar onNewWork={onNewWork} />);
    fireEvent.click(screen.getByRole('button', { name: 'File' }));
    rerender(<WorkMenuBar onNewWork={onNewWork} disabled />);
    expect(screen.queryByRole('group')).toBeNull();
    for (const button of screen.getAllByRole('button')) expect(button.hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'File' }));
    expect(onNewWork).not.toHaveBeenCalled();
    expect(screen.queryByRole('group')).toBeNull();
  });
});
