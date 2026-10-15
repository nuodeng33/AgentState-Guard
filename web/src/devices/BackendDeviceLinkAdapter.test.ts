/**
 * Production pairing adapter tests: exact endpoint/body mapping for the
 * frozen device-link contract and verbatim state passthrough. No SAS or
 * secrets are fabricated here.
 */

import { describe, expect, it, vi } from 'vitest';

import { createApiClient } from '../api/client';
import { BackendDeviceLinkAdapter } from './BackendDeviceLinkAdapter';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const INVITATION = {
  protocol_version: 1,
  session_id: 'ab'.repeat(16),
  ticket: 'cd'.repeat(32),
  endpoint: 'https://192.168.1.42:8788',
  expires_in_s: 120,
  expires_at_epoch: 1_800_000_000,
  desktop_uuid: 'uuid-desktop-1',
  desktop_public_key_der: 'deadbeef',
  desktop_signing_fingerprint: 'ab'.repeat(32),
  tls_spki_fingerprint: 'cd'.repeat(32),
  state: 'created',
};

interface Recorded {
  path: string;
  method: string;
  body: unknown;
}

function buildPairingFetch(routes: Record<string, { body: unknown; status?: number }>, posts: Recorded[]) {
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === '/api/session') return json({ token: 't' });
    const method = init?.method ?? 'GET';
    if (method === 'POST') posts.push({ path, method, body: JSON.parse(String(init?.body ?? '{}')) });
    const route = routes[path];
    if (!route) return json({ error: 'no route' }, 404);
    return json(route.body, route.status ?? 200);
  });
  return createApiClient(fetchImpl as unknown as typeof fetch);
}

describe('BackendDeviceLinkAdapter', () => {
  it('startPairing posts exactly {} and builds the canonical QR + expiry from the invitation', async () => {
    const posts: Recorded[] = [];
    const client = buildPairingFetch({ '/api/v1/device-link/pairings': { body: INVITATION } }, posts);
    const adapter = new BackendDeviceLinkAdapter(client);

    const state = await adapter.startPairing();
    expect(posts.length).toBe(1);
    expect(posts[0].body).toEqual({});
    expect(state.phase).toBe('PAIRING_CREATED');
    expect(state.qrPayload).toContain('agentstate://pair?');
    expect(state.qrPayload).toContain('v=1');
    expect(state.qrPayload).toContain('sid=' + INVITATION.session_id);
    expect(state.qrPayload).toContain('ticket=' + INVITATION.ticket);
    expect(state.qrPayload).not.toContain('api_key');
    expect(state.expiresAt).toBe(INVITATION.expires_at_epoch * 1000);
  });

  it('a disabled link reports DEVICE_LINK_UNSUPPORTED honestly', async () => {
    const posts: Recorded[] = [];
    const client = buildPairingFetch(
      {
        '/api/v1/device-link/pairings': {
          body: { schema_version: 'device-link-lifecycle-1', status: 'UNCHANGED', reason_code: 'DEVICE_LINK_DISABLED' },
          status: 409,
        },
      },
      posts,
    );
    const adapter = new BackendDeviceLinkAdapter(client);

    await expect(adapter.startPairing()).rejects.toMatchObject({ code: 'DEVICE_LINK_UNSUPPORTED' });
  });

  it('poll maps backend states verbatim; 404 maps to EXPIRED with PAIR_SESSION_NOT_FOUND', async () => {
    const posts: Recorded[] = [];
    const client = buildPairingFetch(
      {
        [`/api/v1/device-link/pairings/${INVITATION.session_id}`]: {
          body: { session_id: INVITATION.session_id, state: 'sas_pending' },
        },
      },
      posts,
    );
    const adapter = new BackendDeviceLinkAdapter(client);

    const pending = await adapter.pollPairing(INVITATION.session_id);
    expect(pending.phase).toBe('SAS_PENDING');

    const gone = buildPairingFetch(
      {
        [`/api/v1/device-link/pairings/${INVITATION.session_id}`]: {
          body: { schema_version: 'device-link-pairing-1', status: 'UNCHANGED', reason_code: 'PAIR_SESSION_NOT_FOUND' },
          status: 404,
        },
      },
      posts,
    );
    const adapter2 = new BackendDeviceLinkAdapter(gone);
    const expired = await adapter2.pollPairing(INVITATION.session_id);
    expect(expired.phase).toBe('EXPIRED');
    expect(expired.reasonCode).toBe('PAIR_SESSION_NOT_FOUND');
  });

  it('confirm posts {confirm:true}; reject posts {confirm:false}', async () => {
    const posts: Recorded[] = [];
    const client = buildPairingFetch(
      {
        [`/api/v1/device-link/pairings/${INVITATION.session_id}/confirm`]: {
          body: { session_id: INVITATION.session_id, state: 'confirmed_both' },
        },
      },
      posts,
    );
    const adapter = new BackendDeviceLinkAdapter(client);

    const confirmed = await adapter.confirmSas(INVITATION.session_id);
    expect(confirmed.phase).toBe('CONFIRMING');
    expect(posts[0].body).toEqual({ confirm: true });

    const rejected = await adapter.rejectSas(INVITATION.session_id);
    expect(posts[1].body).toEqual({ confirm: false });
    expect(rejected.phase).toBe('CONFIRMING'); // 'confirmed_both' state maps to CONFIRMING here
  });

  it('failure on confirm surfaces the typed reason code, never raw text', async () => {
    const posts: Recorded[] = [];
    const client = buildPairingFetch(
      {
        [`/api/v1/device-link/pairings/${INVITATION.session_id}/confirm`]: {
          body: { schema_version: 'device-link-pairing-1', status: 'UNCHANGED', reason_code: 'PAIR_STATE_CONFLICT' },
          status: 409,
        },
      },
      posts,
    );
    const adapter = new BackendDeviceLinkAdapter(client);

    const state = await adapter.confirmSas(INVITATION.session_id);
    expect(state.phase).toBe('ERROR');
    expect(state.reasonCode).toBe('PAIR_STATE_CONFLICT');
  });
});
