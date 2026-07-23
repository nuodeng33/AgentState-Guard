# Acceptance Matrix — AgentState Guard v1.0

| ID | Status | Impl | Unit | Int | E2E | Evidence | Commit | Limitation |
|----|--------|------|------|-----|-----|----------|--------|------------|
| ASG-CORE-001 | VERIFIED | migrations.py | test_migrations | — | — | 6 tests, forward+rollback | pending | — |
| ASG-CORE-002 | VERIFIED | blob.py | test_blob | — | — | 7 tests, put/get/dedup/verify/GC | pending | Not integrated into snapshots yet |
| ASG-CORE-003 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CORE-004 | IMPLEMENTED | storage/gc.py | — | — | — | Plan + execute dry-run | pending | Needs integration |
| ASG-CORE-005 | VERIFIED | audit.py | test_audit | — | — | Tampering detection verified | pending | — |
| ASG-CLI-001 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CLI-002 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CLI-003 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CLI-004 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CLI-005 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CLI-006 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CLI-007 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CLI-008 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CLI-009 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CLI-010 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-CLI-011 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-GUI-001 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-GUI-002 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-GUI-003 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-GUI-004 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-GUI-005 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-GUI-006 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-GUI-007 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-GUI-008 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-SEC-001 | VERIFIED | v0.2 | test_doctor | — | — | container check | pre-v1-gui | Only container heuristic |
| ASG-SEC-002 | IMPLEMENTED | v0.2 | test_sanitizer | — | — | sanitizer module | pre-v1-gui | Needs more output coverage |
| ASG-SEC-003 | IMPLEMENTED | v0.2 | test_whitelist | test_restore | — | whitelist module | pre-v1-gui | Traversal tested |
| ASG-SEC-004 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-SEC-005 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-TEST-001 | PARTIAL | v0.2 | 107 tests | — | — | pytest | pre-v1-gui | Needs more |
| ASG-TEST-002 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-TEST-003 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-TEST-004 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-REL-001 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-REL-002 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-REL-003 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-REL-004 | NOT_STARTED | — | — | — | — | — | — | — |
