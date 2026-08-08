import { useState } from 'react';

/**
 * Collapsible evidence reference list.
 *
 * Renders only the safe references the API already provides. The UI never
 * derives paths, commands, or environments from a reference.
 */
export function EvidenceRefs({ refs }: { refs: string[] }) {
  const [open, setOpen] = useState(false);

  if (refs.length === 0) {
    return <span className="evidence-none">No evidence refs</span>;
  }
  return (
    <div className="evidence">
      <button
        type="button"
        className="evidence-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        Evidence ({refs.length})
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
