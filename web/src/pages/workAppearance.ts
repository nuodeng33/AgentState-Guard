export const AGENT_COLOR_OPTIONS = [
  { tint: '#7cc6bc', nameEn: 'Teal', nameZh: '青绿' },
  { tint: '#b9a6f4', nameEn: 'Lavender', nameZh: '薰紫' },
  { tint: '#8fb8e8', nameEn: 'Blue', nameZh: '浅蓝' },
  { tint: '#e3a5bf', nameEn: 'Rose', nameZh: '玫粉' },
  { tint: '#d9c08a', nameEn: 'Gold', nameZh: '暖金' },
  { tint: '#a8c98f', nameEn: 'Green', nameZh: '草绿' },
] as const;
export function agentTint(agentId: string, selected?: Record<string, string>) {
  const chosen = selected?.[agentId];
  if (AGENT_COLOR_OPTIONS.some(option => option.tint === chosen)) return chosen!;
  let hash = 2166136261;
  for (let i = 0; i < agentId.length; i++) hash = Math.imul(hash ^ agentId.charCodeAt(i), 16777619);
  return AGENT_COLOR_OPTIONS[(hash >>> 0) % AGENT_COLOR_OPTIONS.length].tint;
}
