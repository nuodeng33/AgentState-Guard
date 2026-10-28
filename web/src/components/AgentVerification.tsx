import type { VerificationState } from '../api/types';
import { useI18n } from '../i18n/I18nProvider';

/** A snapshot is a dated observation, never authority to perform an operation. */
export function AgentVerification({ verification }: { verification?: VerificationState }) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  if (!verification) return null;
  const messages = {
    PENDING: zh ? '正在进行首次记录校验，仍可继续查看工作。' : 'Verifying records for the first time. Your work remains available.',
    VERIFIED: zh ? '最近已验证状态' : 'Last verified state',
    STALE: zh ? '正在补充校验，暂时显示最近已验证的状态。' : 'Showing the last verified state while verification catches up.',
    INVALID: zh ? '记录完整性校验失败，当前观测不可用。' : 'Record integrity verification failed. Current observations are unavailable.',
    ERROR: zh ? '暂时无法完成校验，稍后会自动重试。' : 'Verification is temporarily unavailable. It will retry automatically.',
  };
  const verifiedTime = verification.verified_at && Number.isFinite(Date.parse(verification.verified_at)) ? verification.verified_at : null;
  return <div className="agent-verification" role={verification.state === 'INVALID' || verification.state === 'ERROR' ? 'alert' : 'status'}>
    <p>{messages[verification.state] ?? (zh ? '校验状态未知。' : 'Verification status is unknown.')}</p>
    {verifiedTime && <p><span>{zh ? '最近验证：' : 'Last verified:'}</span>{' '}<time dateTime={verifiedTime}>{new Date(verifiedTime).toLocaleString(locale)}</time></p>}
    {verification.refreshing && verification.state !== 'PENDING' && <p>{zh ? '后台正在校验。' : 'Verification is running in the background.'}</p>}
    <p className="muted">{zh ? '执行关联、审批或恢复时会重新校验。' : 'Linking, approvals and recovery are verified again when performed.'}</p>
  </div>;
}
