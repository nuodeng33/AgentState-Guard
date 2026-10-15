# Acceptance Matrix — AgentState Guard v1.0

The original rows below are historical evidence. Current local Work UI slice
verification is recorded in [WORKSPACE_UI_VERIFICATION.md](../WORKSPACE_UI_VERIFICATION.md)
(2026-09-29), with implementation, automated checks and the outstanding visual
acceptance boundary kept separate. This does not mark the full product accepted.

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
| DL-AUDIT-001 | VERIFIED_LOCALLY | device_link + FastAPI + Android dev transport | 117 targeted / 281 full | production FastAPI + Android JVM 11 | — | 2026-07-27 stable-baseline log | pending | Stable development baseline only; TLS/pinning, dual SAS, mutual challenge auth, full contract migration remain |
| ASG-TEST-001 | PARTIAL | v0.2 | 107 tests | — | — | pytest | pre-v1-gui | Needs more |
| ASG-TEST-002 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-TEST-003 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-TEST-004 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-REL-001 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-REL-002 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-REL-003 | NOT_STARTED | — | — | — | — | — | — | — |
| ASG-REL-004 | NOT_STARTED | — | — | — | — | — | — | — |
| R4-P7-001 | VERIFIED_LOCALLY | recovery/service.py, supervision/service.py, migrations.py | recovery drill/baseline, migration, supervision | CLI smoke | wheel install smoke | 753 pytest; Ruff; wheel build | pending | CI audit and normal push pending at this documentation point |
| ASG-PROT-20260929-01 | VERIFIED_LOCALLY | protection service, schema 16, domain adapters | Full Python regression: 1135 passed, 8 skipped | Local API and Work links | Windows, Docker bind/volume/layer, Ubuntu WSL: 12 live passed | [Dated acceptance record](../CORE_PROTECTION_ACCEPTANCE_20260929.md), [regression XML](../../reports/core-protection-20260929/optimization-regression-final.xml), [live XML](../../reports/core-protection-20260929/optimization-live-final.xml) | pending | Backend only; frontend not connected; no instantaneous or tree-wide atomic history |
| ASG-PROT-20260929-02 | VERIFIED_LOCALLY | migration 15, preview/operation ledger, legacy restore closure | Fault, recovery and compatibility tests | 39 compatibility tests passed | Existing database backup copy 14 to 15 | [Migration evidence](../../reports/core-protection-20260929/migration-acceptance.json), [compatibility XML](../../reports/core-protection-20260929/compatibility.xml) | pending | Original local 8787 upgraded with two Works preserved; Docker ping and six crash/race scenarios verified; frontend remains pending |
| ASG-PROT-20260929-03 | PARTIAL | read-only provider session metadata | Fixed-format fixture tests | Real Kimi 50 sessions/50 cwd; WSL absent stores UNKNOWN | — | [Dated acceptance record](../CORE_PROTECTION_ACCEPTANCE_20260929.md) | pending | WSL positive store only tested with temporary fixture; no Agent execution or orchestration |
| ASG-PROT-20260929-04 | VERIFIED_LOCALLY | migration 16, history/runtime modules, logical quota | Summary, metadata pressure, retention, rollback and no-progress retry regressions | Default full responses plus optional summary/pagination | Real 10,000-file directory with three versions; 8787 upgraded 15 to 16 | [Scale evidence](../../reports/core-protection-20260929/optimization-scale.json), [Upgrade evidence](../../reports/core-protection-20260929/optimization-live-upgrade.json) | pending | Two Works and associations preserved; immutable journals may require recording to pause at quota; logical quota is not a total disk cap |
