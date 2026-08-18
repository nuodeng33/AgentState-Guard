import { describe, expect, it } from 'vitest';

import { agentDisplayName, agentRoleDisplay, workspaceStatusDisplay } from './productLanguage';

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
