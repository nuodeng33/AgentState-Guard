import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';

import { useI18n } from '../i18n/I18nProvider';
import { workspaceCopy } from '../pages/workspaceCopy';
import './WorkMenuBar.css';

export interface WorkMenuBarProps {
  onNewWork: () => void;
  onSave?: () => void;
  onAllWork?: () => void;
  onNewTask?: () => void;
  onNewArtifact?: () => void;
  onBoard?: () => void;
  onSpace?: () => void;
  on3d?: () => void;
  onEditWork?: () => void;
  onReviseRequirement?: () => void;
  onEditTask?: () => void;
  onSessions?: () => void;
  onDelivery?: () => void;
  onRecovery?: () => void;
  onProtectionLinks?: () => void;
  onAgents?: () => void;
  onAppearance?: () => void;
  disabled?: boolean;
}

type MenuName = 'file' | 'edit' | 'view';

export function WorkMenuBar({
  onNewWork, onSave, onAllWork, onNewTask, onNewArtifact,
  onBoard, onSpace, on3d, onEditWork, onReviseRequirement, onEditTask, onSessions, onDelivery, onRecovery, onProtectionLinks, onAgents, onAppearance, disabled = false,
}: WorkMenuBarProps) {
  const { locale } = useI18n();
  const copy = workspaceCopy[locale];
  const zh = locale === 'zh-CN';
  const id = useId();
  const root = useRef<HTMLElement>(null);
  const triggers = useRef<Partial<Record<MenuName, HTMLButtonElement | null>>>({});
  const [open, setOpen] = useState<MenuName | null>(null);
  const focusEdge = useRef<'first' | 'last' | null>(null);
  const menus = [
    { name: 'file' as const, label: zh ? '文件' : 'File', items: [
      { label: copy.create, action: onNewWork },
      { label: copy.saveView, action: onSave },
      { label: copy.delivery, action: onDelivery },
      { label: copy.recovery, action: onRecovery },
      { label: zh ? '保护目录' : 'Protected directories', action: onProtectionLinks },
      { label: copy.back, action: onAllWork },
    ] },
    { name: 'edit' as const, label: zh ? '编辑' : 'Edit', items: [
      { label: zh ? '工作信息' : 'Work information', action: onEditWork },
      { label: zh ? '修订要求' : 'Revise requirement', action: onReviseRequirement },
      { label: copy.newTask, action: onNewTask },
      { label: zh ? '编辑任务' : 'Edit task', action: onEditTask },
      { label: copy.newArtifact, action: onNewArtifact },
    ] },
    { name: 'view' as const, label: zh ? '视图' : 'View', items: [
      { label: zh ? '工作台' : 'Workbench', action: onBoard },
      { label: zh ? '关系空间' : 'Relationship space', action: onSpace },
      { label: copy.restScene, action: on3d },
      { label: copy.sessions, action: onSessions },
      { label: copy.agents, action: onAgents },
      { label: zh ? '智能体颜色' : 'Agent colors', action: onAppearance },
    ] },
  ];

  function close(restoreFocus = true) {
    setOpen(null);
    focusEdge.current = null;
    if (restoreFocus && open) triggers.current[open]?.focus();
  }

  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) {
        setOpen(null);
        focusEdge.current = null;
        triggers.current[open!]?.focus();
      }
    }
    function escape(event: globalThis.KeyboardEvent) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(null);
      focusEdge.current = null;
      triggers.current[open!]?.focus();
    }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape, true);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape, true);
    };
  }, [open]);

  useEffect(() => {
    if (disabled) setOpen(null);
  }, [disabled]);

  useEffect(() => {
    if (!open || !focusEdge.current) return;
    const buttons = root.current?.querySelectorAll<HTMLButtonElement>('.work-menu-dropdown button:not(:disabled)');
    const button = focusEdge.current === 'first' ? buttons?.[0] : buttons?.[buttons.length - 1];
    focusEdge.current = null;
    button?.focus();
  }, [open]);

  function triggerKey(event: KeyboardEvent<HTMLButtonElement>, name: MenuName) {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    focusEdge.current = event.key === 'ArrowDown' ? 'first' : 'last';
    if (open === name) {
      const buttons = root.current?.querySelectorAll<HTMLButtonElement>('.work-menu-dropdown button:not(:disabled)');
      (focusEdge.current === 'first' ? buttons?.[0] : buttons?.[buttons.length - 1])?.focus();
      focusEdge.current = null;
    } else setOpen(name);
  }

  return <nav
    className="work-menu-bar"
    aria-label={zh ? '工作菜单' : 'Work menus'}
    ref={root}
    onBlur={event => {
      if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) close(false);
    }}
  >
    {menus.map(menu => <div className="work-menu-folder" key={menu.name}>
      <button
        type="button"
        className="work-menu-trigger"
        ref={element => { triggers.current[menu.name] = element; }}
        aria-expanded={open === menu.name}
        aria-controls={`${id}-${menu.name}`}
        disabled={disabled}
        onClick={() => { focusEdge.current = null; setOpen(open === menu.name ? null : menu.name); }}
        onKeyDown={event => triggerKey(event, menu.name)}
      >{menu.label}</button>
      {open === menu.name && <div className="work-menu-dropdown" id={`${id}-${menu.name}`} role="group" aria-label={menu.label}>
        {menu.items.map(item => <button
          type="button"
          className="work-menu-action"
          key={item.label}
          disabled={disabled || !item.action}
          onClick={() => { close(); item.action?.(); }}
        >{item.label}</button>)}
      </div>}
    </div>)}
  </nav>;
}

export default WorkMenuBar;
