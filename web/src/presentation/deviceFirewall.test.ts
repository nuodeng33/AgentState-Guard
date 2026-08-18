import { describe, expect, it } from 'vitest';

import { deviceFirewallState } from './deviceFirewall';

/**
 * The primary Device Link surface renders only a coarse, user-readable
 * firewall state. Raw backend authority fields stay in collapsed
 * diagnostics; presentation never upgrades or fabricates success.
 */
describe('deviceFirewallState', () => {
  it('no record is UNKNOWN, never a fabricated state', () => {
    expect(deviceFirewallState(null)).toBe('UNKNOWN');
    expect(deviceFirewallState(undefined)).toBe('UNKNOWN');
  });

  it('an error state needs attention regardless of operation', () => {
    expect(
      deviceFirewallState({
        operation: 'APPLY',
        status: 'ERROR',
        reason_code: 'DEVICE_FIREWALL_ELEVATION_UNAVAILABLE',
        scope_digest: null,
        recorded_at: null,
      }),
    ).toBe('ATTENTION');
  });

  it('an applied non-error apply is protection applied', () => {
    expect(
      deviceFirewallState({
        operation: 'APPLY',
        status: 'AVAILABLE',
        reason_code: 'DEVICE_FIREWALL_APPLIED',
        scope_digest: 'x'.repeat(64),
        recorded_at: '2026-08-18T00:00:00Z',
      }),
    ).toBe('APPLIED');
  });

  it('a recorded removal is not applied', () => {
    expect(
      deviceFirewallState({
        operation: 'REMOVE',
        status: 'AVAILABLE',
        reason_code: 'DEVICE_FIREWALL_REMOVED',
        scope_digest: null,
        recorded_at: null,
      }),
    ).toBe('IDLE');
  });

  it('never upcodes an unknown operation to applied', () => {
    expect(
      deviceFirewallState({
        operation: 'PROBE',
        status: 'AVAILABLE',
        reason_code: 'DEVICE_FIREWALL_APPLIED',
        scope_digest: null,
        recorded_at: null,
      }),
    ).toBe('UNKNOWN');
  });

  it('unknown machine statuses never fabricate success', () => {
    for (const status of ['PENDING', '', 'SUCCESS']) {
      expect(
        deviceFirewallState({
          operation: 'APPLY',
          status,
          reason_code: 'DEVICE_FIREWALL_APPLIED',
          scope_digest: null,
          recorded_at: null,
        }),
      ).toBe('UNKNOWN');
    }
  });
});
