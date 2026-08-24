import { describe, expect, it } from 'vitest';

import {
  agentDisplayName,
  agentInstanceId,
  agentRoleDisplay,
  capabilityDisplay,
  productTokenDisplay,
  reasonCodeDisplay,
  runtimeTypeDisplay,
  workspaceStatusDisplay,
} from './productLanguage';

/**
 * Bounded backend identity tokens render as user-readable product language;
 * unknown tokens stay verbatim and are never upgraded or fabricated.
 */
describe('agentDisplayName', () => {
  it('maps known bounded identities to product names', () => {
    expect(agentDisplayName('CLAUDE')).toBe('Claude Code');
    expect(agentDisplayName('claude-code')).toBe('Claude Code');
    expect(agentDisplayName('CODEX')).toBe('Codex');
    expect(agentDisplayName('KIMI_CODE')).toBe('Kimi Code');
    expect(agentDisplayName('kimi_code')).toBe('Kimi Code');
    expect(agentDisplayName('kimi-code')).toBe('Kimi Code');
  });

  it('keeps instance suffixes so same-product instances stay distinguishable', () => {
    expect(agentDisplayName('CODEX a1b2c3')).toBe('Codex a1b2c3');
    expect(agentDisplayName('KIMI_CODE 00ff11')).toBe('Kimi Code 00ff11');
  });

  it('never fabricates a name for unknown identities', () => {
    expect(agentDisplayName('mystery-launcher')).toBe('mystery-launcher');
    expect(agentDisplayName('UNCLASSIFIED')).toBe('UNCLASSIFIED');
  });

  it('handles empty input without crashing', () => {
    expect(agentDisplayName('')).toBe('');
    expect(agentDisplayName(null)).toBe('');
    expect(agentDisplayName(undefined)).toBe('');
  });
});

describe('agentRoleDisplay', () => {
  it('maps bounded role tokens to product language', () => {
    expect(agentRoleDisplay('EXECUTION_AGENT')).toBe('Execution agent');
    expect(agentRoleDisplay('AGENT_HOST')).toBe('Agent host');
    expect(agentRoleDisplay('MODEL_ROUTER')).toBe('Model router');
  });

  it('never fabricates a role for unknown tokens', () => {
    expect(agentRoleDisplay('SUPER_ADMIN')).toBe('SUPER_ADMIN');
    expect(agentRoleDisplay('')).toBe('');
    expect(agentRoleDisplay(null)).toBe('');
  });
});

describe('workspaceStatusDisplay', () => {
  it('maps bounded workspace association tokens to product language', () => {
    expect(workspaceStatusDisplay('BOUND')).toBe('Linked');
    expect(workspaceStatusDisplay('UNBOUND')).toBe('Not linked');
    expect(workspaceStatusDisplay('UNKNOWN')).toBe('Unknown');
  });

  it('never fabricates a status for unknown tokens', () => {
    expect(workspaceStatusDisplay('PINNED')).toBe('PINNED');
    expect(workspaceStatusDisplay('')).toBe('');
    expect(workspaceStatusDisplay(undefined)).toBe('');
  });
});

describe('Chinese machine-token annotations', () => {
  it('adds concise Chinese meaning while preserving the raw authority token', () => {
    expect(productTokenDisplay('AVAILABLE', 'zh-CN')).toBe('可用（AVAILABLE）');
    expect(productTokenDisplay('NOT_RUN', 'zh-CN')).toBe('尚未执行（NOT_RUN）');
    expect(reasonCodeDisplay('PAIRING_EXPIRED', 'zh-CN')).toBe(
      '配对请求已过期（PAIRING_EXPIRED）',
    );
    expect(runtimeTypeDisplay('CONTAINER_RUNTIME', 'zh-CN')).toBe(
      '容器运行环境（CONTAINER_RUNTIME）',
    );
    expect(capabilityDisplay('domain_visible', 'zh-CN')).toBe(
      '运行域可见（domain_visible）',
    );
    expect(agentRoleDisplay('EXECUTION_AGENT', 'zh-CN')).toBe(
      '执行智能体（EXECUTION_AGENT）',
    );
    expect(workspaceStatusDisplay('BOUND', 'zh-CN')).toBe('已绑定（BOUND）');
  });

  it('humanizes the runtime surface in English without altering unknown tokens', () => {
    expect(runtimeTypeDisplay('CONTAINER_RUNTIME', 'en-US')).toBe('Container runtime');
    expect(capabilityDisplay('domain_visible', 'en-US')).toBe('Domain visible');
    expect(reasonCodeDisplay('NEW_REASON', 'zh-CN')).toBe('NEW_REASON');
  });
});

describe('agentInstanceId', () => {
  it('extracts only a backend-provided bounded instance suffix', () => {
    expect(agentInstanceId('CODEX 4f2a91')).toBe('4f2a91');
    expect(agentInstanceId('KIMI_CODE 00ff11')).toBe('00ff11');
    expect(agentInstanceId('CODEX')).toBeNull();
    expect(agentInstanceId(null)).toBeNull();
  });
});
