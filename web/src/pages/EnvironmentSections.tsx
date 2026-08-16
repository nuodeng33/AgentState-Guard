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
import { useT } from '../i18n/I18nProvider';

function checkValue(value: boolean | string | null) {
  if (typeof value === 'boolean') return <code>{String(value)}</code>;
  return orDash(value);
}

export function StatusSection({ data }: { data: StatusPayload }) {
  const t = useT();
  const checks = Object.entries(data.checks ?? {});
  const versions = Object.entries(data.versions ?? {});
  return (
    <section className="card">
      <KeyValueGrid>
        {data.timestamp_utc && <KeyValue k="timestamp_utc" v={data.timestamp_utc} />}
      </KeyValueGrid>

      {versions.length > 0 && (
        <>
          <h3 className="card-sub">{t('env.status.versions')}</h3>
          <KeyValueGrid>
            {versions.map(([name, value]) => (
              <KeyValue key={name} k={name} v={orDash(value)} />
            ))}
          </KeyValueGrid>
        </>
      )}

      {checks.length > 0 && (
        <>
          <h3 className="card-sub">{t('env.status.checks')}</h3>
          <KeyValueGrid>
            {checks.map(([name, value]) => (
              <KeyValue key={name} k={name} v={checkValue(value)} />
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
  if (checks.length === 0) {
    return null;
  }
  return (
    <section className="card">
      <ul className="env-doctor-list">
        {checks.map((row, index) => (
          <li key={`${row.check}:${index}`} className="env-doctor-row">
            <StateBadge label={row.status} tone={doctorTone(row.status)} />
            <span className="env-doctor-check">{row.check}</span>
            <span className="muted env-doctor-message">{row.message}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
