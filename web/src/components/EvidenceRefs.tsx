import { useState } from 'react';

import { useT } from '../i18n/I18nProvider';

/**
 * Collapsible evidence reference list.
 *
 * Renders only the safe references the API already provides. The UI never
 * derives paths, commands, or environments from a reference. References are
 * stable machine tokens and render verbatim in every locale.
 */
export function EvidenceRefs({ refs }: { refs: string[] }) {
  const [open, setOpen] = useState(false);
  const t = useT();

  if (refs.length === 0) {
    return <span className="evidence-none">{t('evidence.none')}</span>;
  }
  return (
    <div className="evidence">
      <button
        type="button"
        className="evidence-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {t('evidence.toggle', { count: refs.length })}
      </button>
      {open && (
        <ul className="evidence-list">
          {refs.map((ref) => (
            <li key={ref}>
              <code>{ref}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
