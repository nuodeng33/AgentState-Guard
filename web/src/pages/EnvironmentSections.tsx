/**
 * Bounded host projections for the Environment surface.
 *
 * Both sections render exactly what the backend returned:
 * - /api/status payload (timestamp_utc, checks, versions) as verbatim
 *   key/value rows; no verdict is derived from these facts.
 * - /api/doctor check list ({check, status, message}) with the backend's own
 *   status token carried by a badge; INFO/OK are not upgraded and
 *   UNREACHABLE/FAIL are never hidden.
 */

import { KeyValue, KeyValueGrid, orDash } from '../components/KeyValue';
import { StateBadge, type BadgeTone } from '../components/StateBadge';
import type { DoctorCheck, StatusPayload } from '../api/types';
import { useI18n } from '../i18n/I18nProvider';
import type { Locale } from '../i18n/locale';
import { productTokenDisplay } from '../presentation/productLanguage';

function checkValue(value: boolean | string | null, locale: Locale) {
  if (typeof value === 'boolean') {
    return <code>{locale === 'zh-CN' ? (value ? '是（true）' : '否（false）') : String(value)}</code>;
  }
  return orDash(value);
}

function versionName(name: string, locale: Locale): string {
  if (locale !== 'zh-CN') return name;
  return name === 'python_bundled' ? '内置 Python 运行时（python_bundled）' : name;
}

const DOCTOR_LABELS_ZH: Record<string, string> = {
  platform: '运行平台',
  'bundled-runtime': '内置运行时',
  'docker-daemon': 'Docker 服务',
  container: '产品容器',
  node: 'Node.js',
  git: 'Git',
  python: 'Python',
};

function doctorLabel(check: string, locale: Locale): string {
  return locale === 'zh-CN' ? DOCTOR_LABELS_ZH[check] ?? check : check;
}

function doctorMessage(row: DoctorCheck, locale: Locale): string {
  if (locale !== 'zh-CN') return row.message;
  const exact: Record<string, string> = {
    'Windows host detected; running Windows-native probes':
      '已检测到 Windows 主机；正在使用 Windows 原生探测。',
    'Bundled sidecar runtime active': '内置 Sidecar 运行时已启动。',
    'No product-owned container configured': '未配置由本产品管理的容器。',
    'No external Python interpreter detected on host': '未在主机上检测到外部 Python 解释器。',
    'Optional external Python interpreter not detected on host':
      '未检测到可选的外部 Python 解释器；内置运行时不受影响。',
  };
  return exact[row.message] ?? row.message;
}

export function StatusSection({ data }: { data: StatusPayload }) {
  const { locale, t } = useI18n();
  const checks = Object.entries(data.checks ?? {});
  const versions = Object.entries(data.versions ?? {}).filter(([, value]) =>
    typeof value === 'string' ? value.trim().length > 0 : value != null,
  );
  return (
    <section className="card">
      <KeyValueGrid>
        {data.timestamp_utc && (
          <KeyValue
            k={locale === 'zh-CN' ? '时间戳（timestamp_utc）' : 'timestamp_utc'}
            v={data.timestamp_utc}
          />
        )}
      </KeyValueGrid>

      {versions.length > 0 && (
        <>
          <h3 className="card-sub">{t('env.status.versions')}</h3>
          <KeyValueGrid>
            {versions.map(([name, value]) => (
              <KeyValue key={name} k={versionName(name, locale)} v={orDash(value)} />
            ))}
          </KeyValueGrid>
        </>
      )}

      {checks.length > 0 && (
        <>
          <h3 className="card-sub">{t('env.status.checks')}</h3>
          <KeyValueGrid>
            {checks.map(([name, value]) => (
              <KeyValue key={name} k={name} v={checkValue(value, locale)} />
            ))}
          </KeyValueGrid>
        </>
      )}
    </section>
  );
}

/** Map each doctor status token to a tone; the token itself is the label. */
function doctorTone(status: string): BadgeTone {
  switch (status) {
    case 'OK':
    case 'INFO':
    case 'PASS':
      return 'ok';
    case 'WARN':
    case 'SKIP':
      return 'warn';
    case 'FAIL':
    case 'UNREACHABLE':
      return 'bad';
    default:
      return 'unknown';
  }
}

export function DoctorSection({ checks }: { checks: DoctorCheck[] }) {
  const { locale } = useI18n();
  if (checks.length === 0) {
    return null;
  }
  return (
    <section className="card">
      <ul className="env-doctor-list">
        {checks.map((row, index) => (
          <li key={`${row.check}:${index}`} className="env-doctor-row">
            <StateBadge label={productTokenDisplay(row.status, locale)} tone={doctorTone(row.status)} />
            <span className="env-doctor-check">{doctorLabel(row.check, locale)}</span>
            <span className="muted env-doctor-message">{doctorMessage(row, locale)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
