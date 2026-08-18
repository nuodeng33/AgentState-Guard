/**
 * Shared DEGRADED / UNKNOWN panels for the authoritative views. Copy comes
 * from the active locale; the reason_code stays a verbatim machine token and
 * renders only as secondary diagnostics. Neither panel ever presents the
 * view as healthy.
 */

import { useT } from '../i18n/I18nProvider';

export function DegradedPanel({ label, reasonCode }: { label: string; reasonCode: string }) {
  const t = useT();
  return (
    <div className="panel panel-bad" role="alert">
      <p className="panel-title">{t('state.degraded.title', { label })}</p>
      <p className="panel-body">{t('state.degraded.body', { label })}</p>
      <p className="panel-diagnostics muted">
        reason_code: <code>{reasonCode}</code>
      </p>
    </div>
  );
}

export function UnknownPanel({ label, reasonCode }: { label: string; reasonCode: string }) {
  const t = useT();
  return (
    <div className="panel panel-warn" role="alert">
      <p className="panel-title">{t('state.unknown.title', { label })}</p>
      <p className="panel-body">{t('state.unknown.body', { label })}</p>
      <p className="panel-diagnostics muted">
        reason_code: <code>{reasonCode}</code>
      </p>
    </div>
  );
}
