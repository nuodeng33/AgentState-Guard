/** Honest empty state: the backend has no authoritative records for this view. */
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
        <p className="empty-state-reason">
          reason_code: <code>{reasonCode}</code>
        </p>
      )}
    </div>
  );
}
