import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { RuntimeView } from '../api/types';
import { RuntimeViewBody } from './RuntimePage';

const EMPTY: RuntimeView = {
  schema_version: 'r4-p8-1',
  view: 'runtime',
  status: 'EMPTY',
  reason_code: 'R4_STATE_EMPTY',
  evidence_refs: [],
  items: [],
};

const DEGRADED: RuntimeView = {
  ...EMPTY,
  status: 'DEGRADED',
  reason_code: 'R4_LEDGER_INVALID',
};

const AVAILABLE: RuntimeView = {
  ...EMPTY,
  status: 'AVAILABLE',
  reason_code: 'R4_RUNTIME_AVAILABLE',
  evidence_refs: ['evt-runtime-1', 'evt-runtime-2'],
  items: [
    {
      runtime_type: 'CONTAINER_RUNTIME',
      execution_domain_id: 'docker-container-abc123def456',
      domain_label: 'agent-k3',
      availability: 'AVAILABLE',
      capabilities: ['domain_visible'],
      reason_code: 'RUNTIME_DETECTED',
      uncertainty: false,
      evidence_refs: ['evt-runtime-1'],
    },
    {
      runtime_type: null,
      execution_domain_id: 'edge-domain',
      availability: 'UNREACHABLE',
      capabilities: [],
      reason_code: 'DISCOVERY_PROBE_UNREACHABLE',
      uncertainty: true,
      evidence_refs: ['evt-runtime-2'],
    },
  ],
};

describe('RuntimePage EMPTY state', () => {
  it('renders an explicit empty state, not a blank page', () => {
    render(<RuntimeViewBody data={EMPTY} />);
    expect(screen.getByText('EMPTY')).toBeTruthy();
    expect(screen.getByText('No runtime records')).toBeTruthy();
    expect(screen.getAllByText('R4_STATE_EMPTY').length).toBeGreaterThan(0);
  });
});

describe('RuntimePage DEGRADED state', () => {
  it('shows a fail-closed degraded panel and never looks healthy', () => {
    render(<RuntimeViewBody data={DEGRADED} />);
    expect(screen.getByText('DEGRADED')).toBeTruthy();
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('Runtime view degraded');
    expect(alert.textContent).toContain('R4_LEDGER_INVALID');
    expect(screen.queryByText('AVAILABLE')).toBeNull();
  });
});

describe('RuntimePage AVAILABLE state', () => {
  it('renders authoritative fields and per-item availability verbatim', () => {
    render(<RuntimeViewBody data={AVAILABLE} />);
    expect(screen.getByText('agent-k3')).toBeTruthy();
    expect(screen.queryByText('Runtime 1')).toBeNull();
    expect(screen.getByText('Container runtime')).toBeTruthy();
    expect(screen.getAllByText('AVAILABLE').length).toBeGreaterThan(0);
    expect(screen.getByText('UNREACHABLE')).toBeTruthy();
    expect(screen.getByText('docker-container-abc123def456')).toBeTruthy();
    expect(screen.getByText('Domain visible')).toBeTruthy();
    expect(screen.getByText('uncertain')).toBeTruthy();
  });

  it('keeps the machine reason_code in collapsed secondary diagnostics', () => {
    render(<RuntimeViewBody data={AVAILABLE} />);
    expect(screen.queryByText('R4_RUNTIME_AVAILABLE')).toBeNull();
    expect(screen.queryByText('DISCOVERY_PROBE_UNREACHABLE')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: /Evidence/ })[1]);
    expect(screen.getByText('DISCOVERY_PROBE_UNREACHABLE')).toBeTruthy();
  });
});
