import { useState, type ReactNode } from 'react';
import type { AgentItem } from '../api/types';
import { useI18n } from '../i18n/I18nProvider';
import './agent-identity.css';

export function agentIdentityLabel(item: AgentItem, zh = false): string {
  const source = item.source_label || item.execution_domain_id || (zh ? '未知环境' : 'Unknown environment');
  const environment = item.source_short_id ? `${source} (${item.source_short_id})` : source;
  const instance = item.agent_id ? item.agent_id.slice(-8) : (zh ? '实例未知' : 'Unknown instance');
  return `${item.detected_identity} · ${environment} · ${instance}`;
}

export function AgentIdentityLabel({ item }: { item: AgentItem }) {
  const { locale } = useI18n();
  return <span className="agent-identity-label">{agentIdentityLabel(item, locale === 'zh-CN')}</span>;
}

export function AgentIdentityDetails({ item, children }: { item: AgentItem; children?: ReactNode }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  async function copyId() {
    if (!item.agent_id) return;
    try {
      await navigator.clipboard.writeText(item.agent_id);
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
  }
  return <details className="agent-identity-details">
    <summary>{zh ? '身份与观测详情' : 'Identity and observation details'}</summary>
    <dl>
      <div><dt>{zh ? '程序路径' : 'Program path'}</dt><dd>{item.program_path || '—'}</dd></div>
      <div><dt>PID</dt><dd>{item.pid ?? '—'}</dd></div>
      <div><dt>{zh ? '工作目录' : 'Working directory'}</dt><dd>{item.cwd || '—'}</dd></div>
      <div><dt>{zh ? '观测时间' : 'Observed at'}</dt><dd>{item.observed_at ? new Date(item.observed_at).toLocaleString(locale) : '—'}</dd></div>
      <div><dt>{zh ? '完整实例 ID' : 'Full instance ID'}</dt><dd><code>{item.agent_id || '—'}</code>
        {item.agent_id && <button type="button" onClick={() => void copyId()}>{zh ? '复制 ID' : 'Copy ID'}</button>}
        {copyState !== 'idle' && <small role="status">{copyState === 'copied' ? (zh ? '已复制' : 'Copied') : (zh ? '复制失败' : 'Copy failed')}</small>}
      </dd></div>
    </dl>
    {children}
  </details>;
}
