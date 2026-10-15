/**
 * Pairing QR renderer (qrcode-generator, MIT). Renders the canonical
 * `agentstate://pair?...` URI from the backend invitation verbatim as an SVG
 * QR. The payload itself never appears as text — the QR is its only rendering.
 */

import qrcode from 'qrcode-generator';

import { useT } from '../../i18n/I18nProvider';

export function QrCodeCard({ payload }: { payload: string }) {
  const t = useT();
  const qr = qrcode(0, 'M');
  qr.addData(payload);
  qr.make();
  const svg = qr.createSvgTag({ cellSize: 5, margin: 8, scalable: true });
  return (
    <div className="qr-card" aria-label={t('devices.qrHint')}>
      {/* qr.createSvgTag builds the SVG from the module matrix only; no user data
          is interpolated into markup. */}
      <span className="qr-svg" dangerouslySetInnerHTML={{ __html: svg }} />
    </div>
  );
}
