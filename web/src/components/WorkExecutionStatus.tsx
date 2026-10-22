import { useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../api/client';
import { executionBase, runIsActive, type RunStatus } from '../api/execution';
import { useI18n } from '../i18n/I18nProvider';
import { formatDateTime } from '../i18n/format';

export type ExecutionTarget = { sessionId: string; runId: string };
type Item = { id: string; session_id: string; state: RunStatus; provider: string; requirement_version: number; updated_at: string };
type Summary = { work_id: string; requirement_version: number; work_revision: number;
  counts: Record<string, number>; items: Item[]; has_more: boolean; observed_at: string };

export default function WorkExecutionStatus({ client, workId, visible, onOpen, onRequirement }: {
  client: ApiClient; workId: string; visible: boolean;
  onOpen: (target?: ExecutionTarget) => void; onRequirement: (version: number) => void;
}) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const [summary, setSummary] = useState<Summary | null>(null);
  const [failed, setFailed] = useState(false); const [retry, setRetry] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const callback = useRef(onRequirement); callback.current = onRequirement;
  useEffect(() => {
    if (!visible) return;
    let live = true, inFlight = false; let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (!live || document.hidden || inFlight) return;
      inFlight = true;
      try {
        const value = await client.get<Summary>(`${executionBase(workId)}/execution-summary`);
        if (value.work_id !== workId || !Array.isArray(value.items) || !value.counts ||
          !Number.isSafeInteger(value.requirement_version) || value.requirement_version < 1) throw new Error('Invalid execution summary');
        if (live) { setSummary(value); setFailed(false); callback.current(value.requirement_version); }
      } catch { if (live) setFailed(true); }
      finally { inFlight = false; if (live && !document.hidden) timer = setTimeout(() => void poll(), 3000); }
    };
    const visibility = () => { if (timer) clearTimeout(timer); if (!document.hidden) void poll(); };
    document.addEventListener('visibilitychange', visibility); void poll();
    return () => { live = false; if (timer) clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [client, workId, visible, retry]);
  const counts = summary?.counts ?? {};
  const attention = (counts.WAITING_INPUT ?? 0) + (counts.WAITING_APPROVAL ?? 0) +
    (counts.UNCERTAIN ?? 0) + (counts.FAILED ?? 0) + (counts.INTERRUPTED ?? 0);
  const active = Object.entries(counts).reduce((n, [state, count]) => n + (runIsActive(state as RunStatus) ? count : 0), 0);
  if (!attention && !active && !failed) return null;
  const label = (state: string) => ({ WAITING_INPUT: zh ? '等待回答' : 'Needs input', WAITING_APPROVAL: zh ? '等待审批' : 'Needs approval',
    UNCERTAIN: zh ? '状态待确认' : 'State unknown', FAILED: zh ? '执行失败' : 'Failed', INTERRUPTED: zh ? '执行中断' : 'Interrupted',
    CANCELLING: zh ? '正在停止' : 'Stopping', FINALIZING: zh ? '收集结果' : 'Collecting results' }[state] ?? (zh ? '执行中' : 'Active'));
  return <section className="work-execution-status" aria-label={zh ? '工作执行状态' : 'Work execution status'}>
    <button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
      {!summary ? (zh ? '执行状态暂不可用' : 'Execution status unavailable') : attention ? (zh ? `${attention} 项执行需要处理` : `${attention} ${attention === 1 ? 'execution needs' : 'executions need'} attention`) : (zh ? `${active} 项执行进行中` : `${active} active ${active === 1 ? 'execution' : 'executions'}`)}
    </button>
    {failed && <span role="status">{zh ? '状态更新失败，显示上次结果' : 'Status update failed; last known results'} <button type="button" onClick={() => setRetry(value => value + 1)}>{zh ? '刷新状态' : 'Refresh status'}</button></span>}
    {expanded && <div className="work-execution-items">
      {summary?.items.map(item => <button type="button" key={item.id} onClick={() => { setExpanded(false); onOpen({ sessionId: item.session_id, runId: item.id }); }}>
        {item.provider} · {label(item.state)} · {item.id.slice(0, 8)}
        <small>{zh ? '要求' : 'Requirement'} v{item.requirement_version} · {zh ? '最后活动' : 'Last activity'} <time dateTime={item.updated_at} title={item.updated_at}>{formatDateTime(item.updated_at, locale)}</time></small>
      </button>)}
      {summary?.has_more && <button type="button" onClick={() => { setExpanded(false); onOpen(); }}>{zh ? '更多执行（列表已截取）' : 'More executions (list limited)'}</button>}
    </div>}
  </section>;
}
