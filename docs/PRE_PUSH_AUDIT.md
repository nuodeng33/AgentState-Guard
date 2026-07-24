# Pre-Push Audit — AgentState Guard 0.9.0.dev0

## 1. Test Count

**Current: 139 tests** (not 140)
- Previous claim of 140 was a counting error
- 107 baseline + 32 new = 139
- No tests deleted to pass; 139 is the real count

| File | Tests |
|------|-------|
| test_audit.py | 5 |
| test_blob.py | 8 |
| test_doctor.py | 5 |
| test_hasher.py | 12 |
| test_integration.py | 24 |
| test_migrations.py | 7 |
| test_report.py | 5 |
| test_restore.py | 9 |
| test_runner.py | 12 |
| test_sanitizer.py | 13 |
| test_snapshot.py | 14 |
| test_transactions.py | 12 |
| test_whitelist.py | 13 |
| **Total** | **139** |

## 2. Capability Matrix Count

- **22** top-level CLI commands
- + **3** subcommands (transaction show, transaction undo, host-probe)
- = **25** entries in matrix
- + **4** NOT_IMPLEMENTED (watch, export, import, run) also listed
- = **26** total matrix rows
- Active: **22 commands**

## 3. Full Verification Results

| Check | Status |
|-------|--------|
| Python tests (139) | ✅ All pass |
| Frontend typecheck | ✅ Exit 0 |
| Frontend build | ✅ 583ms, web_static/ updated |
| Wheel build | ✅ dist/agentstate_guard-0.9.0.dev0 |
| Fresh venv install | ✅ agentguard --help, doctor work |
| GUI HTTP smoke | ✅ Homepage (200), Health (200), JS (200) |
| Restore roundtrip | ✅ Verified in previous queue |
| Secret leak scan | ✅ 0 leaks in git, docs, web_static |

## 4. Git File Audit

| Check | Result |
|-------|--------|
| .env in git | ❌ 0 files |
| .agentguard/ in git | ❌ 0 files |
| *.db in git | ❌ 0 files |
| node_modules in git | ❌ 0 files |
| .venv in git | ❌ 0 files |
| reports/ in git | ❌ 0 files |
| .gitignore present | ✅ (16 patterns) |
| Total files tracked | 120 |
| Largest file | web_static/assets/*.js (146KB) |

## 5. README Disclaimers

- ✅ "Development Preview" stated
- ✅ Version 0.9.0.dev0 stated
- ✅ test-restore limitation stated
- ✅ export/import limitation stated
- ✅ Browser E2E / CI awaiting GitHub Actions stated
- ✅ Default bind 127.0.0.1 stated
- ✅ Not recommended for public exposure stated

## 6. Summary

- All gating checks pass
- No dangerous files in git
- README disclaimers accurate
- Version correctly set to 0.9.0.dev0
- Ready for GitHub push (when token is available)
