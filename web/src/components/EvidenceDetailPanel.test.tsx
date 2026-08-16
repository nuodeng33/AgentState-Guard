import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { EvidenceDetail } from '../api/types';
import { EvidenceDetailPanel } from './EvidenceDetailPanel';

const AVAILABLE: EvidenceDetail = {
  schema_version: 'r4-product-evidence-1',
  status: 'AVAILABLE',
  reason_code: 'RECOVERY_COVERAGE_EVALUATED',
  event_id: 'ledger-evt-1',
  event_type: 'RECOVERY_COVERAGE_EVALUATED',
  observed_at: '2026-08-15T00:00:00.000000Z',
  recorded_at: '2026-08-15T00:00:01.000000Z',
  source: 'core',
  subject: 'checkpoint-aaa',
  result: 'COMPLETE',
  verification_summary: 'VERIFIED_R2',
  checkpoint_id: 'checkpoint-aaa',
  change_id: null,
  chain_ref: 'sha256:'.padEnd(71, 'ab'),
  sanitized_detail: {
    affected_objects: ['sha256:'.padEnd(71, 'cd')],
    verification: 'COMPLETE',
  },
  related_evidence_refs: ['ledger-evt-0'],
};

const NOT_FOUND: EvidenceDetail = {
  schema_version: 'r4-product-evidence-1',
  status: 'NOT_FOUND',
  reason_code: 'EVIDENCE_EVENT_NOT_FOUND',
  event_id: 'ledger-evt-missing',
};

describe('EvidenceDetailPanel', () => {
  it('honest empty state when nothing is selected', () => {
    render(<EvidenceDetailPanel detail={null} phase="idle" />);
    expect(
      screen.getByText('Select an evidence reference to inspect its sanitized detail.'),
    ).toBeTruthy();
  });

  it('NOT_FOUND surfaces the machine reason verbatim without inventing detail', () => {
    render(<EvidenceDetailPanel detail={NOT_FOUND} phase="idle" />);
    expect(screen.getAllByText('NOT_FOUND').length).toBeGreaterThan(0);
    expect(screen.getAllByText('EVIDENCE_EVENT_NOT_FOUND').length).toBeGreaterThan(0);
    expect(screen.getAllByText('ledger-evt-missing').length).toBeGreaterThan(0);
  });

  it('AVAILABLE renders sanitized detail only, tokens verbatim', () => {
    render(<EvidenceDetailPanel detail={AVAILABLE} phase="idle" />);
    expect(screen.getAllByText('RECOVERY_COVERAGE_EVALUATED').length).toBeGreaterThan(0);
    expect(screen.getByText('core')).toBeTruthy(); // source
    expect(screen.getAllByText('COMPLETE').length).toBeGreaterThan(0); // result
    expect(screen.getAllByText(/sha256:/).length).toBeGreaterThan(0); // chain_ref + affected
    expect(screen.getByText('ledger-evt-0')).toBeTruthy(); // related ref
  });

  it('loading phase is visible, never fake content', () => {
    render(<EvidenceDetailPanel detail={null} phase="loading" />);
    expect(screen.getByText('Loading Evidence detail…')).toBeTruthy();
  });

  it('error phase shows the error and nothing else invented', () => {
    render(<EvidenceDetailPanel detail={null} phase="error" error="boom" />);
    expect(screen.getByText('boom')).toBeTruthy();
  });
});
