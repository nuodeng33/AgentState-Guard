# Implementation Log

## 2026-07-23 — Phase 00 Start

- Created governance documents (MASTER_SPEC, REQUIREMENTS, ACCEPTANCE_MATRIX, NON_GOALS)
- Created phase contracts (00-baseline-and-governance)
- Created ROLLBACK_LEDGER.md
- Git baseline: 9043c07 (feat/v1-gui-release)
- Tag: pre-v1-gui
- Tests: 107 passed

## 2026-07-27 — Device Link adversarial audit and correction

- Scope: ASDL/1 crypto, pairing FSM, registry binding, challenge/token lifecycle, FastAPI wiring, Android client contract.
- First audit patch correctly introduced 32-byte tokens, monotonic token expiry, duplicate/single-device rejection, exact replay TTL, P-256 DER validation, terminal secret clearing, and Bearer headers, but independent review found blocking regressions.
- RED evidence: FastAPI `/device/v1/status` returned 422 because postponed annotation resolution could not resolve a function-local `Request`; expired pairing could be reactivated; `pair_ttl` stayed at 120 seconds; unvisited expired sessions exhausted `max_sessions`; Android HTTPS-only enforcement broke all HTTP development tests.
- Correction: module-scope `Request`; production Bearer 401/200 tests; absorbing terminal/expiry transition and controlled errors; pair TTL/max-attempt propagation; expiry cleanup before capacity checks; `pair_complete` consumes before binding so expiry cannot leave a registry side effect; restored HTTP development transport while retaining Bearer authentication.
- Fresh verification via `/workspace/venv/bin/python`: 281 Python tests passed; Device Link targeted tests passed 117/117. Android `testDebugUnitTest` succeeded and parsed JUnit XML reported 11 passed, 0 failed, 0 skipped. Python `compileall` and `git diff --check` passed.
- Rollback point: revert only the listed Device Link product/test/document paths; preserve unrelated pre-existing worktree changes.
- Remaining: production TLS/SPKI pinning, dual SAS confirmation, complete challenge transcript/mutual authentication, UUID canonicalization, challenge/session bounded cleanup, and full ASDL/1 endpoint/schema migration.
