import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type CSSProperties, type ReactNode } from 'react';
import './WorkBubbleGroup.css';
export type BubbleAction = { id: string; label: string; onSelect: () => void };
export default function WorkBubbleGroup({ label, renderTrigger, actions, style, width, height, side = 1, verticalSide = -1, forceClosed = false, pinOnClick = true, onHover, pinLabel = 'Pin children', unpinLabel = 'Unpin children' }: {
  label: string; renderTrigger: (props: ButtonHTMLAttributes<HTMLButtonElement>) => ReactNode;
  actions: BubbleAction[]; style?: CSSProperties; width: number; height: number; side?: number; verticalSide?: number;
  forceClosed?: boolean; pinOnClick?: boolean; pinLabel?: string; unpinLabel?: string; onHover?: (open: boolean) => void;
}) {
  const id = useId(); const root = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false), [focused, setFocused] = useState(false), [pinned, setPinned] = useState(false);
  const [dismissed, setDismissed] = useState(false), [mounted, setMounted] = useState(false), [expanded, setExpanded] = useState(false);
  const grace = useRef<ReturnType<typeof setTimeout>>();
  const pointerFocus = useRef(false);
  const wanted = !forceClosed && !dismissed && (hovered || focused || pinned);
  useEffect(() => {
    let first = 0, second = 0; let exit: ReturnType<typeof setTimeout> | undefined;
    if (wanted) {
      setMounted(true);
      first = requestAnimationFrame(() => { second = requestAnimationFrame(() => setExpanded(true)); });
    } else {
      setExpanded(false);
      if (root.current?.querySelector('.work-bubble-children')?.contains(document.activeElement)) root.current?.querySelector<HTMLButtonElement>('.work-bubble-trigger')?.focus();
      const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      exit = setTimeout(() => setMounted(false), reduced ? 0 : 240);
    }
    return () => { cancelAnimationFrame(first); cancelAnimationFrame(second); if (exit) clearTimeout(exit); };
  }, [wanted]);
  useEffect(() => () => { if (grace.current) clearTimeout(grace.current); }, []);
  useEffect(() => { if (forceClosed) { setPinned(false); setHovered(false); setFocused(false); } }, [forceClosed]);
  useEffect(() => {
    const keyboard = () => { pointerFocus.current = false; };
    document.addEventListener('keydown', keyboard, true);
    return () => document.removeEventListener('keydown', keyboard, true);
  }, []);
  const items = actions.slice(0, 5);
  return <div ref={root} className={`work-bubble-group${expanded ? ' is-expanded' : ''}${pinned ? ' is-pinned' : ''}`}
    style={{ ...style, width, height }} data-bubble-group={label}
    onPointerDown={event => { pointerFocus.current = true; event.stopPropagation(); }}
    onPointerEnter={() => { if (grace.current) clearTimeout(grace.current); setDismissed(false); setHovered(true); onHover?.(true); }}
    onPointerLeave={() => { grace.current = setTimeout(() => { setHovered(false); onHover?.(false); }, 80); }}
    onFocus={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDismissed(false); setFocused(!pointerFocus.current); }}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false); }}
    onKeyDown={event => { pointerFocus.current = false; if (event.key === 'Escape' && (wanted || mounted)) { event.preventDefault(); event.stopPropagation(); setDismissed(true); setPinned(false); setHovered(false); setFocused(false); onHover?.(false); root.current?.querySelector<HTMLButtonElement>('.work-bubble-trigger')?.focus(); } }}>
    {renderTrigger({ className: 'work-bubble-trigger', 'aria-expanded': wanted, 'aria-controls': id,
      'aria-pressed': pinOnClick ? pinned : undefined,
      ...(pinOnClick ? { onClick: () => { setDismissed(false); setPinned(value => !value); } } : {}) })}
    {mounted && <div className="work-bubble-children" id={id} aria-label={label} aria-hidden={!expanded}>
      {items.map((item, index) => {
        const angle = (index - (items.length - 1) / 2) * Math.min(.62, 2.15 / Math.max(1, items.length - 1));
        const dx = side * Math.cos(angle) * (width / 2 + 76), dy = Math.sin(angle) * (height / 2 + 102);
        const mx = side * index * 80, my = verticalSide * (height / 2 + 45);
        const point = { '--mobile-x': `${mx}px`, '--mobile-y': `${my}px`, '--mobile-angle': `${Math.atan2(my, mx)}rad`, '--mobile-length': `${Math.hypot(mx, my)}px`, '--child-x': `${dx}px`, '--child-y': `${dy}px`, '--bridge-angle': `${Math.atan2(dy, dx)}rad`, '--bridge-length': `${Math.hypot(dx, dy)}px` } as CSSProperties;
        return <span className="work-bubble-seat" key={item.id} style={point}>
          <span className="work-bubble-bridge" aria-hidden="true" />
          <button type="button" className="work-bubble-child" data-bubble-child={item.id} disabled={!expanded} tabIndex={expanded ? 0 : -1}
            onClick={event => { event.stopPropagation(); item.onSelect(); }}>{item.label}</button>
        </span>;
      })}
      {!pinOnClick && <button type="button" className="work-bubble-pin" disabled={!expanded} tabIndex={expanded ? 0 : -1}
        aria-pressed={pinned} aria-label={`${pinned ? unpinLabel : pinLabel} ${label}`} onClick={() => setPinned(value => !value)}>{pinned ? unpinLabel : pinLabel}</button>}
    </div>}
  </div>;
}
