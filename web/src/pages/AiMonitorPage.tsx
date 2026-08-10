/**
 * AI Monitor shell. No provider configuration is read here and nothing is
 * analyzed; the page honestly reports the not-configured state.
 */

import { EmptyState } from '../components/EmptyState';
import { useT } from '../i18n/I18nProvider';

export default function AiMonitorPage() {
  const t = useT();
  return (
    <EmptyState
      title={t('aiMonitor.unavailable.title')}
      detail={t('aiMonitor.unavailable.detail')}
    />
  );
}
