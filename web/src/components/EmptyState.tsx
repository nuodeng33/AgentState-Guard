/**
 * Honest empty state: the backend has no authoritative records for this view.
 * The machine reason_code stays verbatim and renders only as secondary
 * diagnostics below the product-language title and detail.
 */
export function EmptyState({
  title,
  detail,
  reasonCode,
}: {
  title: string;
  detail: string;
  reasonCode?: string;
}) {
  return (
    <div className="empty-state">
      <p className="empty-state-title">{title}</p>
      <p className="empty-state-detail">{detail}</p>
      {reasonCode && (
        <p className="empty-state-diagnostics muted">
          reason_code: <code>{reasonCode}</code>
        </p>
      )}
    </div>
  );
}
