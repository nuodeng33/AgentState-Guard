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
| ASG-SEC-001 | No mounted Docker socket or generic Docker command API; bounded host CLI observation is permitted under the [discovery boundary](../CORE_DISCOVERY_MANAGEMENT.md) | H | YES | all | Verify local endpoint, fixed commands, server-enumerated targets and no lifecycle operations |
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

## Local Work extension

The approved [Work product addendum](../../../output/asg-backend-review-20260927/产品规格增补.md)
defines WS-001–WS-015. The current [Work API](../WORK_API.md) provides requirements,
tasks, manual text artifact versions and independently saved spatial views. These
requirements add to the Guard contract above; they do not introduce a Work health
score or infer completion from process activity. Historical status in the original
acceptance matrix is not current verification evidence.

- WS-007/008: real relation nodes open an aggregate workbench; focus reading is optional.
- WS-007: entering a Work hides the overview and Guard shell. Initially show its
  stable core; related objects expand on demand. No decorative object may stand
  in for a real Agent. A saved empty workbench must offer usable content selection.
- WS-009/010: returning and reopening preserve both viewports and card references;
  moving or closing a reference never changes dependencies or deletes content.
- WS-011/012: failed/conflicting saves are explicit and existing Guard navigation remains.
  Returning is never gated on a successful save. Unsaved layouts stay isolated by
  Work in memory while navigating; an old asynchronous response cannot replace
  another Work's layout. Browser close still warns about unsaved local drafts.
- WS-013: relations use actual membership and exact requirement/artifact versions;
  manual responsibility labels do not establish Agent identity or execution.

## ASG-ONBOARD-001 — Existing project connection (2026-10-23)

Expose existing program/store registration and directory register/configure APIs in session creation and recovery. Directory candidates are metadata only. Explicit registration starts protection with existing defaults; configure enabled, retention and quota with revision checks. Do not auto-start environments or grant session consent. Retain uncertain exact request identity across panel closure and refresh confirmed receipts before selection. Enforce application/store/scope source identity and existing backend-supported store readiness. Actual session creation is separate from provider execution. Native/phone verification remains independently attributed.

## ASG-CLOSE-001..005 — All planned capability gaps (2026-10-23)

User explicitly requests all rows of the preceding capability audit. CLOSE-001: real Agent/task child groups, continuous hover/focus, pin/unpin, visible parentward return, dominant Work core, durable user Agent color and usable3D viewer. CLOSE-002: explicit immutable next-instruction queue with safe promotion/hold/stop/restart semantics, separate from unsent drafts. CLOSE-003: Docker/Codex/Kimi/WSL connection with server-owned scope proof, never weaken sandbox or source identity. CLOSE-004: desktop/mobile pairing and existing authorized control integration, exact SAS, durable binding, idempotent confirmation and actual navigation/actions. CLOSE-005: truthful model configuration validation and advisory transport, no key/error echo or event-loop blocking. Required API/DB compatibility approval remains pending; physical devices and unavailable confinement are separate concrete acceptance blockers.
