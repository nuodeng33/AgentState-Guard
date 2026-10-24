/**
 * Pairing QR renderer (qrcode-generator, MIT). Renders the canonical
 * `agentstate://pair?...` URI from the backend invitation verbatim as an SVG
 * QR. The payload itself never appears as text — the QR is its only rendering.
 */

import qrcode from 'qrcode-generator';
import { useLayoutEffect, useMemo, useRef, useState } from 'react';

import { useT } from '../../i18n/I18nProvider';

export function QrCodeCard({ payload }: { payload: string }) {
  const t = useT();
  const viewportRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const [cellSize, setCellSize] = useState(5);
  const qr = useMemo(() => {
    const value = qrcode(0, 'M');
    value.addData(payload); value.make();
    return value;
  }, [payload]);
  const modules = qr.getModuleCount();
  useLayoutEffect(() => {
    const viewport = viewportRef.current, card = cardRef.current;
    if (!viewport || !card) return;
    let active = true;
    const measure = () => {
      if (!active || viewport.clientWidth <= 0) return;
      const style = getComputedStyle(card);
      const chrome = (['paddingLeft', 'paddingRight', 'borderLeftWidth', 'borderRightWidth'] as const)
        .reduce((total, key) => total + (parseFloat(style[key]) || 0), 0);
      // Four quiet modules on every side. Keep complete integer modules even
      // when a very narrow viewport must scroll instead of resampling the QR.
      setCellSize(Math.max(3, Math.min(5, Math.floor((viewport.clientWidth - chrome) / (modules + 8)))));
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(viewport);
    window.addEventListener('resize', measure);
    return () => { active = false; observer?.disconnect(); window.removeEventListener('resize', measure); };
  }, [modules]);
  const svg = qr.createSvgTag({ cellSize, margin: cellSize * 4, scalable: false });
  return (
    <div className="qr-viewport" ref={viewportRef}>
      <div className="qr-card" ref={cardRef} aria-label={t('devices.qrHint')}>
        {/* qr.createSvgTag builds the SVG from the module matrix only; no user data
            is interpolated into markup. */}
        <span className="qr-svg" dangerouslySetInnerHTML={{ __html: svg }} />
      </div>
    </div>
  );
}
