import { useState, type ReactNode } from 'react';
import type { AgentItem } from '../api/types';
import { useI18n } from '../i18n/I18nProvider';
import './agent-identity.css';

/** Display names come from the observed entry, never its opaque instance ID. */
export function agentProgramName(item: Pick<AgentItem, 'detected_identity' | 'program_path'>, zh = false): string {
  const parts = (item.program_path ?? '').split(/[\\/]/).filter(Boolean);
  const filename = parts[parts.length - 1]?.replace(/\.(exe|cmd|bat|ps1|[cm]?js)$/i, '');
  const provider = item.detected_identity.toUpperCase().replace(/[- ]/g, '_');
  // These are the user's separate named installations. This is a display hint
  // from a complete path component, never evidence of API or control authority.
  if (filename?.toLowerCase() === 'codex') {
    return parts.slice(0, -1).some(part => /^(codex[-_ ]api|api[-_ ]codex)$/i.test(part)) ? 'API Codex' : 'Codex';
  }
  if (filename && !/^(node|python[0-9.]*(w)?|cli|main|index|server)$/i.test(filename)) return filename;
  return ({ CODEX: 'Codex', CLAUDE: 'Claude', CLAUDE_CODE: 'Claude Code', CLOUDCLI: 'CloudCLI',
    KIMI: 'Kimi', KIMI_CODE: 'Kimi Code', CCR: 'Claude Code Router' }[provider]) || item.detected_identity || (zh ? '未知程序' : 'Unknown program');
}

export function agentIdentityLabel(item: Pick<AgentItem, 'detected_identity' | 'program_path'>, zh = false): string {
  return agentProgramName(item, zh);
}

export function agentSourceLabel(item: AgentItem, zh = false): string {
  const local = item.source_id === 'local' || item.source_label === 'Local processes';
  const source = local ? (zh ? '本机' : 'This computer') : item.source_label || (zh ? '未知环境' : 'Unknown environment');
  return item.pid != null ? `${source} · PID ${item.pid}` : source;
}

export function AgentProgramIcon({ item }: { item: Pick<AgentItem, 'detected_identity' | 'program_path'> }) {
  return <span className="work-agent-app-icon" aria-hidden="true" data-program={agentProgramName(item)}>
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="m4.5 6 4 4-4 4M11 14h4.5" />
    </svg>
  </span>;
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
      <div><dt>{zh ? '识别类型' : 'Detected type'}</dt><dd>{item.detected_identity}</dd></div>
      <div><dt>{zh ? '观察来源' : 'Observation source'}</dt><dd>{item.source_label || item.execution_domain_id || '—'}{item.source_short_id ? ` (${item.source_short_id})` : ''}</dd></div>
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
