# Requirements — AgentState Guard v1.0

## Core

| ID | Description | Risk | Required | Module | Acceptance |
|----|------------|------|----------|--------|-----------|
| ASG-CORE-001 | Schema migration system | H | YES | storage | Migrate v1→v2 without data loss |
| ASG-CORE-002 | Content-addressed blob store | H | YES | storage | Dedup, integrity, GC |
| ASG-CORE-003 | Transaction engine | H | YES | transactions | Plan→apply→verify→commit/rollback |
| ASG-CORE-004 | Rollback coverage computation | H | YES | transactions | Percentage + reason |
| ASG-CORE-005 | Audit log with hash chain | M | YES | storage | Detect tampering |

## CLI

| ID | Description | Risk | Required | Module | Acceptance |
|----|------------|------|----------|--------|-----------|
| ASG-CLI-001 | plan, transactions, transaction show | M | YES | commands | Create and inspect transactions |
| ASG-CLI-002 | undo transaction | H | YES | commands | Roll back committed transaction |
| ASG-CLI-003 | verify | M | YES | commands | Integrity checks |
| ASG-CLI-004 | test-restore | H | YES | commands | Restore to temp sandbox |
| ASG-CLI-005 | gc, gc --dry-run | M | YES | commands | Retention + reclamation |
| ASG-CLI-006 | watch | L | YES | commands | Polling drift monitor |
| ASG-CLI-007 | handoff | L | YES | commands | AI handoff document |
| ASG-CLI-008 | incident | L | YES | commands | Incident bundle |
| ASG-CLI-009 | export, import | M | YES | commands | Config transfer |
| ASG-CLI-010 | host-probe | L | YES | commands | Host state collection |
| ASG-CLI-011 | run -- <command> | M | YES | commands | Wrapped command execution |

## GUI

| ID | Description | Risk | Required | Module | Acceptance |
|----|------------|------|----------|--------|-----------|
| ASG-GUI-001 | Dashboard with health score | M | YES | web | All panels load |
| ASG-GUI-002 | Environment topology | M | YES | web | Node map with status |
| ASG-GUI-003 | Checkpoint timeline | M | YES | web | Filter/search/Last Known Good |
| ASG-GUI-004 | Diff viewer | M | YES | web | Side-by-side + unified |
| ASG-GUI-005 | Restore wizard | H | YES | web | Step-by-step recovery |
| ASG-GUI-006 | Drift monitor view | L | YES | web | Event timeline |
| ASG-GUI-007 | Profiles and policies | L | YES | web | Config editor |
| ASG-GUI-008 | Reports and incident bundle UI | L | YES | web | View/download |

## Security

| ID | Description | Risk | Required | Module | Acceptance |
|----|------------|------|----------|--------|-----------|
| ASG-SEC-001 | No Docker socket access | H | YES | all | Verify container check |
| ASG-SEC-002 | No credential storage | H | YES | all | Sanitizer covers all outputs |
| ASG-SEC-003 | Path traversal protection | H | YES | core | All path inputs validated |
| ASG-SEC-004 | Audit hash chain | M | YES | storage | Event integrity |
| ASG-SEC-005 | CSRF/Origin/Session auth for GUI | H | YES | api | Web security headers |

## Test

| ID | Description | Risk | Required | Module | Acceptance |
|----|------------|------|----------|--------|-----------|
| ASG-TEST-001 | Python tests cover migration, blob, txn, restore | H | YES | tests | >150 tests |
| ASG-TEST-002 | Frontend component tests | M | YES | web | Dashboard, wizard, diff |
| ASG-TEST-003 | Playwright E2E | M | YES | e2e | Full restore roundtrip |
| ASG-TEST-004 | CI workflows | L | YES | .github | Linux/Windows/macOS |

## Release

| ID | Description | Risk | Required | Module | Acceptance |
|----|------------|------|----------|--------|-----------|
| ASG-REL-001 | pip/wheel install | M | YES | packaging | Fresh venv install |
| ASG-REL-002 | GUI accessible via agentguard ui | H | YES | web | Browser opens |
| ASG-REL-003 | Open-source files | L | YES | repo | README, LICENSE, CONTRIBUTING |
| ASG-REL-004 | Documentation | M | YES | docs | CLI, GUI, architecture, security |
