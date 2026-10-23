import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { AgentItem } from '../api/types';
import { I18nProvider } from '../i18n/I18nProvider';
import { AgentObservationSummary } from './AgentObservationSummary';

afterEach(cleanup);
const agent = { lifecycle: 'RUNNING', presence: 'CURRENT', freshness: 'FRESH' } as AgentItem;
describe('Agent observation count scope', () => {
  it('counts only confirmed running current fresh instances and keeps partial coverage visible', () => {
    const items = [agent, { ...agent, lifecycle: 'UNKNOWN' }, { ...agent, presence: 'STALE' },
      { ...agent, freshness: 'STALE' }, { ...agent, presence: undefined }];
    render(<AgentObservationSummary items={items} status="DEGRADED" reasonCode="R4_AGENT_DISCOVERY_PARTIAL" />);
    expect(screen.getByText('Confirmed running: 1 instances')).toBeTruthy();
    expect(screen.getByText(/5 observation records/)).toBeTruthy();
    expect(screen.getByText(/not a complete total/)).toBeTruthy();
    expect(screen.getByText(/Installed programs are listed in Environment/)).toBeTruthy();
  });
  it('explains zero observations in Chinese without claiming zero installed agents', () => {
    render(<I18nProvider systemLanguage="zh-CN"><AgentObservationSummary items={[]} status="DEGRADED" reasonCode="R4_AGENT_DISCOVERY_PARTIAL" /></I18nProvider>);
    expect(screen.getByText('已确认运行：0 个实例')).toBeTruthy();
    expect(screen.getByText(/已安装程序见环境页/)).toBeTruthy();
    expect(screen.getByText(/不代表完整总数/)).toBeTruthy();
  });
  it('does not describe workspace-binding uncertainty as missing process coverage', () => {
    render(<AgentObservationSummary items={[agent]} status="DEGRADED" reasonCode="R4_WORKSPACE_BINDING_INCOMPLETE" />);
    expect(screen.queryByText(/not a complete total/)).toBeNull();
    expect(screen.getByText('Confirmed running: 1 instances')).toBeTruthy();
  });
  it('does not invent a coverage failure for an available projection', () => {
    render(<AgentObservationSummary items={[agent]} status="AVAILABLE" reasonCode="R4_AGENTS_AVAILABLE" />);
    expect(screen.queryByText(/not a complete total/)).toBeNull();
  });
});
