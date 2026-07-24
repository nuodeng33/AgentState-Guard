# Capability Matrix — AgentState Guard 0.9.0.dev0

| Command | Req ID | File | Status | Unit | Int | E2E | Real Evidence | Limitation | README Claim |
|---------|--------|------|--------|------|-----|-----|---------------|-------------|-------------|
| status | — | commands/status.py | VERIFIED_REAL | ✅ | — | — | Real diagnostics | No E2E | ✅ OK |
| doctor | — | commands/doctor.py | VERIFIED_REAL | ✅ | ✅ | — | 6 status types | Container SKIP expected | ✅ OK |
| checkpoint | ASG-CORE-001 | commands/checkpoint.py | VERIFIED_TEMP_SANDBOX | — | ✅ | — | Creates real snapshots | Only tracked files | ✅ OK |
| checkpoints | — | storage/db.py | VERIFIED_REAL | — | ✅ | — | Lists from DB | — | ✅ OK |
| diff | ASG-CORE-002 | commands/diff.py | VERIFIED_TEMP_SANDBOX | — | ✅ | — | Hash comparison | V1 format fallback | ✅ OK |
| restore | ASG-CORE-002 | commands/restore.py | VERIFIED_TEMP_SANDBOX | ✅ | ✅ | — | Atomic restore from blob | Needs restorable snapshot | ✅ OK |
| plan | ASG-CORE-003 | transactions/engine.py | VERIFIED_TEMP_SANDBOX | ✅ | ✅ | — | Creates txn with coverage | No step detail | ✅ OK |
| transactions | ASG-CLI-001 | transactions/engine.py | VERIFIED_TEMP_SANDBOX | ✅ | ✅ | — | Lists from DB | — | ✅ OK |
| transaction show | ASG-CLI-001 | transactions/engine.py | VERIFIED_TEMP_SANDBOX | ✅ | ✅ | — | Shows txn with steps | Steps may be empty | ✅ OK |
| undo | ASG-CORE-003 | transactions/engine.py | VERIFIED_TEMP_SANDBOX | — | — | — | State machine tested | Needs populated steps | ⚠️ Partial |
| verify | ASG-CLI-003 | cli.py | PARTIAL | — | — | — | DB integrity only | No full integrity scan | ⚠️ Overstated |
| test-restore | ASG-CLI-004 | cli.py | STUB | — | — | — | Parses args, lists files | Does NOT restore to temp | ❌ Overstated |
| gc | ASG-CORE-004 | storage/gc.py | VERIFIED_TEMP_SANDBOX | — | — | — | Plan + dry-run works | Execute disabled | ✅ OK |
| handoff | ASG-CLI-007 | commands/handoff.py | VERIFIED_TEMP_SANDBOX | — | — | — | Generates MD+JSON | Budget is approximate | ✅ OK |
| incident | ASG-CLI-008 | commands/incident.py | VERIFIED_TEMP_SANDBOX | — | — | — | Generates bundle | No real log inclusion | ✅ OK |
| host-import | ASG-CLI-010 | commands/host_import.py | VERIFIED_REAL | — | — | — | Sanitized stdin import | Schema version validation | ✅ OK |
| host-probe | ASG-CLI-010 | cli.py | STUB | — | — | — | Alias for host-import | No shell scripts | ⚠️ Overstated |
| report | — | commands/report.py | VERIFIED_TEMP_SANDBOX | ✅ | — | — | MD+JSON output | Needs checkpoint data | ✅ OK |
| update-state | — | commands/update_state.py | IMPLEMENTED | — | — | — | Docs from structured data | No test | ⚠️ Overstated |
| ui | ASG-GUI-001 | api/server.py | VERIFIED_TEMP_SANDBOX | — | — | — | Serves API+static | Frontend build needed | ✅ OK |
| serve | ASG-GUI-001 | api/server.py | VERIFIED_TEMP_SANDBOX | — | — | — | Same as ui+remote | Requires --allow-remote | ✅ OK |
| watch | — | — | NOT_IMPLEMENTED | — | — | — | — | Entry removed | ❌ Remove from docs |
| run | — | — | NOT_IMPLEMENTED | — | — | — | — | Entry removed | ❌ Remove from docs |
| export | — | — | NOT_IMPLEMENTED | — | — | — | — | Entry removed | ❌ Remove from docs |
| import | — | — | NOT_IMPLEMENTED | — | — | — | — | Entry removed | ❌ Remove from docs |

## Status Definitions
| Status | Meaning |
|--------|---------|
| VERIFIED_REAL | Real environment execution verified |
| VERIFIED_TEMP_SANDBOX | Verified in temporary sandbox directory |
| VERIFIED_WITH_MOCKS | Verified with mocked external dependencies |
| PARTIAL | Real implementation exists but incomplete |
| IMPLEMENTED | Code exists but lacks verification |
| STUB | Only argument parsing, no real logic |
| NOT_IMPLEMENTED | No code exists |
