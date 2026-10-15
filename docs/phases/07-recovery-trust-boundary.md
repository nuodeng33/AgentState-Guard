# Phase 07 — Recovery Trust Boundary

## Requirement IDs

- ASG-CLI-004: isolated test-restore remains an authoritative R2 prerequisite.
- ASG-CORE-005: recovery and baseline evidence remains append-only and ledger-verifiable.
- ASG-SEC-002: recovery records and CLI outputs retain only safe structured evidence.
- ASG-SEC-003: R3 drill remains limited to the managed self-runtime target.
- ASG-REL-001: wheel installation and Recovery CLI smoke remain release gates.

## Scope

- Add schema v7 durable bindings from recovery drills and baseline candidates to Supervision sessions.
- Add one-time, nonce-bound, expiry-bound `recovery_authorizations` for R3 drills and trusted-baseline confirmation.
- Require Supervision `REVIEW` plus explicit `USER_APPROVED` evidence before authorization issuance.
- Keep authorization consumption, drill/candidate state changes, immutable baseline writes, and ledger events in one SQLite transaction.
- Derive R0 through R3 and trusted-baseline facts only from StateDB, Snapshot V3, durable recovery records, and a verified Evidence Ledger.
- Expose inspection and baseline lifecycle commands through `agentguard recovery` without changing legacy restore commands.

## Non-goals

- No production restore, Windows/WSL recovery, Docker integration, UI, public HTTP API, or P8 work.
- No automatic trust grant from R3 evidence; `VERIFIED_R3` and `TRUSTED` remain distinct states.
- No boolean or v5/v6 approval record serves as authorization authority.

## Affected Files

- `agentguard/storage/migrations.py`
- `agentguard/supervision/service.py`
- `agentguard/recovery/service.py`
- `agentguard/recovery/coverage.py`
- `agentguard/cli.py`
- `tests/test_migrations.py`
- `tests/test_recovery_drill.py`
- `tests/test_recovery_trusted_baseline.py`
- `tests/test_cli_recovery.py`

## Risks and Controls

| Risk | Control |
|---|---|
| Authorization replay or cross-subject use | Unique subject/session/nonce bindings, expiry checks, conditional durable consume |
| Baseline trust from incomplete or stale recovery evidence | Full R3 event sequence, ledger verification, approved Supervision binding, and scope-drift failure gate |
| Partial state on failure | `StateDB.transaction()` owns session, authorization, candidate/baseline, consume, and ledger writes |
| Trusted projection survives retirement | Projection reads durable retirement records on every computation |
| Sensitive or filesystem details leak through CLI | Stable structured outputs return IDs/status/reason codes only |

## Test Evidence

- `pytest -q`: 753 passed in 26.58s.
- Scoped Ruff: all checks passed.
- `.venv/bin/python -m build --wheel`: built `agentstate_guard-0.9.0.dev0-py3-none-any.whl`.
- Clean, non-editable wheel install: `agentguard --help`, Recovery help, drill/baseline help, and fail-closed drill show smoke all passed.

## Rollback Point

- Start commit: `60204c233136c5bcf58dfb45c5a477b978a987f8`.
- Roll back only the P7 commit after this phase; do not use reset, clean, stash, or changes from the frozen legacy workspace.

## Exit Criteria

- Every R3 drill and trusted-baseline confirmation requires durable Supervision approval and one-time bound authorization.
- Invalid, expired, consumed, mismatched, BLOCK/UNKNOWN, scope-drift, or ledger-invalid evidence fails closed.
- R3 and trusted-baseline projection facts satisfy R1 -> R2 -> R3 and `TRUSTED -> R3` implications.
- Recovery CLI uses the formal recovery service and has no caller-supplied recovery truth flags.
- Full tests, lint, build, wheel-install smoke, diff check, normal push, and CI audit are recorded.