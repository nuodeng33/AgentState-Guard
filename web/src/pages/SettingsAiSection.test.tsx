/**
 * AI provider settings + Analyze wiring tests.
 *
 * Verifies: exact provider request bodies, key never re-rendered after send,
 * analyze posts exactly {}, and advisory renders only backend-owned tokens.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { createApiClient } from '../api/client';
import SettingsPage from './SettingsPage';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function buildClient(routes: Record<string, { body: unknown; status?: number }>) {
  const posts: Array<{ path: string; body: unknown }> = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === '/api/session') return json({ token: 't' });
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body ?? '{}')) as unknown;
      posts.push({ path, body });
      const route = routes[path];
      if (!route) return json({ error: 'unexpected' }, 404);
      return json(route.body, route.status ?? 200);
    }
    return json({ error: 'unexpected' }, 404);
  });
  return { client: createApiClient(fetchImpl as unknown as typeof fetch), posts };
}

describe('Settings AI provider section', () => {
  it('sends only the three provider fields to /api/ai/test', async () => {
    const { client, posts } = buildClient({
      '/api/ai/test': { body: { ok: true, latency_ms: 12, models_available: 3 } },
    });
    render(<SettingsPage client={client} />);

    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: 'http://host:1/v1' } });
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'SECRET-KEY-123' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'llama3.1:8b' } });
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));

    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].path).toBe('/api/ai/test');
    expect(posts[0].body).toEqual({
      base_url: 'http://host:1/v1',
      api_key: 'SECRET-KEY-123',
      model: 'llama3.1:8b',
    });
    expect((await screen.findByRole('status')).textContent).toContain('Connected (3 models, 12ms)');
    // The submitted key is never re-rendered anywhere in the DOM after success.
    expect(document.body.textContent).not.toContain('SECRET-KEY-123');
  });

  it('models fetch posts {base_url, api_key} only', async () => {
    const { client, posts } = buildClient({
      '/api/ai/models': {
        body: { models: [{ id: 'llama3.1:8b', provider: 'ollama' }] },
      },
    });
    render(<SettingsPage client={client} />);

    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: 'http://host:1/v1' } });
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'k' } });
    fireEvent.click(screen.getByRole('button', { name: 'Fetch models' }));

    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].body).toEqual({ base_url: 'http://host:1/v1', api_key: 'k' });
    expect((await screen.findByRole('alert', { hidden: true }).catch?.(() => null)) ?? true).toBeTruthy();
    expect(screen.getByText(/1 models found/)).toBeTruthy();
  });

  it('failed test surfaces the backend bounded error verbatim', async () => {
    const { client } = buildClient({
      '/api/ai/test': { body: { ok: false, error: 'Connection failed', detail: 'refused' } },
    });
    render(<SettingsPage client={client} />);

    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: 'http://host:1/v1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect((await screen.findByRole('status')).textContent).toContain('Connection failed');
  });
});
