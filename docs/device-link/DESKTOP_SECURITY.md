# Desktop Security Architecture (Windows)

## Authority and identity

- Core remains loopback-only on `127.0.0.1:8787`; Device Link is an independent,
  disabled-by-default TLS 1.3 listener on one verified physical private-LAN
  IPv4 at port `8788`.
- Desktop owns distinct P-256 signing and TLS keys plus a stable Desktop UUID.
  Current-user DPAPI protects the private material on packaged Windows.
- The SQLite binding registry contains only public device identity/binding
  metadata. Pairing tickets, pairing tokens, challenges, and product session
  tokens are bounded, one-use or short-lived in-memory authority.

## Pairing and rediscovery

- Pairing uses the strict `agentstate://pair` invitation and dual explicit SAS
  confirmation. Core derives and stores one SAS; loopback Desktop and Android
  receive that same value while the session is `sas_pending`.
- UDP rediscovery answers only a request naming an already-bound Desktop UUID.
  It is not generic LAN browsing and the response is not itself a trust root.
- Android reconnect still verifies the saved Desktop UUID, signing identity,
  TLS SPKI fingerprint, and authentication response.

## Network selection

- Device Link accepts exactly one RFC1918 IPv4 on an Up physical adapter with a
  default route and Windows `Private` network profile.
- Ambiguous, Public, virtual/tunnel, non-default-route, changed, or unreachable
  candidates fail closed. AgentState Guard never changes Public to Private.

## Product-owned firewall elevation

- Enabling Device Link invokes the installed Desktop executable through Windows
  `runas` only when the two ASG-owned firewall rules must be changed.
- The helper parser accepts only `apply` or `remove` plus a validated RFC1918
  address/prefix. Rule names, port `8788`, TCP/UDP protocols, inbound direction,
  exact local address/subnet, and `Private` profile are compiled constants.
- Before apply, the elevated helper re-observes the exact address/prefix,
  physical adapter, default route, and Private profile. It calls `netsh.exe`
  directly and never invokes `cmd.exe` or `Set-NetConnectionProfile`.
- Listener startup occurs only after both rules succeed. Partial apply is
  cleaned up. UAC decline, scope change, helper absence/failure, and cleanup
  failure are exposed through stable `DEVICE_FIREWALL_*` reason codes and a
  bounded firewall status containing only a scope digest and timestamp.
- This elevation boundary cannot execute arbitrary commands and is not reused
  by host workspace discovery, checkpoint, Test Restore, restore, or permission
  handling.

## Validation boundary

- Python validates path/scope/operation restrictions, reason-code projection,
  and fail-closed listener lifecycle.
- Rust source tests validate strict helper arguments, RFC1918 scope, subnet
  calculation, fixed netsh arguments, and the absence of profile mutation.
- A packaged MSI run under a standard Windows user is still required to prove
  the real UAC prompt, installed executable path, firewall mutation, cleanup,
  and physical-device reachability.
