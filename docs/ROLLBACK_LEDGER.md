# Rollback Ledger

2026-10-23 恢复进程强杀证据：`fix/recovery-process-crash` 基于 `8d9a0a7`。完整后端 1349 passed、26 skipped（JUnit 1375 项核对）；6 项 Linux 真实子进程终止检查已通过：四中断点补偿、现有 runtime 启动恢复、外部编辑保留 NEEDS_ATTENTION；重复请求/对账不新增操作或步骤事件。产品/前端/schema 未改，前端244与真实UI22项沿用明确标注的8d9a0a7基线。Windows轻量门禁已接入Tauri构建依赖，Linux保留JUnit；未推送或远程执行。不是打包Core/Tauri强杀或断电验收。回滚点 8d9a0a766388b3554930a32bce729dc8f68df2be，只 revert 此独立验收提交，无生产/schema变更。当前边界见 [发布基准](RELEASE_READINESS.md)。

2026-10-23 文件恢复界面：`feat/work-file-recovery` 基于 `4feb030`。Work → 更多操作 → 文件恢复，展示全部已保护目录，版本/文件分页、纯文本差异、明确确认恢复与预览撤销均接通现有 API；不改后端协议或数据库结构。完整后端 1343 passed、26 skipped（JUnit 1369 项已核对）；244 前端测试、正式/demo 构建通过；真实 Core/Chromium 总报告 22 项通过，无模型调用。未知回执复用原请求并按 request_id 核对，后台暂停轮询，旧成功不会误作新提交结果。新目录注册、策略配置、Windows/真实提供方与远程 CI 仍不在本轮验收范围。回滚点 4feb030775decda9ab8ad9e5514fade42e77c6de；只 revert 本轮独立提交，无 reset/clean 或迁移。详见 [发布基准](RELEASE_READINESS.md) 和 [阶段契约](phases/11-work-file-recovery.md)。

2026-10-23 本地发布闭环：`feat/release-closure` 基于 `03f4bbc`。真实浏览器连接实际 Core/SQLite，手工工作交付与文件恢复/撤销/过期预览拒绝、Core 重启后重新打开通过（12 项总检查；无模型调用）。修复真实提供方脚本忽略部分失败检查仍给 PASS 的问题；补齐严格判定与中断报告回归。最终完整后端 1341 passed、26 skipped（JUnit 核对）；定向 41 项通过。CI 新门禁已接线但未推送执行。Windows 提供方、安装与异常恢复仍待验收。最新边界以 [RELEASE_READINESS.md](RELEASE_READINESS.md) 为准；下方记录为历史。

2026-10-22 WS-007/008 bilingual UI: start `61b1bf1`, branch `feat/frontend-bilingual`; scope limited to frontend, its browser scripts, shipped static assets and verification docs. Fresh checks: 213 frontend tests plus production/demo builds and fixture browser flows passed. Rollback is a normal revert of the eventual isolated local feature commit, preserving unrelated work; no reset/clean or backend/schema changes are needed. No remote push or merge is part of this change.

| Phase | Start Commit | Time | Test Status | Allowed Paths | Rollback Command | Untracked |
|-------|-------------|------|-------------|---------------|------------------|-----------|
| 00 | 9043c07 | 2026-07-23T16:09:15Z | 107 passed | agentguard/, config/, docs/, tests/, .github/ | git reset --hard pre-v1-gui | None |
| DL-AUDIT | 866a9ef + existing dirty worktree | 2026-07-27 | 281 Python passed; 117 Device Link passed; Android JVM 11 passed; compileall/diff check passed | agentguard/device_link/, agentguard/api/server.py, android/.../DeviceLinkClient.kt, Device Link tests/docs | revert only listed Device Link hunks; preserve unrelated worktree changes | existing unrelated changes retained |
| R4-P7 | 60204c233136c5bcf58dfb45c5a477b978a987f8 | 2026-08-07 | 753 pytest passed; Ruff; wheel build; clean non-editable wheel CLI smoke | P7 recovery, supervision, migration, CLI, tests, phase/docs records | revert only the P7 atomic commit after recording its SHA; never reset/clean/stash or access the frozen workspace | ignored retry-guard runtime state only |
