# Phase 02: CLI Expansion

## Requirement IDs
ASG-CLI-001, ASG-CLI-002, ASG-CLI-003, ASG-CLI-004, ASG-CLI-005, ASG-CLI-007, ASG-CLI-008, ASG-CLI-010, ASG-CLI-011

## Objective
Add all v1.0 CLI commands: plan, transactions, undo, verify, test-restore, gc, watch, handoff, incident, host-probe, run.

## Inputs
- Phase 00 completed: storage, migrations, blob store
- Phase 01 completed: transaction engine

## Scope
- CLI integration for transaction engine (plan, apply, verify, undo)
- verify command (integrity checks)
- test-restore command (restore to temp sandbox)
- gc command (dry-run + execute, with GC plan)
- watch command (polling drift monitor)
- handoff command (AI handoff document)
- incident command (incident bundle)
- host-probe command (host state collection)
- run command (wrapped command execution)
- Scripts: bootstrap-dev.sh, doctor-dev.sh, clean-dev-env.sh

## Files Expected to Change
- agentguard/cli.py (add all new commands)
- agentguard/commands/ (new command modules)
- New: scripts/bootstrap-dev.sh
- New: scripts/doctor-dev.sh
- New: scripts/clean-dev-env.sh

## Test Plan
- CLI help test for each command
- Transaction CLI: plan/create/list/show/undo
- verify: integrity checks on temp DB
- gc: plan dry-run
- host-probe: stdin import
- 139 existing tests must pass
