import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { AgentItem } from '../api/types';
import { AgentIdentityDetails, AgentIdentityLabel, AgentProgramIcon, agentProgramName, agentSourceLabel } from './AgentIdentity';

afterEach(cleanup);
const item: AgentItem = { agent_id: 'agent-internal-12345678', detected_identity: 'CODEX', role: 'UNKNOWN',
  lifecycle: 'UNKNOWN', confidence: .5, execution_domain_id: null, workspace: { status: 'UNKNOWN', binding_ref: null },
  reason_code: 'UNKNOWN', uncertainty: true, evidence_refs: [], source_id: 'local', source_label: 'Local processes', pid: 42 };

it('uses real Windows/Linux entry filenames and separates the two named Codex installations', () => {
  expect(agentProgramName({ ...item, program_path: 'C:/Users/Test/AppData/Local/OpenAI/Codex/bin/a96/codex.EXE' })).toBe('Codex');
  expect(agentProgramName({ ...item, program_path: 'D:\\Codex-API\\ProfileHome\\AppData\\Local\\OpenAI\\Codex\\bin\\a96\\codex.exe' })).toBe('API Codex');
  expect(agentProgramName({ ...item, program_path: '/usr/local/bin/codex' })).toBe('Codex');
  expect(agentProgramName({ ...item, program_path: '/opt/Codex-API-not-an-installation/codex' })).toBe('Codex');
  expect(agentProgramName({ ...item, program_path: '/opt/agent/my-coding-tool.mjs' })).toBe('my-coding-tool');
});

it('does not call a generic host or internal cli filename an application name', () => {
  expect(agentProgramName({ ...item, program_path: '/opt/@openai/codex/bin/codex.js' })).toBe('Codex');
  expect(agentProgramName({ ...item, detected_identity: 'CLOUDCLI', program_path: '/opt/cloudcli/cli.js' })).toBe('CloudCLI');
  expect(agentProgramName({ ...item, program_path: 'C:/node.exe' })).toBe('Codex');
  expect(agentProgramName({ ...item, program_path: null })).toBe('Codex');
});

it('keeps names concise while full path, original type and IDs remain available in details', () => {
  const observed = { ...item, program_path: 'D:/Codex-API/ProfileHome/bin/codex.exe', source_short_id: 'container123' };
  render(<><AgentIdentityLabel item={observed} /><AgentProgramIcon item={observed} /><AgentIdentityDetails item={observed} /></>);
  expect(screen.getByText('API Codex').textContent).toBe('API Codex');
  fireEvent.click(screen.getByText('Identity and observation details'));
  expect(screen.getByText(observed.program_path)).toBeTruthy();
  expect(screen.getByText(observed.agent_id!)).toBeTruthy();
  expect(screen.getByText('CODEX')).toBeTruthy();
  expect(agentSourceLabel(observed)).toBe('This computer · PID 42');
  expect(agentSourceLabel(observed, true)).toBe('本机 · PID 42');
  expect(observed.lifecycle).toBe('UNKNOWN');
});
