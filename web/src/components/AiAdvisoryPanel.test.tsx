import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { AiAdvisory } from '../api/types';
import { AiAdvisoryPanel } from './AiAdvisoryPanel';

const AVAILABLE: AiAdvisory = {
  schema_version: 'product-ai-advisory-1',
  status: 'AVAILABLE',
  reason_code: 'AI_ADVISORY_AVAILABLE',
  severity: 'UNKNOWN',
  summary: 'several targets were not covered at R3',
  uncertainties: ['AI_ASSESSMENT_UNAVAILABLE'],
  recommended_checks: ['verify the authorization chain'],
  evidence_refs: ['evt-ai-1'],
  provider: 'ollama',
  model: 'llama3.1:8b',
  analyzed_at: '2026-08-15T00:00:00.000000Z',
};

const UNAVAILABLE: AiAdvisory = {
  ...AVAILABLE,
  status: 'UNAVAILABLE',
  reason_code: 'AI_PROVIDER_UNAVAILABLE',
  summary: 'AI provider is unavailable.',
  uncertainties: [],
  recommended_checks: [],
  evidence_refs: [],
  provider: null,
  model: null,
  analyzed_at: null,
};

describe('AiAdvisoryPanel', () => {
  it('renders the empty state verbatim, not dressed up as healthy', () => {
    render(<AiAdvisoryPanel advisory={null} phase="idle" />);
    expect(screen.getByText('No advisory yet.')).toBeTruthy();
  });

  it('shows an UNAVAILABLE machine status and reason_code verbatim', () => {
    render(<AiAdvisoryPanel advisory={UNAVAILABLE} phase="idle" />);
    expect(screen.getByText('UNAVAILABLE')).toBeTruthy();
    expect(screen.getByText('AI_PROVIDER_UNAVAILABLE')).toBeTruthy();
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('renders an AVAILABLE advisory with backend tokens intact', () => {
    render(<AiAdvisoryPanel advisory={AVAILABLE} phase="idle" />);
    expect(screen.getByText('UNKNOWN')).toBeTruthy(); // severity badge, verbatim
    expect(screen.getByText('several targets were not covered at R3')).toBeTruthy();
    expect(screen.getByText('AI_ASSESSMENT_UNAVAILABLE')).toBeTruthy();
    expect(screen.getByText('verify the authorization chain')).toBeTruthy();
    expect(screen.getByText('ollama')).toBeTruthy();
    expect(screen.getByText('llama3.1:8b')).toBeTruthy();
    expect(screen.getByText('AI_ADVISORY_AVAILABLE')).toBeTruthy(); // reason_code
  });

  it('reports loading as a status, never as success', () => {
    render(<AiAdvisoryPanel advisory={null} phase="loading" />);
    expect(screen.getByText('Analyzing…')).toBeTruthy();
  });

  it('renders the failure reason verbatim on error', () => {
    render(<AiAdvisoryPanel advisory={null} phase="error" error="AI_ANALYZE_REQUEST_INVALID" />);
    expect(screen.getByText('Analysis failed (AI_ANALYZE_REQUEST_INVALID)')).toBeTruthy();
  });
});
