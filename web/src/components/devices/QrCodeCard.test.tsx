import { act, cleanup, fireEvent, render } from '@testing-library/react';
import qrcode from 'qrcode-generator';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { buildPairUri } from '../../api/product';
import { QrCodeCard } from './QrCodeCard';

// Vitest omits CSS imports; use the real stylesheet as explicit test input.
const { readFileSync } = await vi.importActual<{ readFileSync: (path: string, encoding: string) => string }>('node:fs');
const appCss = readFileSync('src/styles/app.css', 'utf8');

// Public P-256 generator point; deterministic non-live invitation, never authority.
const densePayload = buildPairUri({ protocol_version: 1, session_id: '44'.repeat(16),
  ticket: '55'.repeat(32), endpoint: 'https://192.168.1.20:8788', expires_in_s: 120,
  expires_at_epoch: 2000000000, desktop_uuid: '01234567-89ab-cdef-0123-456789abcdef',
  desktop_public_key_der: '3059301306072a8648ce3d020106082a8648ce3d030107034200046b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c2964fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5',
  desktop_signing_fingerprint: '5cd252fb0ce8932436faf8ccd1040981b89ee4ad6b9fe9e2a2b7e71aacb27cd3',
  tls_spki_fingerprint: '33'.repeat(32), state: 'created',
});
function observerFixture() {
  const callbacks: (() => void)[] = [];
  const disconnects: ReturnType<typeof vi.fn>[] = [];
  vi.stubGlobal('ResizeObserver', class {
    disconnect = vi.fn();
    constructor(callback: () => void) { callbacks.push(callback); disconnects.push(this.disconnect); }
    observe() {}
  });
  return { resize: () => act(() => callbacks[callbacks.length - 1]()), disconnects };
}
function setAvailableWidth(container: HTMLElement, width: number) {
  const viewport = container.querySelector<HTMLElement>('.qr-viewport')!;
  expect(viewport).not.toBeNull();
  // JSDOM has no layout or CSS-variable resolution. Supply the browser's
  // viewport measurement and actual card chrome from app.css explicitly.
  Object.defineProperty(viewport, 'clientWidth', { configurable: true, value: width });
  const card = container.querySelector<HTMLElement>('.qr-card')!;
  card.style.padding = '8px'; card.style.border = '1px solid black';
}
function expectGeometry(container: HTMLElement, cell: number, modules = 89) {
  const side = (modules + 8) * cell;
  const svg = container.querySelector('svg')!;
  expect(svg.getAttribute('width')).toBe(`${side}px`);
  expect(svg.getAttribute('height')).toBe(`${side}px`);
  expect(svg.getAttribute('viewBox')).toBe(`0 0 ${side} ${side}`);
  expect(svg.querySelector('path')!.getAttribute('d')!.startsWith(`M${cell * 4},${cell * 4}l${cell},0 0,${cell} -${cell},0 0,-${cell}z`)).toBe(true);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('dense pairing QR geometry', () => {
  it('renders the complete canonical invitation on a natural 5px grid with four-module quiet zones', () => {
    observerFixture();
    expect(densePayload.length).toBe(547);
    const { container } = render(<QrCodeCard payload={densePayload} />);
    expectGeometry(container, 5);
    expect(container.textContent).not.toContain(densePayload);
  });

  it('keeps intrinsic SVG dimensions and horizontal overflow in the actual QR styles', () => {
    observerFixture();
    const style = document.createElement('style');
    style.textContent = Array.from(appCss.matchAll(/\.qr-(?:viewport|card|svg svg)\s*\{[^}]*\}/g), match => match[0]).join('\n');
    document.head.append(style);
    try {
      const { container } = render(<QrCodeCard payload={densePayload} />);
      expectGeometry(container, 5);
      const svgStyle = getComputedStyle(container.querySelector('svg')!);
      expect(svgStyle.width).toBe(''); expect(svgStyle.height).toBe('');
      expect(svgStyle.maxWidth).toBe('none');
      expect(getComputedStyle(container.querySelector('.qr-viewport')!).overflowX).toBe('auto');
    } finally { style.remove(); }
  });

  it('fits narrower viewports using integer 4px and 3px grids without fractional SVG scaling', () => {
    const observer = observerFixture();
    const { container } = render(<QrCodeCard payload={densePayload} />);
    setAvailableWidth(container, 410); observer.resize(); expectGeometry(container, 4);
    setAvailableWidth(container, 330); observer.resize(); expectGeometry(container, 3);
    setAvailableWidth(container, 560); observer.resize(); expectGeometry(container, 5);
  });

  it('keeps the minimum 3px grid in an overflow viewport instead of shrinking a dense QR below it', () => {
    const observer = observerFixture();
    const { container } = render(<QrCodeCard payload={densePayload} />);
    setAvailableWidth(container, 200); observer.resize(); expectGeometry(container, 3);
    expect(container.querySelector('.qr-viewport')!.contains(container.querySelector('.qr-card'))).toBe(true);
  });

  it('recomputes the natural grid when the invitation changes and releases its observer', () => {
    const observer = observerFixture();
    const view = render(<QrCodeCard payload={densePayload} />);
    setAvailableWidth(view.container, 330); observer.resize(); expectGeometry(view.container, 3);
    const payload = 'agentstate://pair?v=1&fixture=non-live';
    const qr = qrcode(0, 'M'); qr.addData(payload); qr.make();
    view.rerender(<QrCodeCard payload={payload} />);
    expectGeometry(view.container, 5, qr.getModuleCount());
    expect(observer.disconnects[0]).toHaveBeenCalledOnce();
    view.unmount();
    expect(observer.disconnects[observer.disconnects.length - 1]).toHaveBeenCalledOnce();
  });

  it('still measures integer geometry on window resize when ResizeObserver is unavailable', () => {
    vi.stubGlobal('ResizeObserver', undefined);
    const { container } = render(<QrCodeCard payload={densePayload} />);
    setAvailableWidth(container, 330);
    fireEvent(window, new Event('resize'));
    expectGeometry(container, 3);
  });
});
