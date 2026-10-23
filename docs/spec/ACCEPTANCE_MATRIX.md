# Acceptance Matrix — AgentState Guard v1.0

2026-10-23 本机问题已定位修复：旧账本1648条错误来自校验器遗漏三种历史schema-v1事件类型，不是哈希损坏。补回PROCESS_STARTED/PROCESS_EXITED/HOST_OBSERVER_LIFECYCLE后，旧库备份及迁移副本原始哈希链均通过；副本schema10→19全部行保留、integrity=ok、ledger_errors=[]，未修改原库或重算历史哈希。新增回归确认未知类型、哈希篡改仍拒绝；本机完整1391通过/13跳过/0失败。已通过更窄的独立CLI启动验证下载的40693d7打包sidecar：独立数据目录、回环18787，健康/鉴权状态及版本/归属进程清理通过，无现有ASG数据访问。先前自动审批拒绝的是组合操作，本次独立启动实际获准；不再将sidecar记为不可运行。MSI本机升级、桌面主程序交互/异常业务恢复与真机仍待验收。详见 [问题闭环证据](../verification/WINDOWS_LEGACY_LEDGER_20261023.json)。下方旧失败记录保留为历史。

2026-10-23 本机接续：Windows完整回归1388通过/13跳过；真实Edge/Core22项与真实Codex执行/续接/取消/验收/恢复PASS，真实DACL拒绝及ACL还原PASS。修复当前session继承网络策略、验收SQLite连接释放、Windows venv worker PID与路径校验。旧库副本升级仍FAIL：迁移前后均1648条LEDGER_AUTHORITY_INVALID；原始行保留，未修改原库。打包程序启动遭自动审批拒绝，本机MSI/升级与打包异常业务恢复未验收。详见 [本机验证](../WINDOWS_NATIVE_VERIFICATION_20261023.md)。

2026-10-23 接续最终核验：验收产品代码固定为 `40693d78d5e5f7bd9ff046bee72da1e5c1ddea3f`，分支 `fix/codex-empty-store`。实际 Core run `39913027492` 与 Desktop Windows run `39913027509` 均已终态 SUCCESS；Windows 包含 recovery-process、frontend/sidecar/Tauri 构建、独立与打包 sidecar、MSI 新装→打包主程序到 Core→严格桌面检查→清理→卸载及残留检查。实际下载的三份远端 JUnit 经项目解析器分别核对为 1375 passed / 26 skipped（各1401项，不跨运行累加）；真实 Linux UI 报告22项均严格为true。产物ZIP按GitHub digest核验，EXE/安装包格式与逐文件哈希另行核对。详见 [最终CI核验](../verification/CI_40693d7_20261023.json)；未改产品代码、未调用模型、未合并或发布。旧版升级、真实DACL拒绝、打包程序异常后的业务恢复、Windows真实Codex闭环、桌面/手机真机仍未验收。以下记录为历史。

2026-10-23 ASG-TEST-004 远程 CI 推进：用户已明确允许推送 `fix/codex-empty-store`，仍不合并或发布。`4011655` 已推送；Core run `39912640138` 在调度前失败（无 jobs），actionlint 复现 job env 不允许 runner 上下文。将 PLAYWRIGHT_MODULE 原值移到真实验收步骤的 env，actionlint 由 exit1 转为 exit0；产品、协议、权限及依赖不变。设计及独立复核通过，完整后端 1375 passed / 26 skipped（JUnit 1401 项，88.98 秒）；新 SHA 远程验收另行记录，不将配置校验当成 CI 通过。回滚仅 revert 此配置修复，基准 `4011655`。

2026-10-23 真实Codex验收推进：`fix/codex-empty-store` 基于`8e91251`。当前登录复用获用户明确授权；真实尝试定位并修复“安全根目录无历史却被判身份改变”，inspect返回EMPTY、库存为空且revision稳定。缺失/替换/不安全路径仍拒绝；无auth读取或目录创建。关联56passed/2skipped，最终完整1375passed/26skipped（JUnit1401项）；真实重跑已过selection，但Unix provider sandbox proof未验证而拒绝连接，尚未进入thread/model，保持安全边界。Windows真实运行/安装升级权限恢复、准确HEAD远程CI、真机交互仍是4组门槛。

2026-10-23 发布推进：本地桌面smoke假绿修复，20项真实PowerShell控制流检查通过；完整后端1369 passed/26 skipped（JUnit1395项核对）。身份仅PID+出生时间确认后清理；未知/重用进程不杀，健康后主进程必须存活。Windows安装后实际调用已接CI但未远程运行。真实Codex已获当前登录复用授权，首次会话创建失败EXECUTION_STORE_IDENTITY_CHANGED，继续定位而非完成。剩余4组门槛详见发布基准。

2026-10-23 恢复进程强杀证据：`fix/recovery-process-crash` 基于 `8d9a0a7`。完整后端 1349 passed、26 skipped（JUnit 1375 项核对）；6 项 Linux 真实子进程终止检查已通过：四中断点补偿、现有 runtime 启动恢复、外部编辑保留 NEEDS_ATTENTION；重复请求/对账不新增操作或步骤事件。产品/前端/schema 未改，前端244与真实UI22项沿用明确标注的8d9a0a7基线。Windows轻量门禁已接入Tauri构建依赖，Linux保留JUnit；未推送或远程执行。不是打包Core/Tauri强杀或断电验收。当前边界见 [发布基准](../RELEASE_READINESS.md)。

ASG-CORE-003/004、ASG-TEST-001/004：新增真实OS终止后的持久恢复证据；Windows/桌面/断电保持未验状态。

2026-10-23 文件恢复界面：`feat/work-file-recovery` 基于 `4feb030`。Work → 更多操作 → 文件恢复，展示全部已保护目录，版本/文件分页、纯文本差异、明确确认恢复与预览撤销均接通现有 API；不改后端协议或数据库结构。完整后端 1343 passed、26 skipped（JUnit 1369 项已核对）；244 前端测试、正式/demo 构建通过；真实 Core/Chromium 总报告 22 项通过，无模型调用。未知回执复用原请求并按 request_id 核对，后台暂停轮询，旧成功不会误作新提交结果。新目录注册、策略配置、Windows/真实提供方与远程 CI 仍不在本轮验收范围。详见 [发布基准](../RELEASE_READINESS.md) 和 [阶段契约](../phases/11-work-file-recovery.md)。

ASG-GUI-004/005、ASG-TEST-002/003 与 WS-007/008：本地原生目录恢复 UI 已有组件及真实浏览器证据；不将历史整产品矩阵或 Windows 状态改为全部 VERIFIED。

2026-10-23 本地发布闭环：`feat/release-closure` 基于 `03f4bbc`。真实浏览器连接实际 Core/SQLite，手工工作交付与文件恢复/撤销/过期预览拒绝、Core 重启后重新打开通过（12 项总检查；无模型调用）。修复真实提供方脚本忽略部分失败检查仍给 PASS 的问题；补齐严格判定与中断报告回归。最终完整后端 1341 passed、26 skipped（JUnit 核对）；定向 41 项通过。CI 新门禁已接线但未推送执行。Windows 提供方、安装与异常恢复仍待验收。最新边界以 [RELEASE_READINESS.md](../RELEASE_READINESS.md) 为准；下方记录为历史。

ASG-TEST-003/004：新增真实 Core 浏览器与原生恢复验收脚本以及 CI local-workflow 门禁；本地已执行，远程 CI 尚未执行，不将全产品状态上调为 VERIFIED。WS-007/008 的真实手工工作路径已新增证据。

2026-10-22 WS-007/008 与 ASG-TEST-002 前端后续：中英文 Work/顶栏偏好切换、数量/可访问名称/时间显示及 Markdown 分隔行安全回退已实现；213 前端测试、正式/demo 构建、10 项双语及 8 项反馈浏览器夹具通过。证据与限制见 [FRONTEND_REDESIGN.md](../FRONTEND_REDESIGN.md)；不是后端或真实执行域新增验收。

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
