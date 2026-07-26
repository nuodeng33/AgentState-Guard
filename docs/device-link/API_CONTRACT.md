# Device Link Runtime API Contract — P0-A

## Status

**SECURITY STATUS: PARTIAL**

This file describes the currently implemented Python gateway through commit
`20331bb`. It is not the future LAN/TLS contract. Device Link is intentionally
loopback-only until secure transport and certificate pinning are implemented.

## Boundary

- Prefix: `/device/v1`
- Transport currently verified: local HTTP on a loopback socket only.
- Remote bind configuration fails at startup.
- Non-loopback peers are rejected with 403.
- The exact `/device/v1` root and every `/device/v1/*` path pass through the
  same boundary; unknown routes return JSON 404 and never fall through to SPA.
- Browser `Origin` headers are accepted only when their host is loopback.
  Forwarding headers are not trusted.
- Maximum measured request body: 1 MiB. A larger streamed body returns 413
  regardless of `Content-Length`.
- Pairing routes and authentication-challenge routes are public within this
  loopback boundary.
- Protected reads require `X-Session-Token: <64-lowercase-hex-token>`.

## Error response

```json
{
  "error": {
    "code": "STABLE_MACHINE_CODE",
    "message": "Safe human-readable message"
  }
}
```

| HTTP | Meaning |
| --- | --- |
| 400 | Malformed JSON, wrong type, unknown field, invalid identifier/hex/length/version/key |
| 401 | Missing/invalid/expired token or failed/unknown/used challenge |
| 403 | Non-loopback peer, forbidden browser origin, or unbound device |
| 404 | Valid identifier for a resource that does not exist |
| 409 | Pairing state or bound-identity conflict |
| 410 | Pairing session or authentication challenge expired |
| 413 | Measured body exceeds 1 MiB |
| 429 | Active pairing/challenge capacity reached |

FastAPI's default 422 response is not part of the Device Link contract.

## Input rules

- Device/desktop identifiers: 1–128 ASCII characters from
  `A-Z a-z 0-9 . _ : -`.
- Pairing and challenge IDs: 32 lowercase hexadecimal characters.
- Pairing nonce and authentication challenge: exactly 32 bytes.
- Public key: DER SubjectPublicKeyInfo, P-256 only, maximum 512 bytes.
- Signature: DER-encoded ECDSA signature, maximum 512 bytes.
- Display name: 1–128 characters.
- Protocol version: integer `1`.
- JSON objects reject unknown fields and type coercion.

## Pairing endpoints

### `POST /device/v1/pair/start`

Creates one active pairing session. The body must be absent or `{}`; unknown
fields return 400.

```json
{
  "session_id": "32-lowercase-hex",
  "expires_in_s": 120,
  "desktop_uuid": "opaque-identifier",
  "desktop_pubkey_fingerprint": "8-hex",
  "state": "created"
}
```

The active-session limit defaults to five. The capacity check and insertion
are atomic; a sixth request returns `PAIR_CAPACITY_EXCEEDED`/429 and creates no
hidden session.

### `GET /device/v1/pair/{session_id}`

Returns the current monotonic state. Unknown is 404; expired is 410.

### `POST /device/v1/pair/{session_id}/connect`

```json
{"android_uuid": "android-001", "nonce": "64-hex-characters"}
```

### `POST /device/v1/pair/{session_id}/sas`

```json
{"android_pubkey_der_hex": "DER-SPKI-P256-as-hex"}
```

This containment pass validates the existing SAS flow but does not redesign
it. Independent cross-participant and double confirmation remain incomplete.

### `POST /device/v1/pair/{session_id}/confirm`

```json
{"confirm": true}
```

`confirm` is a strict boolean. Terminal states cannot be overwritten.

### `POST /device/v1/pair/{session_id}/complete`

```json
{
  "android_uuid": "android-001",
  "android_pubkey_der_hex": "DER-SPKI-P256-as-hex",
  "display_name": "Test Android",
  "protocol_version": 1
}
```

The UUID and public-key bytes must exactly match the identity bound during
`connect` and `sas`. A mismatch returns `PAIR_IDENTITY_MISMATCH`/409 before
device, token, or state side effects. Success consumes the session and returns
one random 32-byte token.

## Authentication endpoints

### `POST /device/v1/auth/challenge`

```json
{"device_uuid": "android-001", "protocol_version": 1}
```

```json
{
  "challenge_id": "32-lowercase-hex",
  "desktop_challenge": "64-hex-characters",
  "desktop_uuid": "opaque-identifier",
  "expires_in_s": 60
}
```

The server stores the challenge, device binding, monotonic expiry, attempt
count, and consumed state.

### `POST /device/v1/auth/response`

```json
{
  "device_uuid": "android-001",
  "challenge_id": "32-lowercase-hex",
  "signature": "DER-ECDSA-signature-as-hex",
  "protocol_version": 1
}
```

The signature covers the canonical message defined in `PROTOCOL.md`.
Successful verification atomically consumes the challenge, replaces any
previous token for that device, and returns a new token. Replay never returns
a second token. Three failed signature attempts consume the challenge.

## Protected endpoints

`GET /device/v1/status` and `GET /device/v1/checkpoints` require
`X-Session-Token`. Checkpoints currently returns an empty read-only placeholder
plus bound-device summaries. No other future endpoint is claimed as
implemented in P0-A.

## Token lifecycle

- Tokens contain 32 random bytes encoded as 64 lowercase hexadecimal
  characters.
- Only SHA-256 digests are retained by the in-memory token store.
- Expiry uses `now >= expires_at` with a monotonic clock.
- Issuing a new token invalidates the previous token for that device.
- Device revocation invalidates every token for that device.
- Storage is bounded. Tokens are never accepted in URLs and are not logged.
- Persistence and crash/restart continuity are not implemented.
