import type { AgentItem } from '../api/types';
import { useT } from '../i18n/I18nProvider';

/** Count only backend-confirmed current observations, never installed entries. */
export function AgentObservationSummary({ items, status, reasonCode }: { items: AgentItem[]; status: string; reasonCode: string }) {
  const t = useT();
  const running = items.filter(item => item.lifecycle === 'RUNNING'
    && item.presence === 'CURRENT' && item.freshness === 'FRESH').length;
  const coverageLimited = status === 'DEGRADED'
    && ['R4_AGENT_DISCOVERY_PARTIAL', 'R4_AGENT_DISCOVERY_UNREACHABLE'].includes(reasonCode);
  return <div className="status-card-meta muted">
    <p>{t('agents.observedRunning', { count: running })}</p>
    <p>{t('agents.observationScope', { count: items.length })}</p>
    {coverageLimited && <p>{t('agents.partialCoverage')}</p>}
  </div>;
}
