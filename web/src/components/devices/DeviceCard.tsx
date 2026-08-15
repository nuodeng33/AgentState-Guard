/** A linked mobile device as a quiet card; the id is a machine token shown verbatim. */

import type { LinkedDevice } from '../../devices/types';

export function DeviceCard({ device }: { device: LinkedDevice }) {
  return (
    <section className="card">
      <div className="card-head">
        <span className="card-title">{device.displayName}</span>
      </div>
      <p className="device-id">
        <code>{device.id}</code>
      </p>
    </section>
  );
}
