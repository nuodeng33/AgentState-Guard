// @vitest-environment node

import { describe, expect, it } from 'vitest';

import viteConfig from '../../vite.config';
import { resolveApiUrl } from './url';

const PACKAGED_TAURI_LOCATION = {
  protocol: 'http:',
  hostname: 'tauri.localhost',
};

describe('resolveApiUrl', () => {
  it('keeps API paths relative in development so the Vite proxy remains authoritative', () => {
    expect(resolveApiUrl('/api/session', true)).toBe('/api/session');
    expect(resolveApiUrl('/api/v1/runtime?refresh=1', true)).toBe(
      '/api/v1/runtime?refresh=1',
    );
  });

  it('targets the fixed loopback Core origin in a packaged production build', () => {
    expect(resolveApiUrl('/api/session', false, PACKAGED_TAURI_LOCATION)).toBe(
      'http://127.0.0.1:8787/api/session',
    );
    expect(resolveApiUrl('/api/v1/runtime', false, PACKAGED_TAURI_LOCATION)).toBe(
      'http://127.0.0.1:8787/api/v1/runtime',
    );
  });

  it('keeps production API paths relative when Core serves the web bundle itself', () => {
    expect(
      resolveApiUrl('/api/session', false, {
        protocol: 'http:',
        hostname: '127.0.0.1',
      }),
    ).toBe('/api/session');
  });
});

describe('development API routing', () => {
  it('serves the Tauri devUrl port and proxies API paths to loopback Core', () => {
    const config = viteConfig as Exclude<typeof viteConfig, (...args: never[]) => unknown>;

    expect(config.server?.port).toBe(5173);
    expect(config.server?.strictPort).toBe(true);
    expect(config.server?.proxy?.['/api']).toBe('http://127.0.0.1:8787');
  });
});
