# Phase 00: Baseline and Governance

## Requirement IDs
ASG-CORE-001, ASG-CORE-005, ASG-TEST-001, ASG-TEST-004, ASG-REL-003, ASG-REL-004

## Objective
Establish project governance, schema migration system, audit logging, CI templates, and open-source documentation.

## Inputs
- Current v0.2 codebase at pre-v1-gui tag
- 107 existing tests
- Permission baseline

## Scope
- Create governance document tree (specs, phases, handoffs, decisions, risks)
- Implement formal SQLite schema migration system
- Implement audit log with hash chain
- Implement content-addressed blob store (ASG-CORE-002)
- Create GitHub workflow templates (CI, CodeQL, Dependabot, Scorecard)
- Create open-source documentation (README, LICENSE, CONTRIBUTING, etc.)

## Explicit Non-Goals
- No transaction engine
- No CLI expansion beyond what migration testing needs
- No GUI work
- No host-probe scripts
- No Python venv setup (use scripts/bootstrap-dev.sh)

## Files Expected to Change
- agentguard/storage/db.py (schema migration)
- agentguard/storage/snapshots.py (blob store)
- pyproject.toml
- New: agentguard/storage/migrations.py
- New: agentguard/storage/audit.py
- New: agentguard/storage/blob.py
- New: agentguard/storage/gc.py
- New: .github/workflows/*.yml
- New: LICENSE, README.md, CONTRIBUTING.md, SECURITY.md, etc.

## Data Migrations
- Schema version table
- v1/v2 snapshot compatibility preserved

## Security Impact
- Audit hash chain adds integrity detection
- Blob store with content addressing prevents tampering

## Threats Considered
- Migration failure on existing databases
- Blob corruption detection
- Rollback safety

## Test Plan
- Migration tests (forward + rollback)
- Blob store CRUD + dedup + integrity + GC
- Audit chain append + verification
- 107 existing tests must still pass

## Real Integration Scenario
- Create test DB, migrate to latest schema, store blob, retrieve, verify integrity
- Simulate corruption, verify detection

## Rollback Point
pre-v1-gui tag

## Rollback Procedure
git reset --hard pre-v1-gui && git clean -fd

## Exit Criteria
- Schema migration works (create → migrate → verify)
- Blob store: store → dedup → retrieve → GC dry-run
- Audit chain: append → verify → tampering detection
- 107 existing tests pass
- All governance docs created
- CI workflows pass static validation

## Evidence Produced
- artifacts/phases/00/
