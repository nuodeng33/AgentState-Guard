/**
 * QR placeholder card. The payload string itself is never rendered as text;
 * a real QR renderer is wired in with the Device Link adapter later.
 */

import { useT } from '../../i18n/I18nProvider';

export function QrPlaceholderCard({ hasPayload }: { hasPayload: boolean }) {
  const t = useT();
  return (
    <div className="qr-placeholder" aria-label={t('devices.qr.placeholder')}>
      <svg width="44" height="44" viewBox="0 0 24 24" aria-hidden="true" className="qr-icon">
        <rect x="4" y="4" width="6" height="6" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <rect x="14" y="4" width="6" height="6" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <rect x="4" y="14" width="6" height="6" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M14 14h2v2h-2zM18 14h2v2h-2zM14 18h2v2h-2zM18 18h2v2h-2z" fill="currentColor" />
      </svg>
      <p className="qr-note">
        {hasPayload ? t('devices.qr.placeholder') : t('devices.qr.awaiting')}
      </p>
    </div>
  );
}
