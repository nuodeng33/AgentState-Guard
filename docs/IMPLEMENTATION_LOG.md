# Implementation Log

## 2026-07-23 — Phase 00 Start

- Created governance documents (MASTER_SPEC, REQUIREMENTS, ACCEPTANCE_MATRIX, NON_GOALS)
- Created phase contracts (00-baseline-and-governance)
- Created ROLLBACK_LEDGER.md
- Git baseline: 9043c07 (feat/v1-gui-release)
- Tag: pre-v1-gui
- Tests: 107 passed

## 2026-07-26 — Device Link P0-A Security Containment

- Starting commit: `1ae3ad8086ae9c3e2713190b16d013d622037169`.
- Phase contract commit: `a0a13e9`.
- Implementation commits: `7c4277d`, `20331bb`.
- Replaced PEM auto-detection with DER SPKI P-256-only verification.
- Made pairing terminal states monotonic and active-session capacity atomic.
- Bound completion identity to the session before side effects.
- Added digest-only token storage and server-owned challenge lifecycle.
- Added strict FastAPI models, typed error mapping, loopback/origin startup
  gates, and a stream-measured 1 MiB body limit.
- Replaced fake Device Link HTTP security tests with production FastAPI tests.
- Targeted result: 107 passed.
- Full Windows result after independent verification: 246 tests,
  229 passed, 17 pre-existing platform failures, and no new failure relative
  to baseline.
- Independent verification found and closed strict-type, whitespace-hex,
  expiry-overwrite, malformed-origin, invalid-DER-ordering, and Device Link
  namespace fallback gaps.
- Overall Device Link security status remains **PARTIAL**.
