import type { Locale } from './locale';

/** Browser-local time for display only; callers retain the original timestamp. */
export function formatDateTime(value: string | undefined, locale: Locale): string {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return locale === 'zh-CN' ? '时间未知' : 'Time unknown';
  return new Intl.DateTimeFormat(locale, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}
