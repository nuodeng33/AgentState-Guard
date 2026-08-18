import { useState } from 'react';

import { useT } from '../i18n/I18nProvider';

/**
 * Collapsible evidence reference list.
 *
 * Renders only the safe references the API already provides. The UI never
 * derives paths, commands, or environments from a reference. References and
 * reason-code tokens are stable machine tokens and render verbatim in every
 * locale, only as secondary diagnostics inside the toggle.
 */
export function EvidenceRefs({
  refs,
  diagnostics,
}: {
  refs: string[];
  diagnostics?: Array<string | null | undefined>;
}) {
  const [open, setOpen] = useState(false);
  const t = useT();
  const reasons = (diagnostics ?? []).filter(
    (item): item is string => typeof item === 'string' && item.length > 0,
  );

  if (refs.length === 0 && reasons.length === 0) {
    return <span className="evidence-none">{t('evidence.none')}</span>;
  }
  const count = refs.length + reasons.length;
  return (
    <div className="evidence">
      <button
        type="button"
        className="evidence-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {t('evidence.toggle', { count })}
      </button>
      {open && (
        <div className="evidence-detail">
          {reasons.map((reason) => (
            <p key={reason} className="evidence-diagnostic muted">
              reason_code: <code>{reason}</code>
            </p>
          ))}
          {refs.length > 0 && (
            <ul className="evidence-list">
              {refs.map((ref) => (
                <li key={ref}>
                  <code>{ref}</code>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
