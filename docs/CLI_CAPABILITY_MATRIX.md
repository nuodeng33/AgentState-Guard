# CLI Command Capability Matrix — AgentState Guard 0.9.0-dev

| Command | Status | Real Impl | Unit Test | Int Test | E2E | Notes |
|---------|--------|-----------|-----------|----------|-----|-------|
| status | COMPLETE | ✅ | ✅ | — | — | Real diagnostics |
| doctor | COMPLETE | ✅ | ✅ | ✅ | — | PASS/WARN/FAIL/SKIP/UNREACHABLE |
| checkpoint | COMPLETE | ✅ | — | ✅ | — | Creates real snapshots |
| checkpoints | COMPLETE | ✅ | — | ✅ | — | Lists from DB |
| diff | COMPLETE | ✅ | — | ✅ | — | Hash comparison |
| restore | COMPLETE | ✅ | ✅ | ✅ | — | Atomic restore from blob |
| report | COMPLETE | ✅ | — | ✅ | — | MD + JSON output |
| update-state | COMPLETE | ✅ | — | — | — | Docs from structured data |
| plan | PARTIAL | ✅ | ✅ | ✅ | — | Creates txn, no step detail yet |
| transactions | PARTIAL | ✅ | ✅ | ✅ | — | Lists from DB |
| transaction show | PARTIAL | ✅ | ✅ | ✅ | — | Shows txn with steps |
| undo | PARTIAL | ✅ | — | — | — | Needs steps populated |
| verify | PARTIAL | ✅ | — | — | — | DB integrity only |
| test-restore | SHELL_ONLY | — | — | — | — | Only parses args |
| gc | PARTIAL | ✅ | — | — | — | Dry-run works, execute pending |
| handoff | PARTIAL | ✅ | — | — | — | Generates docs |
| incident | PARTIAL | ✅ | — | — | — | Generates bundle |
| host-import | COMPLETE | ✅ | — | — | — | Sanitized stdin import |
| host-probe | SHELL_ONLY | — | — | — | — | Alias for host-import |
| ui | PARTIAL | ✅ | — | — | — | Serves API, needs frontend static |
| serve | PARTIAL | ✅ | — | — | — | Same as ui with --allow-remote |

## Status Definitions
| Status | Meaning |
|--------|---------|
| COMPLETE | Full implementation with tests |
| PARTIAL | Impl exists, missing tests or edge cases |
| SHELL_ONLY | Only CLI arg parsing, no real implementation |
| MISSING | Not implemented at all |

## Actions Needed
- Remove shell-only commands or implement fully
- Add integration tests for partial commands
- Complete frontend static serving in `ui` command
