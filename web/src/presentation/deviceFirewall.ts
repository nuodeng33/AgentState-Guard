/**
 * Product-facing firewall state for the Device Link surface.
 *
 * Backend authority never changes: the raw firewall DTO fields
 * (operation/status/reason_code/scope_digest/recorded_at) stay available
 * verbatim for collapsed secondary diagnostics. The primary UI renders only
 * this coarse, user-readable state — never machine reason codes.
 *
 * Semantics are strictly bounded to the backend's recorded vocabulary
 * (network.py): status ∈ {NOT_RUN, AVAILABLE, ERROR}, operation ∈
 * {NONE, APPLY, REMOVE}. Any combination outside that vocabulary stays
 * UNKNOWN — presentation never upgrades, infers, or fabricates success.
 */

import type { DeviceLinkStatus } from '../api/types';
import type { MessageKey } from '../i18n/messages';

export type DeviceFirewallState = 'APPLIED' | 'IDLE' | 'ATTENTION' | 'UNKNOWN';

type FirewallRecord = NonNullable<DeviceLinkStatus['firewall']>;

export function deviceFirewallState(firewall: FirewallRecord | null | undefined): DeviceFirewallState {
  if (!firewall || typeof firewall.status !== 'string') return 'UNKNOWN';
  const status = firewall.status.toUpperCase();
  const operation = typeof firewall.operation === 'string' ? firewall.operation.toUpperCase() : '';
  if (status === 'ERROR') return 'ATTENTION';
  if (status === 'AVAILABLE') {
    if (operation === 'APPLY') return 'APPLIED';
    if (operation === 'REMOVE' || operation === 'NONE') return 'IDLE';
    return 'UNKNOWN';
  }
  if (status === 'NOT_RUN') return 'IDLE';
  return 'UNKNOWN';
}

export function deviceFirewallLabelKey(state: DeviceFirewallState): MessageKey {
  const labels: Record<DeviceFirewallState, MessageKey> = {
    APPLIED: 'devices.firewall.stateApplied',
    IDLE: 'devices.firewall.stateIdle',
    ATTENTION: 'devices.firewall.stateAttention',
    UNKNOWN: 'devices.firewall.stateUnknown',
  };
  return labels[state];
}
