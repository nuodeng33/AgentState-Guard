# Desktop Security Architecture (Windows)

## Private Key Protection
- Ed25519 key pair generated on first Device Link enable
- Private key encrypted via Windows DPAPI (`CryptProtectData`)
  - `CRYPTPROTECT_LOCAL_MACHINE` flag = tied to this machine
  - Optional: user session scope for additional isolation
- Encrypted key stored in `%APPDATA%/AgentStateGuard/identity.dat`
- Public key stored in plaintext for mDNS inclusion
- Key rotation: delete `identity.dat` → re-enable Device Link → new keys

## Device Binding Store
- SQLite database at `%APPDATA%/AgentStateGuard/bindings.db`
- Table: `bound_devices(uuid TEXT PRIMARY KEY, pubkey BLOB, fingerprint TEXT, name TEXT, created_at TEXT, permissions TEXT, protocol_version INTEGER)`
- Access: file permissions restricted to current user

## Network Detection
- Windows Network List Manager API to detect network profile
- `NLM_CONNECTIVITY` + `NLM_DOMAIN_TYPE` to classify Private vs Public
- Public → Device Link auto-refuse with user notification
- Network change → re-validate profile, pause if Public

## mDNS Advertisement
- `_agentstate._tcp.local` service type
- TXT record: uuid, v, name, port, flags ONLY
- No tokens, secrets, paths, versions, or environment data in TXT
- Service UUID stable across restarts
- Port: configurable, default 8788

## Firewall Rule (Windows)
- Created by installer (not silently at runtime)
- Scope: Private profile only
- Program: `AgentStateGuard.exe`
- Protocol: TCP, specific port
- Direction: Inbound
- Rule name: "AgentState Guard Device Link"

## Certificate
- Self-signed X.509 certificate generated on Device Link enable
- 2048-bit RSA or Ed25519
- CN = desktop display name
- Validity: 365 days, auto-renewed
- Private key encrypted via DPAPI
- Fingerprint displayed to user for verification
