import { useEffect, useRef } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { AGENT_COLOR_OPTIONS, agentTint } from '../pages/workAppearance';
import type { useAgentAppearances } from './useAgentAppearances';
import './AgentAppearancePicker.css';

type Agent = { agent_id: string; detected_identity: string };
export function AgentAppearancePicker({ agents, state, onClose, selected }: { agents: Agent[];
  state: ReturnType<typeof useAgentAppearances>; onClose: () => void; selected?: string }) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const root = useRef<HTMLDivElement>(null);
  const copy = zh ? {
    title: 'Agent 颜色', scope: '颜色仅用于当前工作空间显示，不影响身份、状态或执行权限。',
    close: '关闭颜色设置', default: '使用默认颜色', loading: '正在读取已保存颜色…', saving: '正在保存颜色…',
    ready: '已与服务端同步', failed: '未能读取或保存颜色，请刷新重试。',
    conflict: '颜色已在其他窗口变更。请刷新后重新选择。',
    uncertain: '保存回执未知。请重试原请求以确认结果，确认前不能选择其他颜色。',
    refresh: '刷新已保存颜色', retry: '重试原颜色请求', empty: '请先关联实际 Agent。',
  } : {
    title: 'Agent colors', scope: 'Colors apply only to this workspace display and do not change identity, status or execution authority.',
    close: 'Close color settings', default: 'Use default color', loading: 'Loading saved colors…', saving: 'Saving color…',
    ready: 'Synced with server', failed: 'Could not read or save colors. Refresh and retry.',
    conflict: 'The color changed in another window. Refresh before choosing again.',
    uncertain: 'The save receipt is unknown. Retry the original request to confirm before choosing another color.',
    refresh: 'Refresh saved colors', retry: 'Retry original color request', empty: 'Link an actual Agent first.',
  };
  const close = useRef(onClose); close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    root.current?.querySelector<HTMLButtonElement>('[data-close-appearance]')?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  const ordered = [...agents].sort((a, b) => Number(b.agent_id === selected) - Number(a.agent_id === selected));
  const ready = state.phase === 'ready';
  return <div className="work-overlay work-appearance-overlay" role="dialog" aria-modal="true" aria-label={copy.title} ref={root}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close.current(); }
      if (event.key === 'Tab') {
        const nodes = Array.from(root.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled), input:not(:disabled)') ?? []);
        if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes[nodes.length - 1]?.focus(); }
        else if (!event.shiftKey && document.activeElement === nodes[nodes.length - 1]) { event.preventDefault(); nodes[0]?.focus(); }
      }
    }}>
    <section className="work-form work-appearance-form">
      <header><h2>{copy.title}</h2><button type="button" data-close-appearance onClick={onClose} aria-label={copy.close}>×</button></header>
      <p>{copy.scope}</p>
      <p role={['failed', 'conflict', 'uncertain'].includes(state.phase) ? 'alert' : 'status'}>{copy[state.phase]}</p>
      {state.phase === 'uncertain' && <button type="button" onClick={state.retry}>{copy.retry}</button>}
      {['failed', 'conflict', 'ready'].includes(state.phase) && <button type="button" onClick={() => void state.refresh()}>{copy.refresh}</button>}
      {!agents.length && <p>{copy.empty}</p>}
      {ordered.map(agent => {
        const row = state.rows.find(item => item.agent_id === agent.agent_id);
        const label = `${agent.detected_identity} · ${agent.agent_id.slice(-6)}`;
        return <fieldset key={agent.agent_id} disabled={!ready || !row}>
          <legend>{label}</legend>
          <div className="work-appearance-palette" role="radiogroup" aria-label={`${copy.title} ${label}`}>
            {AGENT_COLOR_OPTIONS.map(option => <label key={option.tint} title={zh ? option.nameZh : option.nameEn}>
              <input type="radio" name={`color-${agent.agent_id}`} value={option.tint} checked={row?.color === option.tint}
                aria-label={zh ? option.nameZh : option.nameEn} onChange={() => state.choose(agent.agent_id, option.tint)} />
              <span className="work-appearance-swatch" style={{ background: option.tint }} aria-hidden="true">{row?.color === option.tint ? '✓' : ''}</span>
              <span>{zh ? option.nameZh : option.nameEn}</span>
            </label>)}
          </div>
          <button type="button" onClick={() => state.choose(agent.agent_id, null)} disabled={!ready || !row || row.color === null}>
            <span className="work-appearance-default" style={{ background: agentTint(agent.agent_id) }} aria-hidden="true" />{copy.default}
          </button>
        </fieldset>;
      })}
    </section>
  </div>;
}
