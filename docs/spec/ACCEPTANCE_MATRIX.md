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

## Device Link P0-A containment

| ID | Status | Impl | Unit | Int | E2E | Evidence | Commit | Limitation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P0A-CRYPTO-001 | VERIFIED_LOCALLY | device_link/crypto.py | DER/P-256 vectors | FastAPI key validation | Not physical | 107 targeted tests | 7c4277d, 20331bb | Android not verified |
| P0A-PAIR-001..003 | VERIFIED_LOCALLY | device_link/pairing.py, gateway.py | FSM/concurrency | Production router | Not physical | Terminal/capacity/identity tests | 7c4277d, 20331bb | Existing SAS design unchanged |
| P0A-AUTH-001..003 | VERIFIED_LOCALLY | device_link/gateway.py | token/challenge tests | Production router | Not physical | expiry/revoke/replay/concurrency | 7c4277d, 20331bb | In-memory only |
| P0A-HTTP-001..004 | VERIFIED_LOCALLY | api/server.py, device_link/models.py | boundary/model tests | Production FastAPI | Not physical | exact status, namespace, origin, 1 MiB tests | 7c4277d, 20331bb | Loopback HTTP only |
| P0A-DOC-001 | VERIFIED_LOCALLY | docs/device-link/, CURRENT_STATE, handoff, rollback | N/A | N/A | N/A | independent verification complete | this closeout | Overall security remains PARTIAL |
