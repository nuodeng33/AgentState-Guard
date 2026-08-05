# ARCHITECTURE

## R4 Evidence, Policy, and Supervision

`StateDB.transaction()` is the transaction foundation for R4 writes. It owns `BEGIN IMMEDIATE`, commit, and rollback; nested transactions are rejected. A `SupervisionService` state change performs its session read/validation, session write, and `EvidenceLedger.append(connection, event)` through the same explicit SQLite connection, so either both session and ledger effects commit or neither does.

The v3 Evidence Ledger is independent from the legacy `audit_events` log. It is append-only through database triggers. Its canonical JSON authority fields, payload digest, predecessor hash, and current hash form a tamper-verifiable chain. Discovery writes bounded, one-way evidence into that ledger; it does not infer external actions.

The v4 `supervision_sessions` table is independent from legacy transactions and legacy audit semantics. Lifecycle states include `EVALUATED`, `AWAITING_APPROVAL`, `APPROVED`, `ACTIVE`, `REJECTED`, `COMPLETED`, and `FAILED`. Terminal sessions cannot revive. `BLOCK` and `UNKNOWN` sessions cannot activate; `REVIEW` requires explicit approval; a required checkpoint cannot be bypassed.

The local policy engine is deterministic and uses `BLOCK > REVIEW > UNKNOWN > ALLOW` safety precedence. It defaults to a non-ALLOW result when evidence or domain facts are insufficient. It has no AI dependency.
