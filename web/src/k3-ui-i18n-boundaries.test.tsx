/**
 * Independent K3 boundary characterization: real App/Devices wiring, locale
 * presentation versus machine values, explicit SAS confirmation, and the
 * deterministic supervision policy gate.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';
import type { ApiClient } from './api/client';
import type { SupervisionItem, SupervisionView } from './api/types';
import type { DeviceLinkAdapter } from './devices/DeviceLinkAdapter';
import { I18nProvider } from './i18n/I18nProvider';
import { LANGUAGE_PREFERENCE_KEY } from './i18n/locale';
import DevicesPage from './pages/DevicesPage';
import { SupervisionViewBody } from './pages/SupervisionPage';

const EVIDENCE_SHA = '0123456789abcdef0123456789abcdef01234567';

let initialDocumentLanguage = '';

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
  });
}

function emptyView(view: 'runtime' | 'agents' | 'supervision' | 'recovery'): Record<string, unknown> {
  const base = {
    schema_version: 'r4-p8-1',
    view,
    status: 'EMPTY',
    reason_code: 'R4_STATE_EMPTY',
    evidence_refs: [],
    items: [],
  };
  return view === 'recovery'
    ? {
        ...base,
        recovery_level: 'R0',
        r1_verified: false,
        r2_verified: false,
        r3_verified: false,
        test_restore_status: 'NOT_RUN_P6',
        trusted_baseline_status: 'NONE',
        trusted_baseline_id: null,
      }
    : base;
}

function supervisionView(item: SupervisionItem): SupervisionView {
  return {
    schema_version: 'r4-p8-1',
    view: 'supervision',
    status: 'AVAILABLE',
    reason_code: 'R4_POLICY_CONFLICT',
    evidence_refs: [EVIDENCE_SHA],
    items: [item],
  };
}

function conflictItem(overrides: Partial<SupervisionItem> = {}): SupervisionItem {
  return {
    supervision_session_id: 'session-k3-independent',
    status: 'AWAITING_APPROVAL',
    policy_decision: 'BLOCK',
    manual_approval: false,
    requires_manual_approval: true,
    requires_checkpoint: false,
    ai_assessment: { decision: 'ALLOW', severity: 'advisory' },
    recovery_facts: null,
    action_ref: 'a'.repeat(64),
    evidence_refs: [],
    ...overrides,
  };
}

beforeEach(() => {
  initialDocumentLanguage = document.documentElement.lang;
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  document.documentElement.lang = initialDocumentLanguage;
  vi.unstubAllGlobals();
});

describe('K3 UI/I18N boundaries', () => {
  it('reaches Devices through App wiring with only bounded backend reads and an honest unpaired state', async () => {
    const fetchCalls: string[] = [];
    vi.stubGlobal('fetch', (input: RequestInfo | URL) => {
      const path = String(input);
      fetchCalls.push(path);
      if (path === '/api/session') return Promise.resolve(json({ token: 'k3-boundary-token' }));
      if (path === '/api/readiness') {
        return Promise.resolve(json({ status: 'ready', database: 'available', reason_code: 'RUNTIME_READY' }));
      }
      const match = path.match(/^\/api\/v1\/(runtime|agents|supervision|recovery)$/);
      if (match) return Promise.resolve(json(emptyView(match[1] as 'runtime' | 'agents' | 'supervision' | 'recovery')));
      return Promise.resolve(new Response('not found', { status: 404 }));
    });

    render(
      <I18nProvider systemLanguage="en-US">
        <App />
      </I18nProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Devices' }));

    expect(screen.getByRole('button', { name: 'Devices' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('heading', { level: 1, name: 'Devices' })).toBeTruthy();
    expect(await screen.findByRole('heading', { level: 2, name: 'Mobile Devices' })).toBeTruthy();
    expect(await screen.findByText('No mobile device connected')).toBeTruthy();
    expect(screen.queryByText(/not wired into this build/)).toBeNull();
    expect(screen.queryByText('Device paired')).toBeNull();
    expect(screen.queryByText('123 456')).toBeNull();

    await waitFor(() => expect(fetchCalls).toContain('/api/readiness'));
    expect(new Set(fetchCalls)).toEqual(
      new Set([
        '/api/session',
        '/api/readiness',
        '/api/v1/runtime',
        '/api/v1/agents',
        '/api/v1/supervision',
        '/api/v1/recovery',
      ]),
    );
    expect(fetchCalls.every((path) => [
      '/api/session',
      '/api/readiness',
      '/api/v1/runtime',
      '/api/v1/agents',
      '/api/v1/supervision',
      '/api/v1/recovery',
    ].includes(path))).toBe(true);
  });

  it('localizes human supervision labels while preserving machine values byte-for-byte', () => {
    const data = supervisionView(conflictItem());
    window.localStorage.setItem(LANGUAGE_PREFERENCE_KEY, 'en-US');
    const english = render(
      <I18nProvider systemLanguage="zh-CN">
        <SupervisionViewBody data={data} />
      </I18nProvider>,
    );

    expect(document.documentElement.lang).toBe('en-US');
    expect(screen.getByText('Requires manual approval')).toBeTruthy();
    expect(screen.getByText('BLOCK')).toBeTruthy();
    expect(screen.getByText('ALLOW')).toBeTruthy();
    expect(screen.getByText('R4_POLICY_CONFLICT')).toBeTruthy();
    expect(screen.getByText('session-k3-independent')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Evidence (1)' }));
    expect(screen.getByText(EVIDENCE_SHA)).toBeTruthy();

    english.unmount();
    window.localStorage.setItem(LANGUAGE_PREFERENCE_KEY, 'zh-CN');
    render(
      <I18nProvider systemLanguage="en-US">
        <SupervisionViewBody data={data} />
      </I18nProvider>,
    );

    expect(document.documentElement.lang).toBe('zh-CN');
    expect(screen.getByText('需要人工审批')).toBeTruthy();
    expect(screen.getByText('BLOCK')).toBeTruthy();
    expect(screen.getByText('ALLOW')).toBeTruthy();
    expect(screen.getByText('R4_POLICY_CONFLICT')).toBeTruthy();
    expect(screen.getByText('session-k3-independent')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '证据（1 条）' }));
    expect(screen.getByText(EVIDENCE_SHA)).toBeTruthy();
  });

  it('keeps SAS pending until the user explicitly confirms the matching code', async () => {
    const confirmCalls: string[] = [];
    const adapter: DeviceLinkAdapter = {
      listDevices: async () => [],
      startPairing: async () => ({ phase: 'SAS_PENDING', pairingId: 'pair-k3', sasCode: '123456' }),
      pollPairing: async () => ({ phase: 'SAS_PENDING', pairingId: 'pair-k3', sasCode: '123456' }),
      confirmSas: async (pairingId) => {
        confirmCalls.push(pairingId);
        return { phase: 'CONFIRMING', pairingId };
      },
      rejectSas: async () => ({ phase: 'REJECTED', pairingId: 'pair-k3' }),
      cancelPairing: async () => {},
    };

    render(<DevicesPage adapter={adapter} pollIntervalMs={10_000} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add mobile device' }));

    expect(await screen.findByText('123 456')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Codes match' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Codes do not match — cancel' })).toBeTruthy();
    expect(confirmCalls).toEqual([]);
    expect(screen.queryByText('Device paired')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Codes match' }));
    await waitFor(() => expect(confirmCalls).toEqual(['pair-k3']));
  });

  it('does not elevate a BLOCK policy to an actionable decision from AI advice', () => {
    const postCalls: Array<{ path: string; body: unknown }> = [];
    const changedCalls: string[] = [];
    const client: ApiClient = {
      bootstrap: async () => {},
      get: async <T,>() => supervisionView(conflictItem()) as T,
      post: async (path, body) => {
        postCalls.push({ path, body });
        return {} as never;
      },
    };

    render(
      <SupervisionViewBody
        data={supervisionView(conflictItem())}
        client={client}
        onChanged={() => changedCalls.push('changed')}
      />,
    );

    expect(screen.getByText('BLOCK')).toBeTruthy();
    expect(screen.getByText('ALLOW')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Approve Once' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull();
    expect(postCalls).toEqual([]);
    expect(changedCalls).toEqual([]);
  });
});
