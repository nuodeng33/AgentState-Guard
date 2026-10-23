/** Existing provider settings request contract and stale-result behavior. */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApiClient, type ApiClient } from '../api/client';
import { SettingsAiSection } from './SettingsAiSection';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
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
      return route ? json(route.body, route.status ?? 200) : json({ error: 'unexpected' }, 404);
    }
    return json({ error: 'unexpected' }, 404);
  });
  return { client: createApiClient(fetchImpl as unknown as typeof fetch), posts };
}

function fill() {
  fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: 'http://fixture:1/v1' } });
  fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'fixture-memory-token' } });
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'fixture-model' } });
}

afterEach(cleanup);

describe('Settings AI provider section', () => {
  it('keeps the three-field request and truthfully labels connection/model validation', async () => {
    const { client, posts } = buildClient({ '/api/ai/test': { body: { ok: true, latency_ms: 12, models_available: 3 } } });
    render(<SettingsAiSection client={client} />);
    fill();
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect((await screen.findByRole('status')).textContent).toContain('Connection and model validated');
    expect(screen.getByRole('status').textContent).toContain('generation not tested');
    expect(posts).toEqual([{ path: '/api/ai/test', body: {
      base_url: 'http://fixture:1/v1', api_key: 'fixture-memory-token', model: 'fixture-model',
    } }]);
    expect(document.body.textContent).not.toContain('fixture-memory-token');
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'another-model' } });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('requires a selected model before testing and fetches models with only endpoint/key', async () => {
    const { client, posts } = buildClient({ '/api/ai/models': { body: { models: [{ id: 'fixture-model', provider: 'custom' }] } } });
    render(<SettingsAiSection client={client} />);
    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: 'http://fixture:1/v1' } });
    expect((screen.getByRole('button', { name: 'Test connection' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Fetch models' }));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].body).toEqual({ base_url: 'http://fixture:1/v1', api_key: '' });
    expect(await screen.findByText(/1 models found/)).toBeTruthy();
    expect(document.querySelector('datalist option')?.getAttribute('value')).toBe('fixture-model');
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'changed-fixture-token' } });
    expect(document.querySelector('datalist')).toBeNull();
    expect(screen.queryByText(/models found/)).toBeNull();
  });

  it('renders explicit model validation failures and clears them after editing', async () => {
    const { client } = buildClient({ '/api/ai/test': { body: { ok: false, error: 'AI_MODEL_UNAVAILABLE' } } });
    render(<SettingsAiSection client={client} />);
    fill();
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect((await screen.findByRole('status')).textContent).toContain('not in the provider model list');
    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: 'http://another-fixture:1/v1' } });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('locks all provider fields during requests and ignores synthetic edits', async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise<unknown>(done => { resolve = done; });
    const post = vi.fn(() => pending);
    const client = { post } as unknown as ApiClient;
    render(<SettingsAiSection client={client} />);
    fill();
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    for (const name of [/Base URL/, 'API key', 'Model']) {
      expect((screen.getByLabelText(name) as HTMLInputElement).disabled).toBe(true);
    }
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'stale-model' } });
    expect((screen.getByLabelText('Model') as HTMLInputElement).value).toBe('fixture-model');
    resolve({ ok: true, latency_ms: 4, models_available: 1 });
    await screen.findByRole('status');
    expect((screen.getByLabelText('Model') as HTMLInputElement).disabled).toBe(false);
  });

  it('shows model-fetch errors without echoing unrecognized upstream text', async () => {
    const { client } = buildClient({ '/api/ai/models': { body: { models: [], error: 'PRIVATE-UPSTREAM-BODY fixture-memory-token' } } });
    render(<SettingsAiSection client={client} />);
    fill();
    fireEvent.click(screen.getByRole('button', { name: 'Fetch models' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Connection failed');
    expect(document.body.textContent).not.toContain('PRIVATE-UPSTREAM-BODY');
    expect(document.body.textContent).not.toContain('fixture-memory-token');
  });
});
