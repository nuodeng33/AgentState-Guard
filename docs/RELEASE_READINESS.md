# Release Readiness — AgentState Guard 0.9.0.dev0

## Verdict: NOT READY for v1.0.0 RC

**Version: 0.9.0.dev0 (Backend Preview)**

## Prerequisites Status

| Prerequisite | Status | Notes |
|-------------|--------|-------|
| All Python tests pass | ✅ | 139/139 |
| Frontend npm ci + typecheck + build | ✅ | Clean build, 0 vulns |
| Wheel contains GUI | ✅ | web_static/ included |
| Fresh venv wheel install | ✅ | CLI + GUI both work |
| CLI smoke test | ✅ | 22 commands |
| GUI HTTP smoke | ✅ | All endpoints verified |
| Restore real roundtrip | ⚠️ PARTIAL | Only tracked files |
| Undo real roundtrip | ⚠️ PARTIAL | Needs populated steps |
| Verify real | ✅ | DB integrity works |
| Incident + Handoff | ✅ | Produces correct output |
| GC dry-run | ✅ | Plan generation works |
| Secret leak scan | ✅ | No leaks found |
| DB migration + rollback | ✅ | Forward + rollback tested |
| Web security | ⚠️ PARTIAL | Auth works, no full pentest |
| Acceptance Matrix accuracy | ⚠️ PARTIAL | Needs final review |
| README accuracy | ⚠️ Needs update | Must match capability matrix |
| Playwright E2E | ❌ BLOCKED | No browser binaries |
| GitHub Actions CI | ❌ WAITING | Not yet pushed |

## Required Before RC
1. Playwright E2E in working environment
2. GitHub Actions real execution
3. export/import functional (or explicitly deferred to v1.1)
4. test-restore real implementation (or removed)
5. Full Web security test suite
