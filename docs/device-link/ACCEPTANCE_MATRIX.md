# Device Link Acceptance Matrix

## P0-A containment

| ID | Status | Evidence |
| --- | --- | --- |
| P0A-CRYPTO-001 | VERIFIED_LOCALLY | DER P-256 succeeds; PEM, RSA, P-384, wrong key/message fail. |
| P0A-PAIR-001 | VERIFIED_LOCALLY | All terminal states reject every later transition. |
| P0A-PAIR-002 | VERIFIED_LOCALLY | Five-session limit is atomic under concurrent excess requests. |
| P0A-PAIR-003 | VERIFIED_LOCALLY | Substituted completion identity returns 409 without side effects. |
| P0A-AUTH-001 | VERIFIED_LOCALLY | Missing/invalid/expired/replaced/revoked tokens fail; only digests are retained. |
| P0A-AUTH-002 | VERIFIED_LOCALLY | Server-issued, bound, expiring, attempt-bounded challenges cover the canonical message. |
| P0A-AUTH-003 | VERIFIED_LOCALLY | Sequential and concurrent replay produce exactly one token. |
| P0A-HTTP-001 | VERIFIED_LOCALLY | Production FastAPI tests assert exact 400/401/403/404/409/410/413/429 statuses. |
| P0A-HTTP-002 | VERIFIED_LOCALLY | Strict models reject wrong types, fields, IDs, hex, lengths, versions, and keys. |
| P0A-HTTP-003 | VERIFIED_LOCALLY | Remote peer/origin/bind and `--allow-remote` fail closed. |
| P0A-HTTP-004 | VERIFIED_LOCALLY | Stream-measured exact 1 MiB succeeds; 1 MiB + 1 fails with false length. |
| P0A-CONC-001 | VERIFIED_LOCALLY | Concurrent capacity, completion, and challenge-consumption tests pass. |
| P0A-TEST-001 | VERIFIED_LOCALLY | Production-router tests replaced fake HTTP security routers. |
| P0A-DOC-001 | VERIFIED_LOCALLY | Runtime truth, current state, handoff, rollback, phase contract, and independent review are recorded. |

## Wider Device Link program

| ID | Status | Description |
| --- | --- | --- |
| ASD-PAIR-003 | PARTIAL | SAS exists; independent cross-participant/double confirmation is incomplete. |
| ASD-CONN-003 | PARTIAL | Desktop challenge-response is tested; physical Android E2E is not. |
| ASD-SEC-001 | PARTIAL | Python gateway is loopback-only; secure LAN transport is absent. |
| ASD-SEC-005 | PARTIAL | In-memory revocation works; persistent revocation is absent. |
| ASD-CONN-001 | NOT_STARTED | TLS 1.3 transport and downgrade resistance. |
| ASD-DISC-001 | NOT_STARTED | mDNS advertisement. |
| ASD-DISC-002 | NOT_STARTED | Android NSD discovery. |
| ASD-ID-003 | NOT_STARTED | Android Keystore-backed identity. |
| ASD-PAIR-001 | NOT_VERIFIED | Physical QR pairing. |
| ASD-CONN-006 | NOT_VERIFIED | Physical auto-reconnect. |
| ASD-CI-001 | BLOCKED_BY_ENVIRONMENT | No Gradle launcher/wrapper JAR; Java 8 only. |
| ASD-CI-002 | NOT_VERIFIED | Starting commit returned no GitHub status contexts. |
| ASD-PERSIST-001 | NOT_STARTED | Durable device/token/challenge/revocation storage. |

## Overall result

**PARTIAL** — P0-A desktop containment is locally verified, but wider Device
Link security closure is incomplete.
