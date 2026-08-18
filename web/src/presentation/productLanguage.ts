/**
 * Product-facing display language for bounded backend identity tokens.
 *
 * Backend authority semantics never change here: EMPTY stays EMPTY, AVAILABLE
 * stays AVAILABLE, reason_code tokens stay verbatim in secondary diagnostics.
 * This module only translates the *agent identity* string the main UI renders
 * as a card title into user-readable AgentState Guard product language.
 * Unknown tokens render verbatim — the UI never upgrades, infers, or
 * fabricates an identity.
 */

const KNOWN_IDENTITIES: Record<string, string> = {
  claude: 'Claude Code',
  'claude-code': 'Claude Code',
  codex: 'Codex',
  kimi: 'Kimi Code',
  'kimi-code': 'Kimi Code',
  ccr: 'Claude Code Router',
  cloudcli: 'CloudCLI Shell',
};

const KNOWN_ROLES: Record<string, string> = {
  EXECUTION_AGENT: 'Execution agent',
  AGENT_HOST: 'Agent host',
  MODEL_ROUTER: 'Model router',
};

const KNOWN_WORKSPACE_STATUS: Record<string, string> = {
  BOUND: 'Linked',
  UNBOUND: 'Not linked',
  UNKNOWN: 'Unknown',
};

function identityToken(token: string): string | undefined {
  return KNOWN_IDENTITIES[token.toLowerCase().replace(/_/g, '-')];
}

export function agentDisplayName(identity: string | null | undefined): string {
  if (!identity) return '';
  const trimmed = identity.trim();
  if (!trimmed) return '';
  const [base, ...rest] = trimmed.split(/\s+/);
  const mapped = identityToken(base);
  if (!mapped) return trimmed;
  return rest.length > 0 ? `${mapped} ${rest.join(' ')}` : mapped;
}

/**
 * Bounded backend role tokens rendered as product language. Unknown values
 * render verbatim — presentation never upgrades or fabricates semantics.
 */
export function agentRoleDisplay(role: string | null | undefined): string {
  if (!role) return '';
  return KNOWN_ROLES[role] ?? role;
}

/**
 * Bounded workspace-association badges rendered as product language.
 */
export function workspaceStatusDisplay(status: string | null | undefined): string {
  if (!status) return '';
  return KNOWN_WORKSPACE_STATUS[status] ?? status;
}
