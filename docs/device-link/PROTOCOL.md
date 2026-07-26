# ASDL/1 Runtime Protocol — P0-A Containment

## Status and limits

This is the implemented loopback runtime protocol through commit `20331bb`.
Security status is **PARTIAL**. TLS, LAN discovery, certificate pinning,
Android Keystore, persistent identity/tokens, independent SAS confirmation,
double confirmation, and physical end-to-end verification are not implemented
or verified by this phase.

Future design documents may describe those capabilities, but they are not
current runtime guarantees.

## Time semantics

All security TTLs use an injected monotonic clock and expire when
`now >= expires_at`.

| Resource | Default TTL |
| --- | --- |
| Pairing session | 120 seconds |
| Authentication challenge | 60 seconds |
| Session token | 3600 seconds |

## Cryptographic wire contract

- Public keys are DER-encoded SubjectPublicKeyInfo.
- The only accepted verification key is an EC key on P-256 (`secp256r1`).
- PEM, RSA DER, other EC curves, malformed DER, and format guessing are
  rejected.
- Signatures use DER-encoded ECDSA with SHA-256.
- Existing SAS transcript/HMAC behavior is preserved; this phase does not
  claim independent participant confirmation.

## Authentication signed message

The device signs the exact byte sequence:

```text
"ASDL\0AUTH_RESPONSE\0"
uint16_be(protocol_version)
uint32_be(len(desktop_uuid_utf8)) || desktop_uuid_utf8
uint32_be(len(device_uuid_utf8))  || device_uuid_utf8
uint32_be(len(challenge_id_bytes)) || challenge_id_bytes
uint32_be(len(challenge_bytes))    || challenge_bytes
```

`challenge_id_bytes` is the decoded 16-byte challenge ID and
`challenge_bytes` is the server-issued 32-byte random challenge. Explicit
length prefixes prevent field-boundary ambiguity.

## Challenge state machine

```text
ISSUED --valid signature--> USED + new token
ISSUED --expiry boundary--> EXPIRED/USED
ISSUED --three failures--> USED
USED --any replay--> 401 AUTH_CHALLENGE_USED
```

The challenge ID is the replay boundary. A client-supplied nonce is not an
authentication source of truth.

## Pairing state machine

Terminal states are `consumed`, `expired`, `rejected`, `failed`, and
`cancelled`. No event can transition a terminal state. Expiry is checked before
a live transition, uses the same monotonic boundary, and cannot be overwritten
by reject/cancel/fail.

Pair completion accepts only the Android identifier and DER key already stored
in that pairing session. Validation, state transition, registry mutation, and
token issuance execute under the gateway mutation lock.

## Concurrency order

```text
gateway -> pairing/session -> device registry -> token/challenge stores
```

Pair capacity check-and-create, pair completion, challenge consumption, token
replacement, and registry mutation are serialized across their security side
effects. Concurrent tests verify a single winning completion/challenge
response and exact active-session capacity.

## HTTP boundary

- Only loopback peer addresses are accepted.
- The exact namespace root and all child paths share the same gate; unknown
  Device Link paths return stable JSON 404 rather than SPA HTML.
- Remote browser origins are rejected even when the socket peer is loopback.
- `X-Forwarded-For` and similar headers are not used as trust inputs.
- `--allow-remote` and non-loopback startup hosts fail closed.
- Body bytes are counted while reading the ASGI stream; buffering stops and
  413 is returned when the count exceeds 1 MiB.
- Exact HTTP statuses and error codes are defined in `API_CONTRACT.md`.
