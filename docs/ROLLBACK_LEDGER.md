# Rollback Ledger

| Phase | Start Commit | Time | Test Status | Allowed Paths | Rollback Command | Untracked |
|-------|-------------|------|-------------|---------------|------------------|-----------|
| 00 | 9043c07 | 2026-07-23T16:09:15Z | 107 passed | agentguard/, config/, docs/, tests/, .github/ | git reset --hard pre-v1-gui | None |
| DL-AUDIT | 866a9ef + existing dirty worktree | 2026-07-27 | 281 Python passed; 117 Device Link passed; Android JVM 11 passed; compileall/diff check passed | agentguard/device_link/, agentguard/api/server.py, android/.../DeviceLinkClient.kt, Device Link tests/docs | revert only listed Device Link hunks; preserve unrelated worktree changes | existing unrelated changes retained |
