import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiActionError, ApiRequestError, type ApiClient } from '../api/client';
import EnvironmentSourceControls, { type ConnectionSource } from './EnvironmentSourceControls';

afterEach(() => { cleanup(); vi.useRealTimers(); });
const source = (kind = 'WSL', running = true, connected = false): ConnectionSource => ({
  source_id: kind === 'WSL' ? 'wsl-target' : kind === 'CONTAINER' ? 'docker-target' : 'local',
  kind, label: 'Exact target', state: 'OBSERVED', reason_code: 'OBSERVED', freshness: 'FRESH',
  enabled: true, configurable: true, revision: 1, agent_count: 0, session_probe_available: true, session_status: 'AVAILABLE',
  protection: { running, connected },
});
function setup(item = source()) {
  const post = vi.fn(); const get = vi.fn(); const onChanged = vi.fn();
  const client: ApiClient = { bootstrap: vi.fn(), post, get };
  const view = render(<EnvironmentSourceControls client={client} source={item} onChanged={onChanged} />);
  return { ...view, post, get, onChanged, client };
}
function receipt(request: unknown, state = 'QUEUED', action = 'connect', id = 'wsl-target') {
  return { id: 'operation-1', kind: 'SOURCE', request_id: (request as { request_id: string }).request_id,
    state, reason_code: state === 'FAILED' ? 'DOMAIN_NOT_RUNNING' : 'PROTECTION_QUEUED', result: { source_id: id, action } };
}
const disabled = (name: string) => (screen.getByRole('button', { name }) as HTMLButtonElement).disabled;

describe('Environment source connections', () => {
  it('never sends on render; ordinary WSL connect sends only request_id and no start permission', async () => {
    const { post, onChanged } = setup();
    expect(post).not.toHaveBeenCalled();
    post.mockImplementation((_path, body) => Promise.resolve(receipt(body, 'SUCCEEDED')));
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe('/api/v1/discovery/sources/wsl-target/connect');
    expect(Object.keys(post.mock.calls[0][1])).toEqual(['request_id']);
    expect(screen.getByRole('status').textContent).toContain('succeeded');
  });

  it('stopped WSL requires a separate exact-target confirmation; cancelling sends nothing', async () => {
    const { post } = setup(source('WSL', false));
    expect(disabled('Connect')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Start and connect…' }));
    expect(screen.getByRole('group', { name: 'Confirm WSL startup' }).textContent).toContain('Exact target');
    expect(screen.getByRole('group').textContent).toContain('wsl-target');
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(post).not.toHaveBeenCalled();
    post.mockImplementation((_path, body) => Promise.resolve(receipt(body, 'QUEUED', 'start-and-connect')));
    fireEvent.click(screen.getByRole('button', { name: 'Start and connect…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm start and connect' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe('/api/v1/discovery/sources/wsl-target/start-and-connect');
    expect(Object.keys(post.mock.calls[0][1])).toEqual(['request_id']);
  });

  it('does not expose a start action for stopped containers, native, aggregate, or unknown facts', () => {
    const { rerender, client, onChanged, post } = setup(source('CONTAINER', false));
    expect(screen.getByText('DOMAIN_NOT_RUNNING')).toBeTruthy();
    expect(disabled('Connect')).toBe(true);
    expect(screen.queryByRole('button', { name: /Start and connect/ })).toBeNull();
    for (const item of [source('NATIVE'), source('DOCKER'), { ...source(), protection: undefined }, { ...source(), freshness: 'STALE' }]) {
      rerender(<EnvironmentSourceControls client={client} source={item} onChanged={onChanged} />);
      expect(screen.queryByRole('button', { name: /Start and connect/ })).toBeNull();
      const button = screen.queryByRole('button', { name: 'Disconnect' }) as HTMLButtonElement | null;
      expect(button === null || button.disabled).toBe(true);
    }
    expect(post).not.toHaveBeenCalled();
  });

  it('connects a running container and disconnects only a connected supported source', async () => {
    const { post, rerender, client, onChanged } = setup(source('CONTAINER'));
    post.mockImplementation((_path, body) => Promise.resolve(receipt(body, 'SUCCEEDED', 'connect', 'docker-target')));
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe('/api/v1/discovery/sources/docker-target/connect');
    rerender(<EnvironmentSourceControls client={client} source={source('WSL', true, true)} onChanged={onChanged} />);
    post.mockImplementation((_path, body) => Promise.resolve(receipt(body, 'SUCCEEDED', 'disconnect')));
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1][0]).toBe('/api/v1/discovery/sources/wsl-target/disconnect');
  });

  it('preserves exact request_id on unconfirmed retry and never renders raw errors', async () => {
    const { post, onChanged } = setup();
    post.mockRejectedValueOnce(new ApiRequestError('private token and commandline'));
    post.mockImplementationOnce((_path, body) => Promise.resolve(receipt(body, 'SUCCEEDED')));
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await screen.findByRole('alert');
    expect(document.body.textContent).not.toContain('private token');
    expect(onChanged).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry same submission' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[1]).toEqual(post.mock.calls[0]);
  });

  it('treats 202 as pending, checks its receipt via GET, and preserves confirmed failure', async () => {
    const { post, get, onChanged } = setup();
    let body: unknown;
    post.mockImplementation((_path, request) => { body = request; return Promise.resolve(receipt(request)); });
    get.mockImplementation(() => Promise.resolve(receipt(body, 'FAILED')));
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await screen.findByRole('button', { name: 'Check operation result' });
    expect(onChanged).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Check operation result' }));
    await screen.findByRole('button', { name: 'Close result and refresh' });
    expect(get).toHaveBeenCalledWith('/api/v1/recovery/operations/operation-1');
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.getByText('DOMAIN_NOT_RUNNING')).toBeTruthy();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('polls an accepted operation through GET only and waits for actual success', async () => {
    vi.useFakeTimers();
    const { post, get, onChanged } = setup();
    let body: unknown;
    post.mockImplementation((_path, request) => { body = request; return Promise.resolve(receipt(request)); });
    get.mockImplementation(() => Promise.resolve(receipt(body, 'SUCCEEDED')));
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Connect' })));
    expect(onChanged).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(get).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledTimes(1);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('reloads authoritative facts on 409 without silently retrying', async () => {
    const { post, onChanged } = setup();
    post.mockRejectedValue(new ApiActionError(409, 'PROTECTION_REQUEST_REUSED'));
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('button', { name: 'Retry same submission' })).toBeNull();
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('alert').textContent).toContain('PROTECTION_REQUEST_REUSED');
  });

  it('does not claim success for mismatched receipt or stale source callbacks', async () => {
    const { post, onChanged, client, rerender } = setup();
    let finish!: (value: unknown) => void;
    post.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    const request = post.mock.calls[0][1];
    rerender(<EnvironmentSourceControls client={client} source={source('CONTAINER')} onChanged={onChanged} />);
    await act(async () => finish(receipt(request, 'SUCCEEDED')));
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.queryByText(/Operation succeeded/)).toBeNull();
    post.mockImplementation((_path, body) => Promise.resolve(receipt(body, 'SUCCEEDED', 'connect', 'wrong-source')));
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await screen.findByRole('alert');
    expect(onChanged).not.toHaveBeenCalled();
  });
});
