# Rollback Ledger

2026-10-23安装验收：测试前无安装，测试后卸载4378c6e测试包；原数据6文件全部SHA-256还原且备份保留。最终后续提交仅证据文件，回滚仅revert文档，不重复迁移或改写原账本。自动审批拦截清理，未宣称删除或空间回收。

2026-10-23 旧账本兼容：基准c03cfd7，仅补三种历史EventType及负向测试，无schema迁移修改、历史重签或原库写入。回滚仅revert本次代码提交；已验证升级副本和源库备份保留。

2026-10-23 Windows本机修复：基准add0775。仅回退本轮providers与验收/测试代码即可；无schema、全局Codex配置、旧库或权限持久变更。原始失败/成功证据均保留。

2026-10-23 CI证据接续：基准 `40693d7`，仅更新最终CI核验与发布/交接记录，无产品、配置、协议、数据库和运行服务变更。回滚仅 revert 本次文档提交，不撤销已验证产品代码、不 reset/clean。

2026-10-23 ASG-TEST-004 远程 CI 推进：用户已明确允许推送 `fix/codex-empty-store`，仍不合并或发布。`4011655` 已推送；Core run `39912640138` 在调度前失败（无 jobs），actionlint 复现 job env 不允许 runner 上下文。将 PLAYWRIGHT_MODULE 原值移到真实验收步骤的 env，actionlint 由 exit1 转为 exit0；产品、协议、权限及依赖不变。设计及独立复核通过，完整后端 1375 passed / 26 skipped（JUnit 1401 项，88.98 秒）；新 SHA 远程验收另行记录，不将配置校验当成 CI 通过。回滚仅 revert 此配置修复，基准 `4011655`。

2026-10-23 真实Codex验收推进：`fix/codex-empty-store` 基于`8e91251`。当前登录复用获用户明确授权；真实尝试定位并修复“安全根目录无历史却被判身份改变”，inspect返回EMPTY、库存为空且revision稳定。缺失/替换/不安全路径仍拒绝；无auth读取或目录创建。关联56passed/2skipped，最终完整1375passed/26skipped（JUnit1401项）；真实重跑已过selection，但Unix provider sandbox proof未验证而拒绝连接，尚未进入thread/model，保持安全边界。Windows真实运行/安装升级权限恢复、准确HEAD远程CI、真机交互仍是4组门槛。

2026-10-23 发布推进：本地桌面smoke假绿修复，20项真实PowerShell控制流检查通过；完整后端1369 passed/26 skipped（JUnit1395项核对）。身份仅PID+出生时间确认后清理；未知/重用进程不杀，健康后主进程必须存活。Windows安装后实际调用已接CI但未远程运行。真实Codex已获当前登录复用授权，首次会话创建失败EXECUTION_STORE_IDENTITY_CHANGED，继续定位而非完成。剩余4组门槛详见发布基准。

2026-10-23 恢复进程强杀证据：`fix/recovery-process-crash` 基于 `8d9a0a7`。完整后端 1349 passed、26 skipped（JUnit 1375 项核对）；6 项 Linux 真实子进程终止检查已通过：四中断点补偿、现有 runtime 启动恢复、外部编辑保留 NEEDS_ATTENTION；重复请求/对账不新增操作或步骤事件。产品/前端/schema 未改，前端244与真实UI22项沿用明确标注的8d9a0a7基线。Windows轻量门禁已接入Tauri构建依赖，Linux保留JUnit；未推送或远程执行。不是打包Core/Tauri强杀或断电验收。回滚点 8d9a0a766388b3554930a32bce729dc8f68df2be，只 revert 此独立验收提交，无生产/schema变更。当前边界见 [发布基准](RELEASE_READINESS.md)。

2026-10-23 文件恢复界面：`feat/work-file-recovery` 基于 `4feb030`。Work → 更多操作 → 文件恢复，展示全部已保护目录，版本/文件分页、纯文本差异、明确确认恢复与预览撤销均接通现有 API；不改后端协议或数据库结构。完整后端 1343 passed、26 skipped（JUnit 1369 项已核对）；244 前端测试、正式/demo 构建通过；真实 Core/Chromium 总报告 22 项通过，无模型调用。未知回执复用原请求并按 request_id 核对，后台暂停轮询，旧成功不会误作新提交结果。新目录注册、策略配置、Windows/真实提供方与远程 CI 仍不在本轮验收范围。回滚点 4feb030775decda9ab8ad9e5514fade42e77c6de；只 revert 本轮独立提交，无 reset/clean 或迁移。详见 [发布基准](RELEASE_READINESS.md) 和 [阶段契约](phases/11-work-file-recovery.md)。

2026-10-23 本地发布闭环：`feat/release-closure` 基于 `03f4bbc`。真实浏览器连接实际 Core/SQLite，手工工作交付与文件恢复/撤销/过期预览拒绝、Core 重启后重新打开通过（12 项总检查；无模型调用）。修复真实提供方脚本忽略部分失败检查仍给 PASS 的问题；补齐严格判定与中断报告回归。最终完整后端 1341 passed、26 skipped（JUnit 核对）；定向 41 项通过。CI 新门禁已接线但未推送执行。Windows 提供方、安装与异常恢复仍待验收。最新边界以 [RELEASE_READINESS.md](RELEASE_READINESS.md) 为准；下方记录为历史。

2026-10-22 WS-007/008 bilingual UI: start `61b1bf1`, branch `feat/frontend-bilingual`; scope limited to frontend, its browser scripts, shipped static assets and verification docs. Fresh checks: 213 frontend tests plus production/demo builds and fixture browser flows passed. Rollback is a normal revert of the eventual isolated local feature commit, preserving unrelated work; no reset/clean or backend/schema changes are needed. No remote push or merge is part of this change.

| Phase | Start Commit | Time | Test Status | Allowed Paths | Rollback Command | Untracked |
|-------|-------------|------|-------------|---------------|------------------|-----------|
| 00 | 9043c07 | 2026-07-23T16:09:15Z | 107 passed | agentguard/, config/, docs/, tests/, .github/ | git reset --hard pre-v1-gui | None |
| DL-AUDIT | 866a9ef + existing dirty worktree | 2026-07-27 | 281 Python passed; 117 Device Link passed; Android JVM 11 passed; compileall/diff check passed | agentguard/device_link/, agentguard/api/server.py, android/.../DeviceLinkClient.kt, Device Link tests/docs | revert only listed Device Link hunks; preserve unrelated worktree changes | existing unrelated changes retained |
| R4-P7 | 60204c233136c5bcf58dfb45c5a477b978a987f8 | 2026-08-07 | 753 pytest passed; Ruff; wheel build; clean non-editable wheel CLI smoke | P7 recovery, supervision, migration, CLI, tests, phase/docs records | revert only the P7 atomic commit after recording its SHA; never reset/clean/stash or access the frozen workspace | ignored retry-guard runtime state only |

## 2026-10-23 ASG-ONBOARD-001

Base0661c24. Revert this feature's UI/API client, focused tests, acceptance scripts and generated frontend assets together. No backend/database/schema change, original data directory mutation, permission change or credential copy. Newly registered disposable test scopes live only in removed harness temporary databases; real user directory policy is never changed by this verification.

## 2026-10-23 ASG-CLOSE-001 spatial UI subset

Rollback1f66480. Revert spatial UI/components/appearancehelper, viewer and generated static assets together. No database/API mutation in this subset. Pins are transient; previously saved work layout/requirements/links unaffected. Tests and actual browser reports retained. Color persistence and queue storage remain blocked on required architecture answer, not enabled implicitly.
