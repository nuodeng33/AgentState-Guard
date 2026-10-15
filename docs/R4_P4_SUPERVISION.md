# R4-P4 Evidence and Supervision Contracts

## Transaction foundation

R4 writes use `StateDB.transaction()`: it starts `BEGIN IMMEDIATE`, gives the outer service one SQLite connection, commits on success, and rolls back on any exception. Nested transactions are rejected. Migration steps also run in explicit transactions; a failed migration is rolled back rather than leaving a partial schema.

## Evidence Ledger and legacy audit

The R4 Evidence Ledger (`evidence_ledger_events`, schema v3) is separate from legacy `audit_events`. Ledger inserts are append-only: database triggers reject updates and deletes. Each event canonicalizes authoritative fields and binds `payload_digest`, `prev_hash`, and `curr_hash`; `verify_ledger()` detects tampering or serialization defects. Discovery supplies bounded one-way evidence to this ledger and does not prove an external side effect.

## Local policy

The deterministic local policy engine has fixed `BLOCK > REVIEW > UNKNOWN > ALLOW` precedence. It requires structured facts and defaults away from ALLOW if evidence or execution-domain facts are insufficient. BLOCK must not be weakened; UNKNOWN does not execute. The policy engine performs no Provider, network, Docker, or AI call.

## Supervision sessions

Schema v4 adds an independent `supervision_sessions` table; it does not change legacy transaction or audit semantics. The service persists a digest of declared intent rather than the raw intent. It coordinates every session state update and ledger event with one `StateDB.transaction()` connection.

- `SESSION_CREATED` and `POLICY_EVALUATED` are written atomically with creation.
- `USER_APPROVED` and `USER_REJECTED` are written atomically with approval decisions.
- `SESSION_COMPLETED` and `SESSION_FAILED` are written atomically with terminal transitions.
- Ledger failure rolls back the session write; a session write failure rolls back the ledger write.
- `REVIEW` requires explicit approval; approval does not bypass a checkpoint requirement.
- `BLOCK` and `UNKNOWN` cannot become `ACTIVE`.
- `COMPLETED`, `FAILED`, and `REJECTED` are terminal and cannot revive.

## Local CLI

`agentguard supervise create|show|evaluate|approve|reject|activate|complete|fail` is a local CLI only. Create/evaluate accept structured policy flags for intent/effect, domain, target/scope/evidence references, effects, checkpoint ID, and recovery coverage. JSON output is a single stable object and excludes database absolute paths, raw command lines, prompts, raw exceptions, and sensitive input. Policy block, policy unknown, approval-required, and checkpoint-required outputs use non-success exits.

## Retry Guard

The project Hook invokes the canonical `scripts/retry_guard.py` through `${CLAUDE_PROJECT_DIR}`. The guard binds its summary to HEAD, staged and unstaged diffs, bounded untracked source/test content digests, and diagnosis evidence. It ignores generated assets and its own state. Sensitive-named untracked files contribute only an irreversible presence digest; their content is not read. The local `.claude/retry-guard-state.json` is ignored runtime state, not source. A third identical failure without new evidence still blocks; new evidence resets only the current command fingerprint.

## Boundaries

P5 AI Supervisor is not implemented; `AI_ASSESSED` is a reserved event type only. Claude Code business supervision hooks are not integrated. P6/P7 recovery realism remains incomplete. UI and public HTTP API supervision surfaces are not implemented. Local offline tests do not establish comprehensive protection or unconditional recovery, and this is not a complete MVP.
