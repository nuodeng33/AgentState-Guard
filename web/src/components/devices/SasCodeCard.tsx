/**
 * SAS confirmation card. The six-digit code always comes from the adapter —
 * it is never generated or altered here. Grouped purely for readability.
 */

import { useT } from '../../i18n/I18nProvider';

export function formatSasCode(code: string): string {
  return code.length === 6 ? `${code.slice(0, 3)} ${code.slice(3)}` : code;
}

export function SasCodeCard({
  sasCode,
  desktopName,
  secondsLeft,
}: {
  sasCode: string;
  desktopName?: string;
  secondsLeft?: number | null;
}) {
  const t = useT();
  return (
    <div className="sas-card">
      <p className="sas-prompt">{t('devices.sas.prompt')}</p>
      <p className="sas-code" aria-label={sasCode}>
        {formatSasCode(sasCode)}
      </p>
      {desktopName && (
        <p className="sas-meta">
          {t('devices.identity')}: <code>{desktopName}</code>
        </p>
      )}
      {secondsLeft !== undefined && secondsLeft !== null && (
        <p className="sas-meta">{t('devices.expires', { seconds: secondsLeft })}</p>
      )}
    </div>
  );
}
