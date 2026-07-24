# Device Link Gateway API Contract — ASDL/1

## Gateway Endpoint
- Bound to: User-selected Private LAN interface
- NOT bound to: 127.0.0.1, 0.0.0.0, or Public network interfaces
- Default port: 8788 (configurable)
- Protocol: HTTPS + WSS only

## Authentication
All endpoints except `/pair/*` require `Authorization: Bearer <session-token>` header.

## Error Response Shape
```json
{"error": "reason_string", "code": 401, "detail": "Human-readable explanation"}
```

## Endpoints

### POST /device/v1/pair/start
**Auth:** None
**Request:**
```json
{}
```
**Response (200):**
```json
{
  "session_id": "base64url-16bytes",
  "expires_at": "ISO8601",
  "desktop_uuid": "uuid-v4",
  "desktop_pubkey": "base64url",
  "desktop_pubkey_fingerprint": "hex-8chars",
  "sas_display": "482 913"
}
```
**Errors:** 423 (Desktop already has max devices), 503 (Device Link disabled)

### GET /device/v1/pair/{session_id}
**Auth:** None
**Response (200):**
```json
{
  "status": "waiting|confirmed|cancelled|expired",
  "android_display_name": null
}
```

### POST /device/v1/pair/confirm
**Auth:** None
**Request:**
```json
{
  "session_id": "base64url",
  "sas_confirmed": true,
  "android_uuid": "uuid-v4",
  "android_pubkey": "base64url-ed25519",
  "display_name": "Pixel 10",
  "protocol_version": 1
}
```
**Response (200):**
```json
{
  "status": "bound",
  "session_token": "base64url-32bytes",
  "desktop_uuid": "uuid-v4",
  "desktop_display_name": "DESKTOP-ABC",
  "permissions": ["read", "checkpoint", "restore"]
}
```

### POST /device/v1/auth/challenge
**Auth:** `X-Device-UUID` header
**Request:**
```json
{"nonce": "base64url-32bytes"}
```
**Response (200):**
```json
{
  "desktop_challenge": "base64url-32bytes",
  "desktop_uuid": "uuid-v4",
  "session_nonce": "base64url-16bytes"
}
```
**Errors:** 403 (device not bound), 426 (protocol mismatch)

### POST /device/v1/auth/response
**Auth:** `X-Device-UUID` header
**Request:**
```json
{
  "challenge_response": "base64url-ed25519-signature",
  "nonce": "base64url-32bytes",
  "session_nonce": "base64url-16bytes"
}
```
**Response (200):**
```json
{
  "session_token": "base64url-32bytes",
  "desktop_signature": "base64url-ed25519-signature",
  "expires_at": "ISO8601"
}
```

### GET /device/v1/status
### GET /device/v1/doctor
### GET /device/v1/checkpoints?limit=20
### GET /device/v1/checkpoints/{id}
### POST /device/v1/checkpoints
### GET /device/v1/diff?checkpoint_id=N
### POST /device/v1/restore/dry-run
### POST /device/v1/restore/apply
### GET /device/v1/handoff?budget=2000
### GET /device/v1/events (WSS upgrade)

**Auth:** All require valid session token. Shapes mirror existing Core API responses.
**Rate limit:** 60 requests/min per session.

## WebSocket Events (WSS)
```
{"type": "health_changed", "data": {"status": "PASS"}}
{"type": "drift_detected", "data": {"changes": 3}}
{"type": "checkpoint_created", "data": {"id": 5, "label": "..."}}
{"type": "transaction_started", "data": {"txn_id": 1}}
{"type": "transaction_finished", "data": {"txn_id": 1, "status": "committed"}}
{"type": "device_revoked", "data": {}}
```
