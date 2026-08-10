/**
 * Changes shell. This build exposes no authoritative changes projection, so
 * the page says exactly that instead of fabricating activity.
 */

import { EmptyState } from '../components/EmptyState';
import { useT } from '../i18n/I18nProvider';

export default function ChangesPage() {
  const t = useT();
  return (
    <EmptyState
      title={t('changes.unavailable.title')}
      detail={t('changes.unavailable.detail')}
    />
  );
}
