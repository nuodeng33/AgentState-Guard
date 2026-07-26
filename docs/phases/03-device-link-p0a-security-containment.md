# Phase 03 — Device Link P0-A Security Containment

## Status

COMPLETE

This is a bounded security-correction phase. It does not declare Device Link
production-ready and it does not reopen the cryptographic or transport design.

## Baseline

- Branch: `feat/device-link`
- Starting commit: `1ae3ad8086ae9c3e2713190b16d013d622037169`
- Targeted Device Link baseline: 88 tests, 86 passed, 2 failed.
- Full Python baseline on Windows: 227 tests, 207 passed, 20 failed.
- GitHub combined status for the starting commit returned no status contexts.
- Android verification is environment-blocked: the checkout has no Gradle
  launcher or wrapper JAR and the available Java runtime is Java 8.

The two targeted failures are test defects, not demonstrated product failures:
one reads UTF-8 source with the Windows legacy default encoding; the other
expects a zero-TTL replay entry to expire without advancing its injected clock.
The remaining full-suite failures are recorded separately and include existing
POSIX assumptions on Windows.

## In scope

| Requirement | Contract |
| --- | --- |
| P0A-CRYPTO-001 | Device public keys are DER SubjectPublicKeyInfo only. Signature verification accepts only P-256 ECDSA keys and never guesses PEM/DER formats. |
| P0A-PAIR-001 | Pairing sessions have monotonic terminal states. A terminal or expired session cannot be overwritten by a later operation. |
| P0A-PAIR-002 | The active-session limit is enforced atomically; an over-capacity request returns 429 and creates no session. |
| P0A-PAIR-003 | Pair completion must use the device UUID and DER public key already bound to the session. A mismatch is rejected before registry or token side effects. |
| P0A-AUTH-001 | Device Link protected reads and mutations use one `X-Session-Token` contract. Missing, invalid, revoked, expired, or replaced tokens are rejected. Tokens are random, stored by digest, bounded, and never logged. |
| P0A-AUTH-002 | Authentication challenges are server-issued, device-bound, time-bounded, attempt-bounded, and one-time. The signed message explicitly encodes protocol version, purpose, desktop UUID, device UUID, challenge ID, and challenge bytes. |
| P0A-AUTH-003 | The replay boundary is successful consumption of a challenge ID. Reusing a consumed challenge never issues another token. |
| P0A-HTTP-001 | Production FastAPI routes return real HTTP statuses: 400 input, 401 authentication, 403 unbound device, 404 missing resource, 409 state conflict, 410 expired resource, 413 oversized body, and 429 capacity. |
| P0A-HTTP-002 | Request models reject unknown fields, wrong types, invalid identifiers, invalid hex, excessive lengths, wrong nonce length, and wrong protocol version without leaking internal exceptions. |
| P0A-HTTP-003 | Device Link is fail-closed to loopback. Remote startup and non-loopback requests are rejected until a secure transport phase exists. Forwarded headers are not trusted for this decision. |
| P0A-HTTP-004 | Request bodies are limited to 1 MiB by measured bytes, including requests with missing or false `Content-Length`. |
| P0A-CONC-001 | Pair creation, pair completion, challenge consumption, token replacement, and registry mutation are correct under minimal concurrent execution. |
| P0A-TEST-001 | Security assertions use the production FastAPI router and exact status/code/side-effect checks. A fake HTTP server is not security evidence. |
| P0A-DOC-001 | Device Link contract, protocol, acceptance, current-state, rollback, and handoff documents match verified behavior. |

## Validation details

- Device identifiers are bounded opaque identifiers for this containment pass:
  1–128 ASCII characters from letters, digits, `.`, `_`, `:`, and `-`.
  Converting all existing identifiers to UUIDv4 is a later compatibility
  migration and is not silently introduced here.
- Session and challenge IDs are 32 lowercase hexadecimal characters.
- Public-key DER is non-empty and at most 512 bytes.
- Pairing nonces and authentication challenges are exactly 32 bytes.
- Protocol version is exactly `1`.
- JSON models are strict and reject additional fields.
- Expiry uses an injected monotonic clock with the boundary
  `now >= expires_at`.
- The lock order is gateway, pairing/session, registry, then token/challenge
  storage. Public gateway mutations hold the gateway re-entrant lock across
  validation, state transition, and all side effects.

## Required tests

1. Production `TestClient` tests demonstrate every HTTP status and stable
   machine-readable error code listed above.
2. DER P-256 succeeds; PEM, malformed DER, non-EC keys, and non-P-256 EC keys
   fail closed.
3. Every terminal pairing state remains terminal under later and concurrent
   calls.
4. The sixth active session fails with no hidden sixth session when the limit
   is five, including a concurrent creation test.
5. Pair completion rejects substituted identity material and produces no
   device/token side effect.
6. Protected status/checkpoint routes reject absent, invalid, expired, revoked,
   and superseded tokens.
7. Challenge tests cover unknown, expired, wrong-device, malformed signature,
   attempt exhaustion, success, and replay after success.
8. Payload tests cover exact 1 MiB and 1 MiB plus one byte without relying on
   `Content-Length`.
9. Loopback tests cover startup configuration and request peer address.
10. The targeted Device Link suite and full Python suite are rerun with parsed
    counts. Android tests are run only if the required local toolchain exists;
    otherwise the exact environment blocker is reported.

## Explicit non-goals

- No ECDH, HKDF, SAS, transcript, or double-confirm redesign.
- No Android cryptography, Android Keystore, or Android UI redesign.
- No TLS, mDNS, certificate pinning, LAN exposure, or remote binding.
- No persistence layer, AI integration, Windows runtime, MSI, or unrelated UI
  work.
- No claim that the current same-participant SAS flow provides independent
  cross-device confirmation.

## Risk and rollback

This phase deliberately makes previously permissive endpoints fail closed.
Clients using PEM keys, arbitrary payloads, remote binding, or inconsistent
token headers will stop working and must conform to the documented contract.

Rollback is a revert of the local P0-A commits back to starting commit
`1ae3ad8086ae9c3e2713190b16d013d622037169`. No database or persistent schema
migration is introduced. Repository instructions prohibit pushing from this
agent, so commits remain local for review.

## Exit criteria

- All in-scope requirements have production-path tests and implementation
  evidence.
- Targeted Device Link tests pass.
- Full-suite differences from the recorded baseline are explained; no new
  unrelated regression is accepted.
- Documentation reports the final security status as **PARTIAL** because
  independent SAS confirmation, double confirmation, TLS, pinning, Android
  Keystore, persistence, and physical end-to-end validation remain incomplete.

## Closeout evidence

- Implementation commits: `7c4277d`, `20331bb`.
- Device Link targeted suite: 107 passed, 0 failed/error/skipped.
- Full Python on Windows: 246 total, 229 passed, 17 failed.
- Baseline failure comparison: 3 resolved, 0 new.
- Changed-scope Ruff and compileall: passed.
- Independent verification: no remaining P0-A blocker.
- Android execution: blocked by missing Gradle launcher/wrapper JAR and Java 8.
- Overall Device Link security status: **PARTIAL**.
