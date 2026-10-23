import { expect, it } from 'vitest';
import { AGENT_COLOR_OPTIONS, agentTint } from './workAppearance';
it('keeps fallback colors deterministic and accepts only palette preferences', () => {
  expect(agentTint('stable-agent')).toBe(agentTint('stable-agent'));
  expect(agentTint('stable-agent', { 'stable-agent': '#e3a5bf' })).toBe('#e3a5bf');
  expect(agentTint('stable-agent', { 'stable-agent': 'url(secret)' })).toBe(agentTint('stable-agent'));
  expect(AGENT_COLOR_OPTIONS.some(option => option.tint === agentTint('other-agent'))).toBe(true);
});
