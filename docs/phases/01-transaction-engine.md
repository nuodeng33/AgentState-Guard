# Phase 01: Transaction Engine

## Requirement IDs
ASG-CORE-003, ASG-CORE-004, ASG-CLI-001, ASG-CLI-002

## Objective
Implement the transaction engine: plan → preflight → apply → verify → commit/rollback state machine with rollback coverage computation.

## Inputs
- Phase 00 completed: migrations, blob store, audit chain
- Existing transaction tables in DB schema (v2)
- Existing checkpoint and restore commands

## Scope
- Transaction state machine (planned→preflight_failed→ready→applying→verifying→committed or rollback)
- Rollback coverage computation (fully/partially/not recoverable)
- CLI: plan, transactions list, transaction show, undo
- Integration with existing checkpoint and blob store

## Explicit Non-Goals
- No GUI work
- No host-probe changes
- No export/import changes
- No GC changes beyond what transactions need

## Files Expected to Change
- New: agentguard/transactions/__init__.py
- New: agentguard/transactions/engine.py
- New: agentguard/transactions/coverage.py
- New: agentguard/commands/plan.py
- Change: agentguard/cli.py (add plan, transactions, undo, show commands)
- Change: agentguard/storage/db.py (transaction query methods)

## Data Migrations
- None (schema already exists from v2 migration)

## Security Impact
- Rollback coverage must be honest; never claim fully_recoverable when not
- Undo must validate paths against whitelist

## Threats Considered
- Transaction interrupted mid-apply
- Rollback of partially applied transaction fails
- Coverage overestimation

## Test Plan
- Transaction state machine: all state transitions
- Coverage computation: fully/partially/not_recoverable
- Integration: create plan, apply, verify, undo
- Existing 127 tests must pass

## Real Integration Scenario
- Create checkpoint → modify file → create plan → apply → verify → undo → verify rollback

## Rollback Point
6779a50 (Phase 00 commit)

## Rollback Procedure
git revert HEAD

## Exit Criteria
- Transaction engine passes state machine tests
- Coverage computation gives correct percentages
- CLI plan/create/list/show/undo commands work
- 127+ existing tests pass
