# Device Link Threat Model — ASDL/1

## Trust Boundaries

```
┌─ Trust Boundary 1: Android App ──────────────────────────┐
│ Android process │ Keystore (hardware) │ Compose UI       │
└───────────────────────────────────────────────────────────┘
                    │ TLS + pinned cert + challenge-auth
                    ▼
┌─ Trust Boundary 2: Device Link Gateway ───────────────────┐
│ Minimal API │ Rate limiting │ Audit logging │ TLS term   │
└───────────────────────────────────────────────────────────┘
                    │ Internal IPC (no network)
                    ▼
┌─ Trust Boundary 3: AgentState Guard Core ─────────────────┐
│ CLI │ Storage │ Transactions │ Sanitizer │ Whitelist     │
│ Core API bound to 127.0.0.1 only                         │
└───────────────────────────────────────────────────────────┘
```

## Attacker Profiles

### A1: Same-LAN Attacker
- **Capability**: Can send packets on the same LAN subnet
- **Cannot**: Access Desktop filesystem, read memory, break TLS
- **Goal**: Impersonate Desktop or Android, read environment state

### A2: Physical Access Attacker
- **Capability**: Can touch the Android device or Desktop
- **Cannot**: Break OS-level screen lock
- **Goal**: Access environment state without unlocking

### A3: Malicious App (Android)
- **Capability**: Installed on device, tries to discover AgentState Guard
- **Goal**: Connect to Desktop, read sensitive data

### A4: Remote Internet Attacker
- **Capability**: Internet access, no LAN presence
- **Goal**: Connect to Device Link Gateway
- **Mitigation**: Gateway bound to private LAN interface only; public network = auto-refuse

### A5: Compromised Desktop
- **Capability**: Full Desktop access
- **Goal**: Exfiltrate private keys or Android binding data
- **Mitigation**: DPAPI protects private key by user session; binding data stored encrypted

## Threat Table

| # | Threat | Severity | Likelihood | Mitigation |
|---|--------|----------|------------|------------|
| T1 | A1 spoofs mDNS to impersonate Desktop | HIGH | MEDIUM | Android verifies public key fingerprint after discovery |
| T2 | A1 captures QR code before expiry | HIGH | LOW | SAS confirmation required; QR alone insufficient |
| T3 | A1 replays old SAS confirmation | MEDIUM | LOW | Pairing session nonce + expiry prevents replay |
| T4 | A1 intercepts TLS via self-signed cert swap | CRITICAL | LOW | Android certificate pinning rejects unknown certs |
| T5 | A1 replays challenge-response | MEDIUM | LOW | Session nonce changes each connection |
| T6 | A1 brute-forces SAS (6 digits) | MEDIUM | LOW | Rate limit: 3 failed SAS per pairing session |
| T7 | A2 accesses unlocked Android | MEDIUM | MEDIUM | HIGH_RISK_WRITE requires biometric/PIN |
| T8 | A2 extracts binding data from Android storage | MEDIUM | LOW | Android Keystore; private key non-exportable |
| T9 | A3 rogue app discovers mDNS service | LOW | HIGH | mDNS info is non-sensitive (UUID + name + port) |
| T10 | A3 rogue app connects without permission | HIGH | LOW | TLS auth requires Android private key; only paired device can connect |
| T11 | A4 scans for open ports from Internet | LOW | MEDIUM | Gateway only on private LAN; public network auto-refuse |
| T12 | A5 extracts Desktop private key | HIGH | LOW | Windows DPAPI key tied to user session and machine |
| T13 | Token in URL leak via logs | MEDIUM | LOW | Session tokens never in URL; log sanitization active |
| T14 | Replay of valid session token after expiry | MEDIUM | LOW | Tokens expire 3600s; token reuse within window requires challenge re-auth |
| T15 | Denial of Service via pairing flood | LOW | MEDIUM | Rate limit: 5 pair attempts/IP/60s |
| T16 | Android app decompilation extracts crypto logic | LOW | HIGH | Keystore-backed; reverse engineering reveals algorithm, not keys |
| T17 | HTTP downgrade attack | CRITICAL | LOW | Gateway rejects non-TLS connections; Android hardcodes HTTPS |
| T18 | Revoked device reconnects | CRITICAL | LOW | Desktop checks binding status on every auth; revoked = immediate 403 |

## Residual Risks

| Risk | Why Accepted | Mitigation |
|------|-------------|------------|
| QR code observed by camera | SAS step catches MITM | User must visually verify SAS |
| LAN attacker DoS via mdns flood | Non-security impact | Service auto-restarts |
| Desktop cert private key compromise | Requires DPAPI bypass | Key rotation via re-pair |
