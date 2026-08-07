# Implementation Log

## 2026-08-07 — R4-P7 recovery trust boundary

- Added schema v7 bindings from R3 drills and trusted-baseline candidates to Supervision REVIEW sessions, and durable one-time recovery authorizations with bound subject, context, policy, fingerprint, expiry, nonce, and consumption fields.
- Replaced legacy recovery approval authority with atomic Supervision `USER_APPROVED` plus durable authorization issuance. R3 drill execution and trusted-baseline confirmation reject replay, expiry, mismatch, and stale authorization state.
- Implemented trusted-baseline lifecycle: `CANDIDATE` does not equal `TRUSTED`; confirmation revalidates R3 evidence and atomically writes an immutable baseline, consumes the candidate/authorization, and appends ledger evidence; retirement removes trusted projection.
- Extended server-derived recovery facts with R0/R1/R2/R3 and trusted-baseline dimensions. Strict R3 evidence requires a verified ledger, complete drill sequence, matching approved Supervision binding, and absence of scope/failure evidence.
- Added Recovery CLI inspection and baseline lifecycle commands without changing `agentguard restore` or `agentguard test-restore` and without user-provided trust flags.
- Fresh local evidence: 753 Python tests passed in 26.58s; scoped Ruff passed; wheel build passed; clean non-editable wheel install plus Recovery CLI help/read-only show smoke passed. Normal push and CI audit are recorded separately at the release boundary.


- Added Transaction Foundation support through explicit `StateDB.transaction()` ownership and `BEGIN IMMEDIATE` semantics.
- Added R4 append-only Evidence Ledger, deterministic local Policy, v4 independent supervision sessions, and local `agentguard supervise` CLI workflow.
- Retry Guard now resolves its project-local entrypoint through `CLAUDE_PROJECT_DIR`, detects staged/unstaged/bounded untracked source evidence, and preserves third-identical-failure blocking.
- Offline closure regression covers ALLOW completion and ledger reconnect verification, REVIEW approval requirements, BLOCK/UNKNOWN activation denial, and synthetic secret non-persistence.
- Fresh local targeted evidence before this documentation update: Retry Guard 9 passed; supervision-related regression 35 passed; offline closure 3 passed. Full-suite/package/frontend/CI evidence remains a separate final gate.


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
