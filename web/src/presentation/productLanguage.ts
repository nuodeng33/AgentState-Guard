import type { Locale } from '../i18n/locale';

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

const EN_ROLES: Record<string, string> = {
  EXECUTION_AGENT: 'Execution agent',
  AGENT_HOST: 'Agent host',
  MODEL_ROUTER: 'Model router',
};

const ZH_ROLES: Record<string, string> = {
  EXECUTION_AGENT: '执行智能体',
  AGENT_HOST: '智能体宿主',
  MODEL_ROUTER: '模型路由器',
};

const EN_WORKSPACE_STATUS: Record<string, string> = {
  BOUND: 'Linked',
  UNBOUND: 'Not linked',
  UNKNOWN: 'Unknown',
};

const ZH_WORKSPACE_STATUS: Record<string, string> = {
  BOUND: '已绑定',
  UNBOUND: '未绑定',
  UNKNOWN: '未知',
};

const EN_RUNTIME_TYPES: Record<string, string> = {
  CONTAINER_RUNTIME: 'Container runtime',
  SELF_RUNTIME: 'Host runtime',
  WSL_DISTRO_RUNTIME: 'WSL distro runtime',
};

const ZH_RUNTIME_TYPES: Record<string, string> = {
  CONTAINER_RUNTIME: '容器运行环境',
  SELF_RUNTIME: '本机运行环境',
  WSL_DISTRO_RUNTIME: 'WSL 发行版运行环境',
};

const EN_CAPABILITIES: Record<string, string> = {
  domain_visible: 'Domain visible',
  self_visible: 'Host visible',
};

const ZH_CAPABILITIES: Record<string, string> = {
  domain_visible: '运行域可见',
  self_visible: '本机可见',
};

const ZH_PRODUCT_TOKENS: Record<string, string> = {
  AVAILABLE: '可用',
  COMPLETE: '完整',
  DEGRADED: '降级',
  DETECTED: '已检测',
  DISABLED: '已停用',
  EMPTY: '暂无数据',
  ENABLED: '已启用',
  EVIDENCE_INSUFFICIENT: '证据不足',
  FAIL: '失败',
  INFO: '信息',
  MISSING: '缺失',
  NONE: '无',
  NOT_RUN: '尚未执行',
  NOT_RUN_P6: '尚未执行',
  OK: '正常',
  PARTIAL: '部分完成',
  PASS: '通过',
  SKIP: '已跳过',
  UNKNOWN: '未知',
  UNREACHABLE: '不可达',
  VERIFIED_R2: '已验证到 R2',
  WARN: '警告',
};

const ZH_REASON_CODES: Record<string, string> = {
  EXPLICIT_RESTORE_CONFIRMATION_REQUIRED: '恢复前需要明确确认',
  PAIRING_EXPIRED: '配对请求已过期',
  PRODUCT_CONFIG_TARGET_ONLY: '仅恢复产品配置范围',
  R4_STATE_EMPTY: '暂无状态记录',
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

function localizedToken(
  token: string,
  locale: Locale,
  english: Record<string, string>,
  chinese: Record<string, string>,
): string {
  const primary = locale === 'zh-CN' ? chinese[token] : english[token];
  if (!primary) return token;
  return locale === 'zh-CN' ? `${primary}（${token}）` : primary;
}

export function productTokenDisplay(token: string | null | undefined, locale: Locale): string {
  if (!token) return '';
  return locale === 'zh-CN' && ZH_PRODUCT_TOKENS[token]
    ? `${ZH_PRODUCT_TOKENS[token]}（${token}）`
    : token;
}

export function reasonCodeDisplay(code: string | null | undefined, locale: Locale): string {
  if (!code) return '';
  return locale === 'zh-CN' && ZH_REASON_CODES[code]
    ? `${ZH_REASON_CODES[code]}（${code}）`
    : code;
}

export function runtimeTypeDisplay(type: string | null | undefined, locale: Locale): string {
  if (!type) return '';
  return localizedToken(type, locale, EN_RUNTIME_TYPES, ZH_RUNTIME_TYPES);
}

export function capabilityDisplay(capability: string, locale: Locale): string {
  return localizedToken(capability, locale, EN_CAPABILITIES, ZH_CAPABILITIES);
}

/** Returns only a suffix already supplied by the bounded backend label. */
export function agentInstanceId(label: string | null | undefined): string | null {
  if (!label) return null;
  const [, ...suffix] = label.trim().split(/\s+/);
  return suffix.length > 0 ? suffix.join(' ') : null;
}

/**
 * Bounded backend role tokens rendered as product language. Unknown values
 * render verbatim — presentation never upgrades or fabricates semantics.
 */
export function agentRoleDisplay(role: string | null | undefined, locale: Locale = 'en-US'): string {
  if (!role) return '';
  return localizedToken(role, locale, EN_ROLES, ZH_ROLES);
}

/**
 * Bounded workspace-association badges rendered as product language.
 */
export function workspaceStatusDisplay(
  status: string | null | undefined,
  locale: Locale = 'en-US',
): string {
  if (!status) return '';
  return localizedToken(status, locale, EN_WORKSPACE_STATUS, ZH_WORKSPACE_STATUS);
}
