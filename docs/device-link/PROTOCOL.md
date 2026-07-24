# ASDL/1 Protocol Specification

## Version
AgentState Device Link Protocol Version 1 (ASDL/1)

## Time Semantics (PRECISE)
- Pairing expiry: `age >= 120.000` → EXPIRED. `age < 120.000` → valid.
- Challenge expiry: `age >= 300.000` → EXPIRED.
- Session token expiry: `age >= 3600.000` → EXPIRED.
- All times are monotonic clock seconds, not wall-clock.
- Platforms MUST use `>=` (not `>`) for the expiry comparison.

## ECDSA Wire Format
- Signatures: **DER-encoded** (ASN.1 SEQUENCE { r, s }). Starts with `0x30`.
- NOT fixed-length raw `r||s`.
- Public keys: DER-encoded SubjectPublicKeyInfo (SPKI). Starts with `0x30`.
- Kotlin `java.security.Signature` produces DER by default.
- Python `cryptography` produces DER by default.
- Test vector: cross-platform golden vectors in `docs/device-link/CRYPTO_TEST_VECTORS.md`.

## Transport
- TLS 1.3 (mandatory)
- HTTPS for request/response APIs
- WSS for real-time events
- Desktop generates per-install self-signed TLS certificate
- Android performs certificate pinning on first pair (public key fingerprint)
- Certificate change without explicit re-pair = connection rejected

## Endpoints (Device Link Gateway — LAN only)

```
POST   /device/v1/pair/start          Start pairing session
GET    /device/v1/pair/{session_id}   Poll pairing status
POST   /device/v1/pair/confirm        SAS confirmation
POST   /device/v1/auth/challenge      Initiate session authentication
POST   /device/v1/auth/response       Submit challenge response
GET    /device/v1/status              Environment status (read-only)
GET    /device/v1/doctor              Diagnostic results (PASS/WARN/FAIL/SKIP/UNREACHABLE)
GET    /device/v1/checkpoints         List checkpoints (paginated)
GET    /device/v1/checkpoints/{id}    Single checkpoint detail
POST   /device/v1/checkpoints         Create new checkpoint
GET    /device/v1/diff                Diff against latest checkpoint
GET    /device/v1/diff/{checkpoint_id} Diff against specific checkpoint
POST   /device/v1/restore/dry-run     Dry-run restore
POST   /device/v1/restore/apply       Apply restore
GET    /device/v1/handoff             AI handoff document
GET    /device/v1/events              WSS event stream
```

## Common Headers
```
ASDL-Version: 1
X-Device-UUID: <android-uuid>
Authorization: Bearer <session-token>
```

## Device Discovery (mDNS)

Service type: `_agentstate._tcp.local`

TXT records (public, broadcast-safe):
- `uuid=<device-uuid>`
- `v=1`
- `name=<display-name>`
- `port=<gateway-port>`
- `flags=read,checkpoint`

Prohibited in TXT records:
- Token / secret / key
- Path / file name
- User name / project name
- Environment info
- State / health / version details

## Pairing Flow

1. Desktop generates pairing session (120s TTL)
2. Desktop creates QR: `agentstate://pair/<base64-session-data>`
3. Android scans QR → extracts desktop-ip, port, session-id, public-key-fingerprint
4. Android connects via TLS, verifies certificate fingerprint against QR
5. Desktop sends SAS (6-digit numeric) — displayed on both sides
6. User confirms on both devices
7. Android sends Ed25519 public key
8. Desktop stores binding: {android-uuid, public-key, fingerprint, permissions}
9. Android stores binding: {desktop-uuid, public-key, fingerprint, endpoint-hint}
10. Session token generated for immediate use

## Session Authentication (Reconnection)

1. Android discovers desktop via mDNS (UUID match)
2. Android opens TLS connection, verifies pinned certificate
3. Desktop sends random 32-byte challenge
4. Android signs `challenge || session-nonce || desktop-uuid` with Android Ed25519 private key
5. Desktop verifies signature against stored public key
6. Desktop signs `challenge || session-nonce || android-uuid` with Desktop Ed25519 private key
7. Android verifies signature against stored public key
8. Mutual authentication complete, ephemeral session token issued

## Error Codes

| Code | Meaning |
|------|---------|
| 401 | Not authenticated |
| 403 | Permission denied (revoked / not bound) |
| 404 | Resource not found |
| 409 | Conflict (already paired, SAS mismatch) |
| 410 | Pairing session expired |
| 429 | Rate limited |
| 423 | Locked (DESKTOP_CONFIRMATION_REQUIRED) |
| 426 | Protocol version mismatch |
| 503 | Device Link disabled or LAN unavailable |

## Security Requirements
- Session tokens: random, 32-byte, TTL 3600s, never in URL, never logged
- Rate limits: 5 pairing attempts per IP per 60s, 10 auth failures per 60s
- API payload limit: 1MB per request
- All timestamps UTC ISO 8601
- All device operations logged to audit chain
