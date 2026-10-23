# Rollback Ledger

2026-10-23 本地发布闭环：`feat/release-closure` 基于 `03f4bbc`。真实浏览器连接实际 Core/SQLite，手工工作交付与文件恢复/撤销/过期预览拒绝、Core 重启后重新打开通过（12 项总检查；无模型调用）。修复真实提供方脚本忽略部分失败检查仍给 PASS 的问题；补齐严格判定与中断报告回归。最终完整后端 1341 passed、26 skipped（JUnit 核对）；定向 41 项通过。CI 新门禁已接线但未推送执行。Windows 提供方、安装与异常恢复仍待验收。最新边界以 [RELEASE_READINESS.md](RELEASE_READINESS.md) 为准；下方记录为历史。

2026-10-22 WS-007/008 bilingual UI: start `61b1bf1`, branch `feat/frontend-bilingual`; scope limited to frontend, its browser scripts, shipped static assets and verification docs. Fresh checks: 213 frontend tests plus production/demo builds and fixture browser flows passed. Rollback is a normal revert of the eventual isolated local feature commit, preserving unrelated work; no reset/clean or backend/schema changes are needed. No remote push or merge is part of this change.

| Phase | Start Commit | Time | Test Status | Allowed Paths | Rollback Command | Untracked |
|-------|-------------|------|-------------|---------------|------------------|-----------|
| 00 | 9043c07 | 2026-07-23T16:09:15Z | 107 passed | agentguard/, config/, docs/, tests/, .github/ | git reset --hard pre-v1-gui | None |
| DL-AUDIT | 866a9ef + existing dirty worktree | 2026-07-27 | 281 Python passed; 117 Device Link passed; Android JVM 11 passed; compileall/diff check passed | agentguard/device_link/, agentguard/api/server.py, android/.../DeviceLinkClient.kt, Device Link tests/docs | revert only listed Device Link hunks; preserve unrelated worktree changes | existing unrelated changes retained |
| R4-P7 | 60204c233136c5bcf58dfb45c5a477b978a987f8 | 2026-08-07 | 753 pytest passed; Ruff; wheel build; clean non-editable wheel CLI smoke | P7 recovery, supervision, migration, CLI, tests, phase/docs records | revert only the P7 atomic commit after recording its SHA; never reset/clean/stash or access the frozen workspace | ignored retry-guard runtime state only |
