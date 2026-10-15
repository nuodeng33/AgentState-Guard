/**
 * Analyze Current Environment wiring tests: POST /api/ai/analyze exactly {},
 * advisory tokens verbatim, failure surfaces only the stable reason_code.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { createApiClient } from '../api/client';
import { AnalyzeSection } from './AnalyzeSection';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ADVISORY = {
  schema_version: 'product-ai-advisory-1',
  status: 'AVAILABLE',
  reason_code: 'AI_ADVISORY_AVAILABLE',
  severity: 'MEDIUM',
  summary: 'one target lacks restore verification',
  uncertainties: ['AI_ASSESSMENT_UNAVAILABLE'],
  recommended_checks: ['run a test restore'],
  evidence_refs: ['evt-ai-9'],
  provider: 'ollama',
  model: 'llama3.1:8b',
  analyzed_at: '2026-08-16T00:00:00Z',
};

describe('AnalyzeSection', () => {
  it('posts exactly {} and renders the backend advisory verbatim', async () => {
    const posts: unknown[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/session') return json({ token: 't' });
      if (path === '/api/ai/analyze' && init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body ?? '{}')));
        return json(ADVISORY);
      }
      return json({ error: 'unexpected' }, 404);
    });
    const client = createApiClient(fetchImpl as unknown as typeof fetch);
    render(<AnalyzeSection client={client} />);

    fireEvent.click(screen.getByRole('button', { name: 'Analyze Current Environment' }));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0]).toEqual({});

    expect(await screen.findByText('MEDIUM')).toBeTruthy();
    expect(screen.getByText('one target lacks restore verification')).toBeTruthy();
    expect(screen.getByText('run a test restore')).toBeTruthy();
    expect(screen.getByText('AI_ADVISORY_AVAILABLE')).toBeTruthy();
    expect(screen.getByText('ollama')).toBeTruthy();
  });

  it('UNAVAILABLE advisory with provider not configured stays a warning, never success', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/session') return json({ token: 't' });
      if (path === '/api/ai/analyze' && init?.method === 'POST') {
        return json(
          {
            ...ADVISORY,
            status: 'UNAVAILABLE',
            reason_code: 'AI_PROVIDER_UNAVAILABLE',
            summary: 'AI provider is unavailable.',
            uncertainties: [],
            recommended_checks: [],
            evidence_refs: [],
            provider: null,
            model: null,
            analyzed_at: null,
          },
          503,
        );
      }
      return json({ error: 'unexpected' }, 404);
    });
    const client = createApiClient(fetchImpl as unknown as typeof fetch);
    render(<AnalyzeSection client={client} />);

    fireEvent.click(screen.getByRole('button', { name: 'Analyze Current Environment' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('AI_PROVIDER_UNAVAILABLE');
  });
});
